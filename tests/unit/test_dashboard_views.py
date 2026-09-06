from __future__ import annotations

import json
from pathlib import Path

import pytest

from waam_validator.dashboard import app
from waam_validator.dashboard.app import _layer_display_rows
from waam_validator.dashboard.data import DashboardDataError, JobRecord, RunRecord
from waam_validator.dashboard.replay_service import REPLAY_STATUS
from waam_validator.dashboard.views import (
    _config_inspection,
    _trajectory_inspection,
    robot_time_figure,
    shape_figure,
    shape_metrics_are_uniform,
    shape_variation_message,
)


def _component_by_id(component: object, component_id: str) -> object | None:
    if getattr(component, "id", None) == component_id:
        return component
    children = getattr(component, "children", None)
    if not isinstance(children, list | tuple):
        children = [children] if children is not None else []
    for child in children:
        found = _component_by_id(child, component_id)
        if found is not None:
            return found
    return None


def _result_payload(status: str = "PASS") -> dict[str, object]:
    return {
        "schema_version": "3.0",
        "status": status,
        "schedule": {"makespan_s": 100.0},
        "reach": {"passed": True, "robots": []},
        "collision": {
            "passed": True,
            "collision_event_count": 0,
            "arm_envelope": {
                "enabled": True,
                "passed": True,
                "event_count": 0,
                "minimum_safety_margin_mm": 100.0,
                "centerline_distance_at_worst_mm": 350.0,
                "required_distance_at_worst_mm": 250.0,
                "pair": [1, 2],
                "time_s": 2.0,
                "closest_points_xy_mm": [[0.0, 0.0], [350.0, 0.0]],
                "tcp_positions_xy_mm": [[0.0, 0.0], [350.0, 0.0], [0.0, 500.0]],
            },
            "tcp_radius": {
                "enabled": True,
                "passed": True,
                "event_count": 0,
                "minimum_distance_mm": 500.0,
                "required_distance_at_minimum_mm": 300.0,
                "pair": [1, 2],
                "time_s": 2.0,
            },
        },
        "shape": {
            "passed": True,
            "coverage": 1.0,
            "underfill_ratio": 0.0,
            "overfill_ratio": 0.0,
            "iou": 1.0,
            "failed_layer_count": 0,
            "evaluated_layer_count": 1,
            "target_volume_mm3": 100.0,
            "deposited_volume_mm3": 100.0,
        },
        "failure_reasons": [],
        "issues": [],
    }


def test_context_registry_keeps_independent_folders_alive(tmp_path: Path) -> None:
    registry = app._ContextRegistry()
    trajectory_a = object()
    trajectory_b = object()

    token_a = registry.add(tmp_path / "a", trajectory_a)  # type: ignore[arg-type]
    token_b = registry.add(tmp_path / "b", trajectory_b)  # type: ignore[arg-type]

    assert token_a != token_b
    assert registry.get(token_a) == (tmp_path / "a").resolve()
    assert registry.get(token_b) == (tmp_path / "b").resolve()
    assert registry.trajectories(token_a) is trajectory_a
    assert registry.trajectories(token_b) is trajectory_b


def test_context_registry_reuses_token_for_same_folder(tmp_path: Path) -> None:
    registry = app._ContextRegistry()
    first = object()
    refreshed = object()

    first_token = registry.add(tmp_path, first)  # type: ignore[arg-type]
    refreshed_token = registry.add(tmp_path, refreshed)  # type: ignore[arg-type]

    assert refreshed_token == first_token
    assert registry.trajectories(first_token) is refreshed


def test_old_result_schema_requires_revalidation(fixture_root: Path, tmp_path: Path) -> None:
    run_dir = tmp_path / "output" / "legacy"
    run_dir.mkdir(parents=True)
    run = RunRecord(run_dir, "PASS", {"schema_version": "2.0", "status": "PASS"}, "legacy")

    view = app._result_view("token", JobRecord("fixture", fixture_root / "collision_free"), run)

    assert "schema 3.0" in str(view)
    assert "Validation을 다시 실행" in str(view)


def test_arm_snapshot_preserves_mm_scale_without_expanding_data_range(
    fixture_root: Path,
) -> None:
    figure = app._arm_envelope_snapshot(_result_payload(), fixture_root / "collision_free")

    assert figure.layout.xaxis.constrain == "domain"
    assert figure.layout.yaxis.constrain == "domain"
    assert figure.layout.yaxis.scaleanchor == "x"
    assert figure.layout.xaxis.range is not None
    assert float(figure.layout.xaxis.range[1]) - float(figure.layout.xaxis.range[0]) < 4_000
    assert figure.layout.height == 960


def test_result_view_preserves_selected_tab(fixture_root: Path, tmp_path: Path) -> None:
    run = RunRecord(tmp_path, "PASS", _result_payload(), "now")
    view = app._result_view(
        "token",
        JobRecord("fixture", fixture_root / "collision_free"),
        run,
        selected_tab="shape",
    )

    tabs = _component_by_id(view, "result-tabs")
    assert tabs is not None
    assert getattr(tabs, "value", None) == "shape"


def test_result_view_separates_shape_and_artifacts_and_keeps_shape_expanded(
    fixture_root: Path, tmp_path: Path
) -> None:
    (tmp_path / "replay.html").write_text("<html></html>", encoding="utf-8")
    run = RunRecord(tmp_path, "PASS", _result_payload(), "now")
    view = app._result_view(
        "token",
        JobRecord("fixture", fixture_root / "collision_free"),
        run,
        selected_tab="artifacts",
    )

    tabs = _component_by_id(view, "result-tabs")
    assert tabs is not None
    assert getattr(tabs, "value", None) == "artifacts"
    labels = [str(getattr(tab, "label", "")) for tab in tabs.children]
    assert labels == ["판정 요약", "로봇·일정", "충돌 안전", "형상", "산출물"]

    rendered = str(view)
    assert "형상 판정 기준" in rendered
    assert "동일한 Layer 그래프 펼치기" not in rendered
    assert "형상 판정 기준 보기" not in rendered
    assert "artifact-tab-grid" in rendered
    assert "artifact-files-panel" in rendered
    shape_tab = str(tabs.children[3])
    artifacts_tab = str(tabs.children[4])
    assert "layer-table" in shape_tab
    assert "layer-table" not in artifacts_tab
    assert "replay-frame" not in shape_tab
    assert "replay-frame" in artifacts_tab


def test_one_corrupt_result_csv_does_not_discard_result(
    fixture_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_read_csv_records(_run_dir: Path, filename: str) -> list[dict[str, object]]:
        calls.append(filename)
        if filename == "collision_events.csv":
            raise DashboardDataError("collision CSV is corrupt")
        return []

    monkeypatch.setattr(app, "read_csv_records", fake_read_csv_records)
    run_dir = tmp_path / "output" / "2026-01-01_000000"
    run_dir.mkdir(parents=True)
    run = RunRecord(run_dir, "PASS", _result_payload(), "2026-01-01 00:00:00")

    rendered = str(
        app._result_view("token", JobRecord("fixture", fixture_root / "collision_free"), run)
    )

    assert calls == ["robot_metrics.csv", "collision_events.csv", "layer_metrics.csv"]
    assert "TCP Radius 안전거리" in rendered
    assert "RESULT_CSV_LOAD_ERROR" in rendered
    assert "collision CSV is corrupt" in rendered


def test_result_view_keeps_replay_failure_message_after_refresh(
    fixture_root: Path, tmp_path: Path
) -> None:
    run_dir = tmp_path / "output" / "2026-01-01_000000"
    (run_dir / REPLAY_STATUS).parent.mkdir(parents=True)
    (run_dir / REPLAY_STATUS).write_text(
        json.dumps(
            {
                "state": "FINISHED",
                "stage": "failed",
                "verdict": "ERROR",
                "message": "frame planning failed",
                "progress": 0.0,
            }
        ),
        encoding="utf-8",
    )
    run = RunRecord(run_dir, "PASS", _result_payload(), "2026-01-01 00:00:00")

    rendered = str(
        app._result_view("token", JobRecord("fixture", fixture_root / "collision_free"), run)
    )

    assert "Replay 생성 실패" in rendered
    assert "frame planning failed" in rendered


def test_robot_time_figure_uses_global_makespan_and_inactive_time() -> None:
    figure = robot_time_figure(
        [
            {
                "robot_id": 1,
                "completion_s": 80.0,
                "deposition_time_s": 20.0,
                "travel_time_s": 10.0,
                "wait_time_s": 50.0,
                "inactive_after_completion_s": 20.0,
            },
            {
                "robot_id": 2,
                "completion_s": 100.0,
                "deposition_time_s": 25.0,
                "travel_time_s": 25.0,
                "wait_time_s": 50.0,
                "inactive_after_completion_s": 0.0,
            },
        ]
    )

    assert [trace.name for trace in figure.data] == [
        "Deposition · 적층",
        "Travel · 비적층 이동",
        "Wait · 위치 유지 대기",
        "완료 후 비활성",
    ]
    totals = [sum(float(trace.y[index]) for trace in figure.data) for index in range(2)]
    assert totals == pytest.approx([100.0, 100.0])
    assert list(figure.data[-1].y) == pytest.approx([20.0, 0.0])
    assert "Makespan: 100.00 s" in str(figure.layout.annotations[0].text)


def test_shape_variation_compares_each_metric_across_layers() -> None:
    rows = [
        {"coverage": 0.99, "underfill_ratio": 0.01, "overfill_ratio": 0.0, "iou": 0.99},
        {"coverage": 0.99, "underfill_ratio": 0.01, "overfill_ratio": 0.0, "iou": 0.99},
    ]
    assert "모든 Layer" in shape_variation_message(rows)
    rows[1]["iou"] = 0.98
    assert "1.0000%" in shape_variation_message(rows)


def test_layer_result_rows_and_shape_chart() -> None:
    source = [
        {
            "layer_index": 2,
            "z_slice_mm": 5.0,
            "coverage": 0.7,
            "underfill_ratio": 0.3,
            "overfill_ratio": 0.1,
            "iou": 0.7,
            "passed": False,
        }
    ]
    rows = _layer_display_rows(source)
    assert rows[0]["coverage"] == "70.0000%"
    assert rows[0]["passed"] == "FAIL"
    figure = shape_figure(source, {"minimum_layer_iou": 0.8})
    assert len(figure.layout.shapes) == 1
    assert float(figure.layout.shapes[0].y0) == 0.8
    assert any(trace.name == "실패 Layer" for trace in figure.data)


def test_shape_chart_preserves_undefined_overfill_ratio() -> None:
    source = [
        {
            "layer_index": 1,
            "coverage": 1.0,
            "underfill_ratio": 0.0,
            "overfill_ratio": 0.0,
            "iou": 1.0,
            "passed": True,
        },
        {
            "layer_index": 2,
            "coverage": 0.0,
            "underfill_ratio": 0.0,
            "overfill_ratio": None,
            "iou": 0.0,
            "passed": False,
        },
    ]

    figure = shape_figure(source, {"minimum_layer_iou": 0.8})
    overfill = next(trace for trace in figure.data if trace.name == "Overfill")
    assert list(overfill.y) == [0.0, None]
    assert not shape_metrics_are_uniform(source)
    assert "정의되지 않으며" in shape_variation_message(source)


def test_trajectory_makespan_displays_seconds_and_clock_units() -> None:
    rendered = str(
        _trajectory_inspection(
            {
                "row_count": 3,
                "makespan_s": 7200.0,
                "workload_imbalance_s": 0.0,
                "robots": [],
            }
        )
    )
    assert "7,200.00초" in rendered
    assert "2시간 00분 00초" in rendered


def test_config_view_distinguishes_base_home_and_capsule_radius() -> None:
    rendered = str(
        _config_inspection(
            {
                "simulation": {
                    "max_time_step_s": 0.1,
                    "max_tcp_step_mm": 5.0,
                    "event_merge_gap_s": 0.2,
                    "batch_size": 10_000,
                },
                "robots": [
                    {
                        "id": 1,
                        "base_xyz_mm": [-1400.0, 0.0, 0.0],
                        "home_xyz_mm": [-1000.0, 0.0, 1700.0],
                        "tcp_radius_mm": 100.0,
                        "arm_envelope_radius_mm": 100.0,
                        "reach_radius_mm": 2000.0,
                    }
                ],
                "process": {},
                "workspace": {},
                "collision": {"arm_clearance_mm": 50.0},
                "validation": {},
                "shape_validation": {},
            }
        )
    )
    assert "R1 Base" in rendered
    assert "R1 Home TCP" in rendered
    assert "R1 TCP / Arm 반경 / Reach" in rendered
    assert "충돌 이벤트 병합 최대 간격" in rendered


def test_server_grid_uses_dash_infinite_row_model_property() -> None:
    grid = app._grid(
        "paged-grid",
        [("ID", "event_id")],
        page_size=25,
        server_side=True,
    )

    assert grid.rowModelType == "infinite"
    assert grid.dashGridOptions["cacheBlockSize"] == 25
    assert grid.dashGridOptions["paginationPageSizeSelector"] is False
    assert "rowModelType" not in grid.dashGridOptions

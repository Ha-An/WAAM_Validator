from __future__ import annotations

from pathlib import Path

import pytest

from waam_validator.dashboard import single_app
from waam_validator.dashboard.app import (
    _arm_envelope_gauge,
    _collision_gauge,
    _config_inspection,
    _shape_figure,
    _trajectory_inspection,
)
from waam_validator.dashboard.data import DashboardDataError, JobRecord, RunRecord
from waam_validator.dashboard.single_app import _layer_display_rows, _reach_display_rows


def _collision_payload(*, enabled: bool, event_count: int) -> dict[str, object]:
    return {
        "collision": {
            "tcp_radius": {
                "minimum_distance_mm": 300.0,
                "required_distance_at_minimum_mm": 300.0,
                "event_count": event_count,
                "enabled": enabled,
            },
        }
    }


@pytest.mark.parametrize(
    ("enabled", "events", "expected_color"),
    [(True, 1, "#ff6376"), (True, 0, "#21d4a3"), (False, 0, "#8fa3b8")],
)
def test_tcp_gauge_uses_recorded_verdict_at_touching_threshold(
    enabled: bool, events: int, expected_color: str
) -> None:
    figure = _collision_gauge(_collision_payload(enabled=enabled, event_count=events))

    assert figure.data[0].gauge.bar.color == expected_color


def test_arm_gauge_shows_required_distance_and_recorded_margin() -> None:
    figure = _arm_envelope_gauge(
        {
            "collision": {
                "arm_envelope": {
                    "enabled": True,
                    "passed": False,
                    "event_count": 2,
                    "centerline_distance_at_worst_mm": 225.0,
                    "required_distance_at_worst_mm": 250.0,
                    "minimum_safety_margin_mm": -25.0,
                }
            }
        }
    )

    assert figure.data[0].gauge.bar.color == "#ff6376"
    assert figure.data[0].value == pytest.approx(225.0)
    assert figure.data[0].delta.reference == pytest.approx(250.0)
    assert "-25.00 mm" in str(figure.data[0].title.text)


def test_old_result_schema_requires_revalidation(fixture_root: Path, tmp_path: Path) -> None:
    run_dir = tmp_path / "output" / "legacy"
    run_dir.mkdir(parents=True)
    run = RunRecord(
        run_dir,
        "PASS",
        {"schema_version": "1.1", "status": "PASS"},
        "legacy",
    )

    view = single_app._result_view(
        "token", JobRecord("fixture", fixture_root / "collision_free"), run
    )

    assert "현재 Capsule 결과 형식이 아닙니다" in str(view)
    assert "Validation을 다시 실행" in str(view)


def test_one_corrupt_result_csv_does_not_discard_other_tables(
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

    monkeypatch.setattr(single_app, "read_csv_records", fake_read_csv_records)
    payload = {
        "schema_version": "2.0",
        "status": "PASS",
        "schedule": {"makespan_s": 10.0},
        "reach": {"passed": True, "robots": []},
        "collision": {
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
            },
        },
        "shape": {"coverage": 1.0, "iou": 1.0, "failed_layer_count": 0},
        "failure_reasons": [],
        "warnings": [],
        "errors": [],
    }
    run_dir = tmp_path / "output" / "2026-01-01_000000"
    run_dir.mkdir(parents=True)
    run = RunRecord(run_dir, "PASS", payload, "2026-01-01 00:00:00")

    view = single_app._result_view(
        "token", JobRecord("fixture", fixture_root / "collision_free"), run
    )

    assert view is not None
    assert calls == [
        "robot_metrics.csv",
        "collision_events.csv",
        "layer_metrics.csv",
        "warnings.csv",
    ]
    rendered = str(view)
    assert "TCP Radius 안전거리" in rendered
    assert "판정 결과와 확인사항" in rendered
    assert "형상 판정과 핵심 지표" in rendered
    assert "검증 영역별 결론" not in rendered
    assert "Arm Envelope 안전 여유" not in rendered
    assert "전체 형상 판정 요약" not in rendered


def test_reach_result_rows_are_human_formatted() -> None:
    rows = _reach_display_rows(
        [
            {
                "robot_id": 1,
                "passed": True,
                "reach_radius_mm": 2000.0,
                "maximum_reach_mm": 1845.7311685966965,
                "minimum_margin_mm": 154.26883140330347,
                "utilization_ratio": 0.9228655842983483,
                "violation_point_count": 0,
            }
        ]
    )

    assert rows == [
        {
            "robot_id": "R1",
            "passed": "PASS",
            "reach_radius_mm": "2,000.00",
            "maximum_reach_mm": "1,845.73",
            "minimum_margin_mm": "154.27",
            "utilization_ratio": "92.29%",
            "violation_point_count": "0",
        }
    ]


def test_fail_details_are_collapsed_and_payload_errors_have_a_fallback(
    fixture_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(single_app, "read_csv_records", lambda _path, _name: [])
    payload = {
        "schema_version": "2.0",
        "status": "FAIL",
        "schedule": {"makespan_s": 10.0},
        "reach": {"passed": False, "robots": []},
        "collision": {"passed": True, "arm_envelope": {}, "tcp_radius": {}},
        "shape": {"passed": True},
        "failure_reasons": ["ROBOT_REACH: R1 exceeds configured reach."],
        "warnings": [],
        "errors": ["ROBOT_REACH_VIOLATION - detailed reach evidence"],
    }
    run_dir = tmp_path / "output" / "2026-01-01_000000"
    run_dir.mkdir(parents=True)
    run = RunRecord(run_dir, "FAIL", payload, "2026-01-01 00:00:00")

    rendered = str(
        single_app._result_view(
            "token", JobRecord("fixture", fixture_root / "collision_free"), run
        )
    )

    assert "FAIL 세부 판정 기록 1건 보기" in rendered
    assert "detailed reach evidence" in rendered


def test_layer_result_rows_use_percentages_and_verdict_text() -> None:
    rows = _layer_display_rows(
        [
            {
                "layer_index": 2,
                "z_slice_mm": 5.0,
                "coverage": 0.999677,
                "underfill_ratio": 0.000323,
                "overfill_ratio": 0.0000018,
                "iou": 0.999675,
                "passed": True,
            }
        ]
    )

    assert rows[0] == {
        "layer_index": 2,
        "z_slice_mm": "5.00",
        "coverage": "99.9677%",
        "underfill_ratio": "0.0323%",
        "overfill_ratio": "0.0002%",
        "iou": "99.9675%",
        "passed": "PASS",
    }


def test_layer_chart_only_draws_actual_per_layer_threshold() -> None:
    figure = _shape_figure(
        [
            {
                "layer_index": 1,
                "coverage": 0.7,
                "underfill_ratio": 0.3,
                "overfill_ratio": 0.1,
                "iou": 0.7,
                "passed": False,
            }
        ],
        {
            "minimum_overall_coverage": 0.95,
            "maximum_overall_overfill_ratio": 0.05,
            "minimum_layer_iou": 0.8,
        },
    )

    assert len(figure.layout.shapes) == 1
    assert float(figure.layout.shapes[0].y0) == 0.8
    assert any(trace.name == "실패 Layer" for trace in figure.data)


def test_layer_chart_magnifies_small_errors_without_threshold_compression() -> None:
    figure = _shape_figure(
        [
            {
                "layer_index": 1,
                "coverage": 0.99999,
                "underfill_ratio": 0.00001,
                "overfill_ratio": 0.000002,
                "iou": 0.999988,
                "passed": True,
            },
            {
                "layer_index": 2,
                "coverage": 0.99998,
                "underfill_ratio": 0.00002,
                "overfill_ratio": 0.000003,
                "iou": 0.999977,
                "passed": True,
            },
        ],
        {"minimum_layer_iou": 0.8},
    )

    assert len(figure.layout.shapes) == 0
    assert any(trace.name == "IoU 손실" for trace in figure.data)
    assert float(figure.layout.yaxis.range[0]) > 0.99
    assert float(figure.layout.yaxis2.range[1]) <= 0.001


def test_trajectory_makespan_displays_seconds_minutes_and_hours() -> None:
    view = _trajectory_inspection(
        {
            "row_count": 3,
            "makespan_s": 7200.0,
            "workload_imbalance_s": 0.0,
            "robots": [],
        }
    )

    rendered = str(view)
    assert "7,200.00초" in rendered
    assert "120.00분" in rendered
    assert "2.00시간" in rendered


def test_config_view_distinguishes_robot_base_home_and_tcp_radius() -> None:
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
                "output": {},
            }
        )
    )

    assert "Robot 1 Base 위치" in rendered
    assert "Robot 1 Home TCP 위치" in rendered
    assert "Robot 1 TCP / Arm Capsule / Reach 반경" in rendered
    assert "R1–R1" not in rendered
    assert "충돌 이벤트 병합 최대 간격" in rendered
    assert "Schema" not in rendered

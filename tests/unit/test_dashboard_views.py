from __future__ import annotations

from pathlib import Path

import pytest

from waam_validator.dashboard import single_app
from waam_validator.dashboard.app import _collision_gauge, _shape_figure, _trajectory_inspection
from waam_validator.dashboard.data import DashboardDataError, JobRecord, RunRecord
from waam_validator.dashboard.single_app import _layer_display_rows, _reach_display_rows


def _collision_payload(*, enabled: bool, event_count: int) -> dict[str, object]:
    return {
        "collision": {
            "minimum_tcp_distance_mm": 300.0,
            "minimum_required_distance_mm": 300.0,
            "tcp_radius_event_count": event_count,
            "checks_enabled": {"tcp_radius": enabled},
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
        "status": "PASS",
        "schedule": {"makespan_s": 10.0},
        "reach": {"passed": True, "robots": []},
        "collision": {
            "collision_event_count": 0,
            "tcp_radius_event_count": 0,
            "minimum_tcp_distance_mm": 500.0,
            "minimum_required_distance_mm": 300.0,
            "checks_enabled": {"tcp_radius": True},
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
    assert "최소 TCP 간 거리" in rendered
    assert "검사 영역별 판정" in rendered
    assert "전체 형상 판정 요약" in rendered


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

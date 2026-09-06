from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from waam_validator.config.loader import load_config
from waam_validator.constants import MODE_D, MODE_T, MODE_W
from waam_validator.dashboard.preview import _statistics_figures, build_gantt_figure
from waam_validator.models import RobotTrajectory, TrajectorySet
from waam_validator.trajectory.loader import load_trajectory_csv


def _trajectory(robot_id: int, interval_count: int) -> RobotTrajectory:
    time_s = np.arange(interval_count + 1, dtype=np.float64)
    xyz = np.zeros((interval_count + 1, 3), dtype=np.float32)
    mode = np.resize(np.array([MODE_D, MODE_T], dtype=np.uint8), interval_count + 1)
    return RobotTrajectory(robot_id, time_s, xyz, mode)


def test_input_gantt_bounds_overview_and_loads_exact_selected_range() -> None:
    trajectories = TrajectorySet(
        (_trajectory(1, 3_000), _trajectory(2, 3_000), _trajectory(3, 3_000)),
        9_003,
    )

    overview = build_gantt_figure(trajectories)
    assert overview.data[0].type == "heatmap"
    assert len(overview.data[0].x) == 2_000
    assert "2,000개 시간 bin" in str(overview.layout.annotations[0].text)
    assert "상태시간 비율" in str(overview.layout.annotations[0].text)
    assert [str(trace.name) for trace in overview.data[1:]] == [
        "Travel · 비적층 이동",
        "Deposition · 적층",
        "Wait · 위치 유지 대기",
        "완료 후 비활성",
    ]

    figure = build_gantt_figure(trajectories, start_s=100.0, end_s=120.0)
    traces = {str(trace.name): trace for trace in figure.data}
    assert set(traces) == {
        "Deposition · 적층",
        "Travel · 비적층 이동",
        "Wait · 위치 유지 대기",
        "완료 후 비활성 · 작업 종료",
    }
    assert list(figure.layout.yaxis.categoryarray) == ["R1", "R2", "R3"]
    assert all(trace.orientation == "h" for trace in figure.data)
    assert figure.layout.height == 420
    assert figure.layout.xaxis.rangeslider.visible is True
    assert list(figure.layout.xaxis.range) == [100.0, 120.0]
    assert [button.label for button in figure.layout.updatemenus[0].buttons] == [
        "상세 보기",
        "전체 보기",
    ]
    assert figure.layout.updatemenus[0].showactive is False
    assert figure.layout.updatemenus[0].font.color == "#f3f8ff"
    assert figure.layout.legend.title.text is None

    for robot_label in ("R1", "R2", "R3"):
        deposition = traces["Deposition · 적층"]
        travel = traces["Travel · 비적층 이동"]
        deposition_duration = sum(
            float(value)
            for value, robot in zip(deposition.x, deposition.y, strict=True)
            if robot == robot_label
        )
        travel_duration = sum(
            float(value)
            for value, robot in zip(travel.x, travel.y, strict=True)
            if robot == robot_label
        )
        assert deposition_duration == 10.0
        assert travel_duration == 10.0

    assert list(traces["Wait · 위치 유지 대기"].x) == [0.0]
    assert list(traces["완료 후 비활성 · 작업 종료"].x) == [0.0]
    assert traces["Deposition · 적층"].marker.color == "#f97316"
    assert traces["Travel · 비적층 이동"].marker.color == "#38bdf8"


def test_large_gantt_overview_preserves_frequent_short_state_ratios() -> None:
    interval_count = 10_000
    time_s = np.arange(interval_count + 1, dtype=np.float64)
    xyz = np.zeros((interval_count + 1, 3), dtype=np.float32)
    mode = np.resize(
        np.array([MODE_T, MODE_T, MODE_T, MODE_D, MODE_W], dtype=np.uint8),
        interval_count + 1,
    )
    trajectories = TrajectorySet(
        tuple(RobotTrajectory(robot_id, time_s, xyz, mode) for robot_id in (1, 2, 3)),
        3 * (interval_count + 1),
    )

    overview = build_gantt_figure(trajectories)
    raster = np.asarray(overview.data[0].z, dtype=np.uint8)
    for robot_states in raster:
        counts = np.bincount(robot_states, minlength=4) / len(robot_states)
        assert counts == pytest.approx([0.6, 0.2, 0.2, 0.0], abs=0.001)

    custom = np.asarray(overview.data[0].customdata, dtype=np.float64)
    assert custom.shape == (3, 2_000, 6)
    assert np.sum(custom[0, :, 2]) == pytest.approx(2_000.0)
    assert np.sum(custom[0, :, 3]) == pytest.approx(6_000.0)
    assert np.sum(custom[0, :, 4]) == pytest.approx(2_000.0)


def test_input_time_percentages_and_reach_have_separate_axes(fixture_root: Path) -> None:
    job = fixture_root / "collision_free"
    config = load_config(job / "config.yaml")
    trajectories = load_trajectory_csv(job / "trajectory.csv", config)

    time_figure, motion_figure, reach_figure = _statistics_figures(config, trajectories)

    assert [str(trace.name) for trace in time_figure.data] == [
        "Deposition · 적층",
        "Travel · 비적층 이동",
        "Wait · 위치 유지 대기",
        "완료 후 비활성",
    ]
    for robot_index in range(3):
        assert np.isclose(sum(float(trace.y[robot_index]) for trace in time_figure.data), 100.0)
        assert all(
            str(trace.text[robot_index]).endswith("%")
            for trace in time_figure.data
            if float(trace.y[robot_index]) >= 3.0
        )
    assert time_figure.layout.yaxis.title.text == "Makespan 대비 비율 [%]"
    assert "기준 Makespan" in str(time_figure.layout.annotations[0].text)
    assert len(motion_figure.data) == 2
    assert "yaxis2" not in motion_figure.layout
    assert np.asarray(motion_figure.data[0].customdata).shape == (3, 2)
    speed_note = str(motion_figure.layout.annotations[0].text)
    assert f"Deposition {config.process.deposition_speed_mm_s:g}" in speed_note
    assert f"Travel {config.process.travel_speed_mm_s:g}" in speed_note
    assert len(reach_figure.data) == 1
    assert float(reach_figure.layout.shapes[0].x0) == 100.0


def test_time_chart_does_not_mislabel_post_completion_as_wait(fixture_root: Path) -> None:
    config = load_config(fixture_root / "collision_free" / "config.yaml")
    short = RobotTrajectory(
        robot_id=1,
        time_s=np.array([0.0, 5.0], dtype=np.float64),
        xyz_mm=np.zeros((2, 3), dtype=np.float32),
        mode=np.array([MODE_D, MODE_D], dtype=np.uint8),
    )
    long_2 = RobotTrajectory(
        robot_id=2,
        time_s=np.array([0.0, 10.0], dtype=np.float64),
        xyz_mm=np.zeros((2, 3), dtype=np.float32),
        mode=np.array([MODE_T, MODE_T], dtype=np.uint8),
    )
    long_3 = RobotTrajectory(
        robot_id=3,
        time_s=np.array([0.0, 10.0], dtype=np.float64),
        xyz_mm=np.zeros((2, 3), dtype=np.float32),
        mode=np.array([MODE_T, MODE_T], dtype=np.uint8),
    )
    trajectories = TrajectorySet((short, long_2, long_3), 6)

    time_figure, _, _ = _statistics_figures(config, trajectories)
    traces = {str(trace.name): trace for trace in time_figure.data}

    assert float(traces["Wait · 위치 유지 대기"].y[0]) == 0.0
    assert float(traces["완료 후 비활성"].y[0]) == 50.0

    gantt = build_gantt_figure(trajectories)
    gantt_traces = {str(trace.name): trace for trace in gantt.data}
    inactive = gantt_traces["완료 후 비활성 · 작업 종료"]
    assert list(inactive.y) == ["R1"]
    assert list(inactive.base) == [5.0]
    assert list(inactive.x) == [5.0]
    assert np.allclose(np.asarray(inactive.customdata, dtype=float), [[5.0, 10.0, 5.0]])

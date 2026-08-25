from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from waam_validator.collision.geometry2d import (
    check_arm_crossing_xy,
    check_tcp_radius_xy,
)
from waam_validator.collision.simulator import (
    CollisionEventAccumulator,
    run_collision_analysis,
)
from waam_validator.config.loader import load_config
from waam_validator.models import SegmentIntersectionResult, TcpRadiusResult
from waam_validator.schedule.metrics import compute_schedule_metrics
from waam_validator.trajectory.loader import load_trajectory_csv


@pytest.mark.parametrize(
    ("a", "b", "c", "d", "expected"),
    [
        ((0, 0), (10, 10), (0, 10), (10, 0), True),
        ((0, 0), (10, 0), (0, 5), (10, 5), False),
        ((0, 0), (10, 0), (5, 0), (15, 0), True),
        ((0, 0), (10, 0), (10, 0), (10, 10), True),
        ((0, 0), (0, 0), (0, 0), (10, 0), True),
    ],
)
def test_segment_intersection_cases(
    a: object, b: object, c: object, d: object, expected: bool
) -> None:
    result = check_arm_crossing_xy(
        np.asarray(a), np.asarray(b), np.asarray(c), np.asarray(d), 1e-6, True
    )
    assert result.intersects is expected


def test_endpoint_touch_can_be_disabled() -> None:
    result = check_arm_crossing_xy(
        np.array([0, 0]),
        np.array([10, 0]),
        np.array([10, 0]),
        np.array([10, 10]),
        1e-6,
        False,
    )
    assert not result.intersects


def test_proper_intersection_location() -> None:
    result = check_arm_crossing_xy(
        np.array([0.0, 0.0]),
        np.array([10.0, 10.0]),
        np.array([0.0, 10.0]),
        np.array([10.0, 0.0]),
        1e-6,
        True,
    )
    assert (result.x_mm, result.y_mm) == pytest.approx((5.0, 5.0))


@pytest.mark.parametrize(
    ("distance", "touching", "expected"),
    [(299.9, True, True), (300.0, True, True), (300.0, False, False), (300.1, True, False)],
)
def test_tcp_radius_threshold(distance: float, touching: bool, expected: bool) -> None:
    result = check_tcp_radius_xy(
        np.array([0.0, 0.0]), 150.0, np.array([distance, 0.0]), 150.0, touching
    )
    assert result.collision is expected


def test_schedule_and_collision_free_fixture(fixture_root: Path) -> None:
    job = fixture_root / "collision_free"
    config = load_config(job / "config.yaml")
    trajectories = load_trajectory_csv(job / "trajectory.csv", config)
    schedule = compute_schedule_metrics(trajectories)
    assert schedule.makespan_s == pytest.approx(18.0)
    assert schedule.robots[0].deposition_time_s == pytest.approx(10.0)
    result = run_collision_analysis(trajectories, config)
    assert not result.events


def test_known_collision_fixtures(fixture_root: Path) -> None:
    arm_job = fixture_root / "arm_cross"
    arm_config = load_config(arm_job / "config.yaml")
    arm = run_collision_analysis(
        load_trajectory_csv(arm_job / "trajectory.csv", arm_config), arm_config
    )
    assert arm.arm_cross_event_count >= 1

    tcp_job = fixture_root / "tcp_radius"
    tcp_config = load_config(tcp_job / "config.yaml")
    tcp = run_collision_analysis(
        load_trajectory_csv(tcp_job / "trajectory.csv", tcp_config), tcp_config
    )
    assert tcp.tcp_radius_event_count == 1
    assert tcp.arm_cross_event_count == 0


def test_time_separated_crossing_is_collision_free(fixture_root: Path) -> None:
    job = fixture_root / "time_separated_crossing"
    config = load_config(job / "config.yaml")
    result = run_collision_analysis(load_trajectory_csv(job / "trajectory.csv", config), config)
    assert not result.events


def test_event_accumulator_merges_only_short_false_gaps() -> None:
    accumulator = CollisionEventAccumulator(merge_gap_s=0.2)
    pair = (1, 2)
    true_arm = SegmentIntersectionResult(True, 1.0, 2.0)
    false_arm = SegmentIntersectionResult(False)
    safe_tcp = TcpRadiusResult(False, 100.0, 20.0)
    positions = (np.array([0.0, 0.0]), np.array([100.0, 0.0]))
    for time_s, arm in (
        (0.0, true_arm),
        (0.1, true_arm),
        (0.2, false_arm),
        (0.3, true_arm),
        (0.4, false_arm),
        (0.8, true_arm),
    ):
        accumulator.update(time_s, pair, arm, safe_tcp, positions)
    events = accumulator.finalize(1.0)
    arm_events = [event for event in events if event.collision_type == "ARM_CROSS"]
    assert len(arm_events) == 2
    assert arm_events[0].start_s == 0.0
    assert arm_events[0].end_s == 0.3

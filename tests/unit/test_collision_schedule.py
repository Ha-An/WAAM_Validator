from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from waam_validator.collision.geometry2d import (
    check_arm_envelope_xy,
    check_arm_envelope_xy_batch,
    check_tcp_radius_xy,
)
from waam_validator.collision.simulator import (
    CollisionEventAccumulator,
    run_collision_analysis,
)
from waam_validator.config.loader import load_config
from waam_validator.models import (
    ArmEnvelopeResult,
    RobotTrajectory,
    TcpRadiusResult,
    TrajectorySet,
)
from waam_validator.schedule.metrics import compute_schedule_metrics
from waam_validator.trajectory.loader import load_trajectory_csv


@pytest.mark.parametrize(
    ("a", "b", "c", "d", "expected_distance"),
    [
        ((0, 0), (10, 10), (0, 10), (10, 0), 0.0),
        ((0, 0), (10, 0), (0, 5), (10, 5), 5.0),
        ((0, 0), (10, 0), (5, 0), (15, 0), 0.0),
        ((0, 0), (10, 0), (10, 0), (10, 10), 0.0),
        ((0, 0), (0, 0), (3, 4), (3, 4), 5.0),
    ],
)
def test_capsule_centerline_distance_cases(
    a: object, b: object, c: object, d: object, expected_distance: float
) -> None:
    result = check_arm_envelope_xy(
        np.asarray(a), np.asarray(b), 1.0, np.asarray(c), np.asarray(d), 1.0, 0.0, 1e-6, True
    )
    assert result.centerline_distance_mm == pytest.approx(expected_distance)


def test_capsule_exact_touching_respects_policy_and_epsilon() -> None:
    arguments = (
        np.array([0.0, 0.0]),
        np.array([10.0, 0.0]),
        2.0,
        np.array([0.0, 5.0]),
        np.array([10.0, 5.0]),
        2.0,
        1.0,
        1e-6,
    )
    assert check_arm_envelope_xy(*arguments, True).collision
    assert not check_arm_envelope_xy(*arguments, False).collision


def test_capsule_unequal_radii_and_clearance_only_violation() -> None:
    result = check_arm_envelope_xy(
        np.array([0.0, 0.0]),
        np.array([10.0, 0.0]),
        2.0,
        np.array([0.0, 7.0]),
        np.array([10.0, 7.0]),
        3.0,
        3.0,
        1e-6,
        True,
    )
    assert result.centerline_distance_mm == pytest.approx(7.0)
    assert result.capsule_surface_clearance_mm == pytest.approx(2.0)
    assert result.safety_margin_mm == pytest.approx(-1.0)
    assert result.collision


def test_capsule_batch_matches_scalar_and_closest_points() -> None:
    base_a = np.array([0.0, 0.0])
    base_b = np.array([10.0, 0.0])
    tcp_a = np.array([[10.0, 10.0], [5.0, 2.0], [0.0, 0.0], [2.0, 2.0]])
    tcp_b = np.array([[0.0, 10.0], [15.0, 2.0], [13.0, 4.0], [8.0, 2.0]])
    batch = check_arm_envelope_xy_batch(
        base_a, tcp_a, 2.0, base_b, tcp_b, 3.0, 1.0, 1e-6, True
    )
    for index in range(len(tcp_a)):
        scalar = check_arm_envelope_xy(
            base_a,
            tcp_a[index],
            2.0,
            base_b,
            tcp_b[index],
            3.0,
            1.0,
            1e-6,
            True,
        )
        assert bool(batch.collision[index]) is scalar.collision
        assert batch.centerline_distance_mm[index] == pytest.approx(
            scalar.centerline_distance_mm
        )
        assert batch.safety_margin_mm[index] == pytest.approx(scalar.safety_margin_mm)
        assert batch.closest_a_xy_mm[index] == pytest.approx(
            (scalar.closest_a_x_mm, scalar.closest_a_y_mm)
        )
        assert batch.closest_b_xy_mm[index] == pytest.approx(
            (scalar.closest_b_x_mm, scalar.closest_b_y_mm)
        )


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
    assert arm.arm_envelope_event_count >= 1

    near_job = fixture_root / "arm_envelope_near_miss"
    near_config = load_config(near_job / "config.yaml")
    near = run_collision_analysis(
        load_trajectory_csv(near_job / "trajectory.csv", near_config), near_config
    )
    assert near.arm_envelope_event_count >= 1
    assert near.minimum_arm_pair == (1, 2)
    assert 0.0 < near.arm_centerline_distance_at_worst_mm < 250.0
    assert near.minimum_arm_safety_margin_mm < 0.0

    tcp_job = fixture_root / "tcp_radius"
    tcp_config = load_config(tcp_job / "config.yaml")
    tcp = run_collision_analysis(
        load_trajectory_csv(tcp_job / "trajectory.csv", tcp_config), tcp_config
    )
    assert tcp.tcp_radius_event_count == 1
    assert tcp.arm_envelope_event_count == 0
    assert tcp.minimum_arm_safety_margin_mm < 0.0


def test_time_separated_crossing_is_collision_free(fixture_root: Path) -> None:
    job = fixture_root / "time_separated_crossing"
    config = load_config(job / "config.yaml")
    result = run_collision_analysis(load_trajectory_csv(job / "trajectory.csv", config), config)
    assert not result.events


def test_completed_robot_remains_a_stationary_collision_body(fixture_root: Path) -> None:
    config = load_config(fixture_root / "collision_free" / "config.yaml")

    def trajectory(
        robot_id: int,
        times: list[float],
        xyz: list[list[float]],
        modes: list[int],
    ) -> RobotTrajectory:
        return RobotTrajectory(
            robot_id,
            np.asarray(times, dtype=np.float64),
            np.asarray(xyz, dtype=np.float32),
            np.asarray(modes, dtype=np.uint8),
        )

    trajectories = TrajectorySet(
        (
            trajectory(1, [0.0, 1.0], [[-1000, -600, 100], [0, -100, 100]], [0, 2]),
            trajectory(
                2,
                [0.0, 1.0, 2.0],
                [[1000, -600, 100], [1000, -600, 100], [0, 100, 100]],
                [2, 0, 2],
            ),
            trajectory(3, [0.0, 2.0], [[0, 1200, 100], [0, 1200, 100]], [2, 2]),
        ),
        7,
    )
    result = run_collision_analysis(trajectories, config)
    events = [
        event
        for event in result.events
        if event.collision_type == "ARM_ENVELOPE" and (event.robot_a, event.robot_b) == (1, 2)
    ]

    assert events
    assert events[0].start_s > 1.0


def _arm_result(collided: bool, margin: float) -> ArmEnvelopeResult:
    return ArmEnvelopeResult(
        collision=collided,
        centerline_distance_mm=250.0 + margin,
        required_distance_mm=250.0,
        safety_margin_mm=margin,
        capsule_surface_clearance_mm=50.0 + margin,
        closest_a_x_mm=1.0,
        closest_a_y_mm=2.0,
        closest_b_x_mm=3.0,
        closest_b_y_mm=4.0,
    )


def test_event_accumulator_merges_only_short_false_gaps() -> None:
    accumulator = CollisionEventAccumulator(merge_gap_s=0.2)
    pair = (1, 2)
    safe_tcp = TcpRadiusResult(False, 100.0, 20.0)
    positions = (np.array([0.0, 0.0]), np.array([100.0, 0.0]))
    for time_s, arm in (
        (0.0, _arm_result(True, -1.0)),
        (0.1, _arm_result(True, -2.0)),
        (0.2, _arm_result(False, 1.0)),
        (0.3, _arm_result(True, -3.0)),
        (0.4, _arm_result(False, 1.0)),
        (0.8, _arm_result(True, -1.0)),
    ):
        accumulator.update(time_s, pair, arm, safe_tcp, positions)
    events = accumulator.finalize(1.0)
    arm_events = [event for event in events if event.collision_type == "ARM_ENVELOPE"]
    assert len(arm_events) == 2
    assert arm_events[0].start_s == 0.0
    assert arm_events[0].end_s == 0.3
    assert arm_events[0].minimum_safety_margin_mm == pytest.approx(-3.0)


def test_batch_event_accumulator_merges_gap_across_batch_boundary() -> None:
    accumulator = CollisionEventAccumulator(merge_gap_s=0.2)
    pair = (1, 2)
    for times, arm_collision in (
        (np.array([0.0, 0.1, 0.2]), np.array([True, True, False])),
        (np.array([0.3, 0.4, 0.8]), np.array([True, False, True])),
    ):
        count = len(times)
        arm_margin = np.where(arm_collision, -1.0, 1.0)
        accumulator.update_batch(
            times,
            pair,
            arm_collision,
            250.0 + arm_margin,
            250.0,
            arm_margin,
            50.0 + arm_margin,
            np.zeros((count, 2)),
            np.ones((count, 2)),
            np.zeros(count, dtype=np.bool_),
            np.full(count, 100.0),
            20.0,
            np.full(count, 80.0),
            np.zeros((count, 2)),
            np.ones((count, 2)),
        )

    events = accumulator.finalize(1.0)
    arm_events = [event for event in events if event.collision_type == "ARM_ENVELOPE"]
    assert [(event.start_s, event.end_s) for event in arm_events] == [
        (0.0, 0.3),
        (0.8, 0.8),
    ]

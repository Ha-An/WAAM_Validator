from __future__ import annotations

import numpy as np
import pytest

from waam_validator.constants import MODE_W
from waam_validator.models import CollisionEvent, RobotTrajectory, TrajectorySet
from waam_validator.visualization.replay import build_replay_frame_times


def _trajectories(makespan_s: float) -> TrajectorySet:
    robots = []
    for robot_id in (1, 2, 3):
        robots.append(
            RobotTrajectory(
                robot_id,
                np.asarray([0.0, makespan_s], dtype=np.float64),
                np.asarray(
                    [[float(robot_id), 0.0, 0.0], [float(robot_id), 0.0, 0.0]],
                    dtype=np.float32,
                ),
                np.asarray([MODE_W, MODE_W], dtype=np.uint8),
            )
        )
    return TrajectorySet((robots[0], robots[1], robots[2]), 6)


def _event(index: int) -> CollisionEvent:
    start = index * 0.7
    return CollisionEvent(
        event_id=index + 1,
        collision_type="ARM_ENVELOPE",
        robot_a=1,
        robot_b=2,
        start_s=start,
        end_s=start + 0.3,
        duration_s=0.3,
        minimum_distance_mm=0.0,
        required_distance_mm=250.0,
        minimum_safety_margin_mm=-float(index),
        minimum_capsule_surface_clearance_mm=-200.0,
        minimum_distance_time_s=start + 0.15,
        closest_a_x_mm=0.0,
        closest_a_y_mm=0.0,
        closest_b_x_mm=0.0,
        closest_b_y_mm=0.0,
    )


def test_many_collision_boundaries_are_sampled_within_frame_budget() -> None:
    events = [_event(index) for index in range(1_200)]

    frames = build_replay_frame_times(
        _trajectories(1_000.0),
        events,
        100.0,
        max_frames=100,
    )

    assert len(frames) == 100
    assert frames == sorted(set(frames))
    assert set(np.arange(0.0, 1_000.0, 100.0)).issubset(frames)
    assert 1_000.0 in frames
    assert events[-1].minimum_distance_time_s in frames


def test_regular_frames_still_enforce_the_hard_safety_limit() -> None:
    with pytest.raises(ValueError, match="regular frames"):
        build_replay_frame_times(
            _trajectories(1_000.0),
            [],
            1.0,
            max_frames=100,
        )

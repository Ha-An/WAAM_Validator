"""Robot TCP reach metrics for the spherical Config reach proxy."""

from __future__ import annotations

import numpy as np

from ..config.models import Config
from ..models import ReachMetrics, RobotReachMetrics, TrajectorySet


def compute_reach_metrics(trajectories: TrajectorySet, config: Config) -> ReachMetrics:
    """Measure all original TCP points against each robot's configured reach sphere."""
    robots: list[RobotReachMetrics] = []
    epsilon = config.collision.geometry_epsilon_mm
    for trajectory in trajectories.robots:
        robot = config.robot(trajectory.robot_id)
        base = np.asarray(robot.base_xyz_mm, dtype=np.float64)
        distances = np.linalg.norm(trajectory.xyz_mm.astype(np.float64) - base[None, :], axis=1)
        violating = distances > robot.reach_radius_mm + epsilon
        indices = np.flatnonzero(violating)
        maximum = float(distances.max())
        robots.append(
            RobotReachMetrics(
                robot_id=robot.id,
                reach_radius_mm=robot.reach_radius_mm,
                maximum_reach_mm=maximum,
                minimum_margin_mm=robot.reach_radius_mm - maximum,
                utilization_ratio=maximum / robot.reach_radius_mm,
                violation_point_count=int(len(indices)),
                first_violation_s=(
                    float(trajectory.time_s[int(indices[0])]) if len(indices) else None
                ),
                last_violation_s=(
                    float(trajectory.time_s[int(indices[-1])]) if len(indices) else None
                ),
            )
        )
    return ReachMetrics(robots=robots)

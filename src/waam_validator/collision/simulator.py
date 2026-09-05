"""Streaming collision simulation and event aggregation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config.models import Config
from ..constants import ROBOT_PAIRS
from ..models import (
    CollisionEvent,
    CollisionSimulationResult,
    SegmentIntersectionResult,
    TcpRadiusResult,
    TrajectorySet,
)
from ..progress import StageProgressCallback
from ..trajectory.sampling import iter_simulation_samples
from .geometry2d import check_arm_crossing_xy_batch


@dataclass(slots=True)
class _ActiveEvent:
    collision_type: str
    robot_a: int
    robot_b: int
    start_s: float
    last_true_s: float
    gap_start_s: float | None = None
    min_distance_mm: float | None = None
    required_distance_mm: float | None = None
    min_distance_time_s: float | None = None
    crossing_x_mm: float | None = None
    crossing_y_mm: float | None = None
    marker_x_mm: float | None = None
    marker_y_mm: float | None = None


class CollisionEventAccumulator:
    """Merge sampled collision states into stable interval events."""

    def __init__(self, merge_gap_s: float) -> None:
        self.merge_gap_s = merge_gap_s
        self._active: dict[tuple[str, int, int], _ActiveEvent] = {}
        self._finished: list[CollisionEvent] = []

    def update(
        self,
        time_s: float,
        pair: tuple[int, int],
        arm_result: SegmentIntersectionResult,
        tcp_result: TcpRadiusResult,
        tcp_positions: tuple[np.ndarray, np.ndarray],
    ) -> None:
        self._update_one(time_s, pair, "ARM_CROSS", arm_result.intersects, arm_result, None)
        midpoint = (
            np.asarray(tcp_positions[0], dtype=np.float64)
            + np.asarray(tcp_positions[1], dtype=np.float64)
        ) / 2.0
        self._update_one(
            time_s,
            pair,
            "TCP_RADIUS",
            tcp_result.collision,
            None,
            tcp_result,
            midpoint,
        )

    def _update_one(
        self,
        time_s: float,
        pair: tuple[int, int],
        collision_type: str,
        collided: bool,
        arm: SegmentIntersectionResult | None,
        tcp: TcpRadiusResult | None,
        midpoint: np.ndarray | None = None,
    ) -> None:
        key = (collision_type, pair[0], pair[1])
        active = self._active.get(key)
        if not collided:
            if active is not None and active.gap_start_s is None:
                active.gap_start_s = time_s
            return
        if active is not None and active.gap_start_s is not None:
            gap = time_s - active.gap_start_s
            if gap > self.merge_gap_s:
                self._finish(key)
                active = None
            else:
                active.gap_start_s = None
        if active is None:
            active = _ActiveEvent(collision_type, pair[0], pair[1], time_s, time_s)
            self._active[key] = active
        active.last_true_s = time_s
        active.gap_start_s = None
        if arm is not None and active.crossing_x_mm is None:
            active.crossing_x_mm = arm.x_mm
            active.crossing_y_mm = arm.y_mm
        if tcp is not None and (
            active.min_distance_mm is None or tcp.distance_mm < active.min_distance_mm
        ):
            active.min_distance_mm = tcp.distance_mm
            active.required_distance_mm = tcp.required_distance_mm
            active.min_distance_time_s = time_s
            if midpoint is not None:
                active.marker_x_mm = float(midpoint[0])
                active.marker_y_mm = float(midpoint[1])

    def update_batch(
        self,
        time_s: np.ndarray,
        pair: tuple[int, int],
        arm_collision: np.ndarray,
        crossing_x_mm: np.ndarray,
        crossing_y_mm: np.ndarray,
        tcp_collision: np.ndarray,
        tcp_distance_mm: np.ndarray,
        required_tcp_distance_mm: float,
        tcp_midpoint_xy: np.ndarray,
    ) -> None:
        """Consume collision arrays while visiting only state runs, not every sample."""
        self._update_batch_one(
            time_s,
            pair,
            "ARM_CROSS",
            arm_collision,
            crossing_x_mm=crossing_x_mm,
            crossing_y_mm=crossing_y_mm,
        )
        self._update_batch_one(
            time_s,
            pair,
            "TCP_RADIUS",
            tcp_collision,
            tcp_distance_mm=tcp_distance_mm,
            required_tcp_distance_mm=required_tcp_distance_mm,
            tcp_midpoint_xy=tcp_midpoint_xy,
        )

    def _update_batch_one(
        self,
        time_s: np.ndarray,
        pair: tuple[int, int],
        collision_type: str,
        collided: np.ndarray,
        *,
        crossing_x_mm: np.ndarray | None = None,
        crossing_y_mm: np.ndarray | None = None,
        tcp_distance_mm: np.ndarray | None = None,
        required_tcp_distance_mm: float | None = None,
        tcp_midpoint_xy: np.ndarray | None = None,
    ) -> None:
        if len(time_s) == 0:
            return
        key = (collision_type, pair[0], pair[1])
        changes = np.flatnonzero(collided[1:] != collided[:-1]) + 1
        starts = np.concatenate((np.array([0]), changes))
        stops = np.concatenate((changes, np.array([len(collided)])))
        for start_raw, stop_raw in zip(starts, stops, strict=True):
            start = int(start_raw)
            stop = int(stop_raw)
            active = self._active.get(key)
            if not bool(collided[start]):
                if active is not None and active.gap_start_s is None:
                    active.gap_start_s = float(time_s[start])
                continue

            run_start_s = float(time_s[start])
            if active is not None and active.gap_start_s is not None:
                if run_start_s - active.gap_start_s > self.merge_gap_s:
                    self._finish(key)
                    active = None
                else:
                    active.gap_start_s = None
            if active is None:
                active = _ActiveEvent(collision_type, pair[0], pair[1], run_start_s, run_start_s)
                self._active[key] = active
            active.last_true_s = float(time_s[stop - 1])
            active.gap_start_s = None

            if crossing_x_mm is not None and active.crossing_x_mm is None:
                active.crossing_x_mm = float(crossing_x_mm[start])
                if crossing_y_mm is not None:
                    active.crossing_y_mm = float(crossing_y_mm[start])
            if tcp_distance_mm is not None:
                relative_minimum = int(np.argmin(tcp_distance_mm[start:stop]))
                minimum_index = start + relative_minimum
                minimum = float(tcp_distance_mm[minimum_index])
                if active.min_distance_mm is None or minimum < active.min_distance_mm:
                    active.min_distance_mm = minimum
                    active.required_distance_mm = required_tcp_distance_mm
                    active.min_distance_time_s = float(time_s[minimum_index])
                    if tcp_midpoint_xy is not None:
                        active.marker_x_mm = float(tcp_midpoint_xy[minimum_index, 0])
                        active.marker_y_mm = float(tcp_midpoint_xy[minimum_index, 1])

    def _finish(self, key: tuple[str, int, int], final_time: float | None = None) -> None:
        active = self._active.pop(key)
        end_s = active.last_true_s if final_time is None else final_time
        self._finished.append(
            CollisionEvent(
                event_id=0,
                collision_type=active.collision_type,
                robot_a=active.robot_a,
                robot_b=active.robot_b,
                start_s=active.start_s,
                end_s=end_s,
                duration_s=end_s - active.start_s,
                min_tcp_distance_mm=active.min_distance_mm,
                required_tcp_distance_mm=active.required_distance_mm,
                crossing_x_mm=active.crossing_x_mm,
                crossing_y_mm=active.crossing_y_mm,
                min_distance_time_s=active.min_distance_time_s,
                marker_x_mm=active.marker_x_mm,
                marker_y_mm=active.marker_y_mm,
            )
        )

    def finalize(self, makespan_s: float) -> list[CollisionEvent]:
        for key, active in list(self._active.items()):
            final_time = makespan_s if active.last_true_s == makespan_s else None
            self._finish(key, final_time)
        self._finished.sort(
            key=lambda item: (item.start_s, item.collision_type, item.robot_a, item.robot_b)
        )
        for event_id, event in enumerate(self._finished, start=1):
            event.event_id = event_id
        return self._finished


def run_collision_analysis(
    trajectories: TrajectorySet,
    config: Config,
    *,
    progress_callback: StageProgressCallback | None = None,
) -> CollisionSimulationResult:
    """Run enabled collision checks and always track global minimum TCP distance."""
    bases: dict[int, np.ndarray] = {
        robot.id: np.asarray(robot.base_xyz_mm[:2], dtype=np.float64) for robot in config.robots
    }
    radii: dict[int, float] = {robot.id: robot.tcp_radius_mm for robot in config.robots}
    accumulator = CollisionEventAccumulator(config.simulation.event_merge_gap_s)
    minimum_distance = float("inf")
    minimum_pair = ROBOT_PAIRS[0]
    minimum_required = radii[1] + radii[2]
    sample_count = 0
    makespan = max(float(item.time_s[-1]) for item in trajectories.robots)
    batch_size = config.simulation.batch_size
    time_buffer = np.empty(batch_size, dtype=np.float64)
    xy_buffer = np.empty((batch_size, 3, 2), dtype=np.float64)
    buffered = 0

    def process_batch(count: int) -> None:
        nonlocal minimum_distance, minimum_pair, minimum_required
        times = time_buffer[:count]
        positions = xy_buffer[:count]
        for robot_a, robot_b in ROBOT_PAIRS:
            tcp_a = positions[:, robot_a - 1]
            tcp_b = positions[:, robot_b - 1]
            distances = np.hypot(tcp_a[:, 0] - tcp_b[:, 0], tcp_a[:, 1] - tcp_b[:, 1])
            required = radii[robot_a] + radii[robot_b]
            local_minimum_index = int(np.argmin(distances))
            local_minimum = float(distances[local_minimum_index])
            if local_minimum < minimum_distance:
                minimum_distance = local_minimum
                minimum_pair = (robot_a, robot_b)
                minimum_required = required
            if config.collision.check_arm_crossing:
                arm_collision, crossing_x, crossing_y = check_arm_crossing_xy_batch(
                    bases[robot_a],
                    tcp_a,
                    bases[robot_b],
                    tcp_b,
                    config.collision.geometry_epsilon_mm,
                    config.collision.touching_is_collision,
                )
            else:
                arm_collision = np.zeros(count, dtype=np.bool_)
                crossing_x = np.full(count, np.nan, dtype=np.float64)
                crossing_y = np.full(count, np.nan, dtype=np.float64)
            if config.collision.check_tcp_radius:
                if config.collision.touching_is_collision:
                    tcp_collision = distances <= required
                else:
                    tcp_collision = distances < required
            else:
                tcp_collision = np.zeros(count, dtype=np.bool_)
            accumulator.update_batch(
                times,
                (robot_a, robot_b),
                arm_collision,
                crossing_x,
                crossing_y,
                tcp_collision,
                distances,
                required,
                (tcp_a + tcp_b) / 2.0,
            )

    last_reported = -1.0
    for sample in iter_simulation_samples(trajectories, config):
        time_buffer[buffered] = sample.time_s
        xy_buffer[buffered] = sample.xyz_by_robot[:, :2]
        buffered += 1
        sample_count += 1
        if buffered == batch_size:
            process_batch(buffered)
            fraction = min(1.0, float(time_buffer[buffered - 1]) / makespan) if makespan else 1.0
            if progress_callback is not None and fraction - last_reported >= 0.001:
                progress_callback(fraction, float(time_buffer[buffered - 1]), makespan)
                last_reported = fraction
            buffered = 0
    if buffered:
        process_batch(buffered)
    if progress_callback is not None and last_reported < 1.0:
        progress_callback(1.0, makespan, makespan)
    return CollisionSimulationResult(
        events=accumulator.finalize(makespan),
        minimum_tcp_distance_mm=minimum_distance,
        minimum_tcp_pair=minimum_pair,
        minimum_required_distance_mm=minimum_required,
        sample_count=sample_count,
    )


def run_collision_simulation(
    trajectories: TrajectorySet,
    config: Config,
    *,
    progress_callback: StageProgressCallback | None = None,
) -> list[CollisionEvent]:
    """Run both configured collision checks and return merged events."""
    return run_collision_analysis(trajectories, config, progress_callback=progress_callback).events

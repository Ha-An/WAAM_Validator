"""Shared compact runtime and result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from shapely.geometry.base import BaseGeometry

from .errors import ValidationIssue


@dataclass(slots=True)
class RobotTrajectory:
    robot_id: int
    time_s: npt.NDArray[np.float64]
    xyz_mm: npt.NDArray[np.float32]
    mode: npt.NDArray[np.uint8]


@dataclass(slots=True)
class TrajectorySet:
    robots: tuple[RobotTrajectory, RobotTrajectory, RobotTrajectory]
    row_count: int

    def by_id(self, robot_id: int) -> RobotTrajectory:
        return self.robots[robot_id - 1]


@dataclass(slots=True)
class SimulationSample:
    time_s: float
    xyz_by_robot: npt.NDArray[np.float32]
    mode_by_robot: npt.NDArray[np.uint8]


@dataclass(slots=True, frozen=True)
class SegmentIntersectionResult:
    intersects: bool
    x_mm: float | None = None
    y_mm: float | None = None


@dataclass(slots=True, frozen=True)
class TcpRadiusResult:
    collision: bool
    distance_mm: float
    required_distance_mm: float


@dataclass(slots=True)
class CollisionEvent:
    event_id: int
    collision_type: str
    robot_a: int
    robot_b: int
    start_s: float
    end_s: float
    duration_s: float
    min_tcp_distance_mm: float | None = None
    required_tcp_distance_mm: float | None = None
    crossing_x_mm: float | None = None
    crossing_y_mm: float | None = None
    min_distance_time_s: float | None = None
    marker_x_mm: float | None = None
    marker_y_mm: float | None = None


@dataclass(slots=True)
class CollisionSimulationResult:
    events: list[CollisionEvent]
    minimum_tcp_distance_mm: float
    minimum_tcp_pair: tuple[int, int]
    minimum_required_distance_mm: float
    sample_count: int

    @property
    def arm_cross_event_count(self) -> int:
        return sum(event.collision_type == "ARM_CROSS" for event in self.events)

    @property
    def tcp_radius_event_count(self) -> int:
        return sum(event.collision_type == "TCP_RADIUS" for event in self.events)


@dataclass(slots=True)
class RobotMetrics:
    robot_id: int
    completion_s: float
    deposition_time_s: float
    travel_time_s: float
    wait_time_s: float
    deposition_ratio: float
    travel_ratio: float
    wait_ratio: float
    deposition_length_mm: float
    travel_length_mm: float
    mean_deposition_speed_mm_s: float | None
    mean_travel_speed_mm_s: float | None


@dataclass(slots=True)
class ScheduleMetrics:
    makespan_s: float
    robots: list[RobotMetrics]
    workload_imbalance_s: float
    normalized_imbalance: float


@dataclass(slots=True)
class LayerGeometry:
    layer_index: int
    z_bottom_mm: float
    z_top_mm: float
    z_slice_mm: float
    deposited_polygon: BaseGeometry
    target_polygon: BaseGeometry


@dataclass(slots=True)
class LayerMetrics:
    layer_index: int
    z_bottom_mm: float
    z_top_mm: float
    z_slice_mm: float
    target_area_mm2: float
    deposited_area_mm2: float
    intersection_area_mm2: float
    underfill_area_mm2: float
    overfill_area_mm2: float
    coverage: float
    underfill_ratio: float
    overfill_ratio: float | None
    iou: float
    passed: bool


@dataclass(slots=True)
class ShapeMetrics:
    target_volume_mm3: float
    deposited_volume_mm3: float
    intersection_volume_mm3: float
    underfill_volume_mm3: float
    overfill_volume_mm3: float
    coverage: float
    underfill_ratio: float
    overfill_ratio: float
    iou: float
    failed_layer_count: int
    evaluated_layer_count: int
    failed_layer_ratio: float
    target_mesh_volume_mm3: float
    target_volume_discrepancy_ratio: float
    passed: bool


@dataclass(slots=True)
class ValidationResult:
    status: str
    input_dir: Path
    output_dir: Path
    trajectory_rows: int
    target_watertight: bool
    schedule: ScheduleMetrics
    collision: CollisionSimulationResult
    shape: ShapeMetrics
    layer_metrics: list[LayerMetrics]
    warnings: list[ValidationIssue] = field(default_factory=list)
    errors: list[ValidationIssue] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    checks_enabled: dict[str, bool] = field(default_factory=dict)

    @property
    def collision_free(self) -> bool:
        return not self.collision.events

    def summary_dict(self) -> dict[str, Any]:
        completions = {str(item.robot_id): item.completion_s for item in self.schedule.robots}
        return {
            "schema_version": "1.0",
            "status": self.status,
            "input": {
                "directory": str(self.input_dir),
                "trajectory_rows": self.trajectory_rows,
                "target_watertight": self.target_watertight,
            },
            "schedule": {
                "makespan_s": self.schedule.makespan_s,
                "robot_completion_s": completions,
                "workload_imbalance_s": self.schedule.workload_imbalance_s,
                "normalized_imbalance": self.schedule.normalized_imbalance,
            },
            "collision": {
                "passed": self.collision_free,
                "collision_event_count": len(self.collision.events),
                "arm_cross_event_count": self.collision.arm_cross_event_count,
                "tcp_radius_event_count": self.collision.tcp_radius_event_count,
                "minimum_tcp_distance_mm": self.collision.minimum_tcp_distance_mm,
                "minimum_tcp_pair": list(self.collision.minimum_tcp_pair),
                "minimum_required_distance_mm": self.collision.minimum_required_distance_mm,
                "checks_enabled": self.checks_enabled,
            },
            "shape": {
                "passed": self.shape.passed,
                "target_volume_mm3": self.shape.target_volume_mm3,
                "deposited_volume_mm3": self.shape.deposited_volume_mm3,
                "coverage": self.shape.coverage,
                "underfill_ratio": self.shape.underfill_ratio,
                "overfill_ratio": self.shape.overfill_ratio,
                "iou": self.shape.iou,
                "failed_layer_count": self.shape.failed_layer_count,
                "evaluated_layer_count": self.shape.evaluated_layer_count,
                "failed_layer_ratio": self.shape.failed_layer_ratio,
                "target_mesh_volume_mm3": self.shape.target_mesh_volume_mm3,
                "target_volume_discrepancy_ratio": (
                    self.shape.target_volume_discrepancy_ratio
                ),
            },
            "failure_reasons": self.failure_reasons,
            "warnings": [issue.display() for issue in self.warnings],
            "errors": [issue.display() for issue in self.errors],
            "output_directory": str(self.output_dir),
        }

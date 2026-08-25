"""Strict Pydantic configuration schema."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class RobotConfig(FrozenModel):
    id: Literal[1, 2, 3]
    base_xyz_mm: tuple[float, float, float]
    tcp_radius_mm: float = Field(gt=0)


class SimulationConfig(FrozenModel):
    max_time_step_s: float = Field(gt=0)
    max_tcp_step_mm: float = Field(gt=0)
    event_merge_gap_s: float = Field(ge=0)
    batch_size: int = Field(ge=1000)


class ProcessConfig(FrozenModel):
    deposition_speed_mm_s: float = Field(gt=0)
    travel_speed_mm_s: float = Field(gt=0)
    layer_height_mm: float = Field(gt=0)
    bead_width_mm: float = Field(gt=0)
    build_plane_z_mm: float
    tcp_z_reference: Literal["top", "center"]


class CollisionConfig(FrozenModel):
    check_arm_crossing: bool
    check_tcp_radius: bool
    touching_is_collision: bool
    geometry_epsilon_mm: float = Field(gt=0)


class ValidationConfig(FrozenModel):
    wait_position_tolerance_mm: float = Field(ge=0)
    layer_z_tolerance_mm: float = Field(ge=0)
    speed_relative_tolerance: float = Field(ge=0)
    fail_on_speed_violation: bool
    require_watertight_target: bool
    attempt_target_repair: bool
    target_volume_discrepancy_warning_ratio: float = Field(ge=0)


class ShapeValidationConfig(FrozenModel):
    polygon_buffer_resolution: int = Field(ge=1)
    polygon_snap_tolerance_mm: float = Field(gt=0)
    minimum_overall_coverage: float = Field(ge=0, le=1)
    maximum_overall_overfill_ratio: float = Field(ge=0)
    minimum_overall_iou: float = Field(ge=0, le=1)
    minimum_layer_iou: float = Field(ge=0, le=1)
    maximum_failed_layer_ratio: float = Field(ge=0, le=1)
    area_epsilon_mm2: float = Field(gt=0)


class OutputConfig(FrozenModel):
    save_summary_json: bool
    save_report_markdown: bool
    save_collision_events_csv: bool
    save_robot_metrics_csv: bool
    save_layer_metrics_csv: bool
    save_deposited_stl: bool
    save_static_plots: bool
    save_interactive_html: bool
    animation_sample_interval_s: float = Field(gt=0)


class Config(FrozenModel):
    schema_version: Literal["1.0"]
    simulation: SimulationConfig
    robots: tuple[RobotConfig, RobotConfig, RobotConfig]
    process: ProcessConfig
    collision: CollisionConfig
    validation: ValidationConfig
    shape_validation: ShapeValidationConfig
    output: OutputConfig

    @model_validator(mode="after")
    def validate_robot_set(self) -> Config:
        ids = [robot.id for robot in self.robots]
        if sorted(ids) != [1, 2, 3]:
            raise ValueError("robots must contain IDs 1, 2, and 3 exactly once")
        bases = [robot.base_xyz_mm for robot in self.robots]
        epsilon = self.collision.geometry_epsilon_mm
        for index, base_a in enumerate(bases):
            for base_b in bases[index + 1 :]:
                distance = math.dist(base_a, base_b)
                if distance <= epsilon:
                    raise ValueError("robot base coordinates must be distinct")
        return self

    def robot(self, robot_id: int) -> RobotConfig:
        return next(robot for robot in self.robots if robot.id == robot_id)

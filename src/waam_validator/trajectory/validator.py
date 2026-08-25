"""Semantic validation for trajectory intervals."""

from __future__ import annotations

import numpy as np

from ..config.models import Config
from ..constants import EXTREME_COORDINATE_WARNING_MM, MODE_D, MODE_T, MODE_W
from ..errors import InputValidationError, ValidationMessages
from ..models import TrajectorySet
from ..shape.layer_index import determine_layer_index


def validate_trajectory_set(
    trajectories: TrajectorySet,
    config: Config,
) -> ValidationMessages:
    """Validate W movement, D layers, and configured reference speeds."""
    messages = ValidationMessages()
    geometry_epsilon = config.collision.geometry_epsilon_mm
    process = config.process
    validation = config.validation

    for trajectory in trajectories.robots:
        deltas = np.diff(trajectory.xyz_mm.astype(np.float64), axis=0)
        durations = np.diff(trajectory.time_s)
        lengths = np.linalg.norm(deltas, axis=1)
        xy_lengths = np.linalg.norm(deltas[:, :2], axis=1)

        if np.max(np.abs(trajectory.xyz_mm)) > EXTREME_COORDINATE_WARNING_MM:
            messages.warning(
                "EXTREME_COORDINATE_WARNING",
                f"Robot {trajectory.robot_id} contains coordinates above "
                f"{EXTREME_COORDINATE_WARNING_MM:g} mm.",
                robot_id=trajectory.robot_id,
            )

        for index, mode_value in enumerate(trajectory.mode[:-1]):
            mode = int(mode_value)
            start_s = float(trajectory.time_s[index])
            end_s = float(trajectory.time_s[index + 1])
            context = {
                "robot_id": trajectory.robot_id,
                "start_s": start_s,
                "end_s": end_s,
            }
            if mode == int(MODE_W):
                if lengths[index] > validation.wait_position_tolerance_mm:
                    messages.error(
                        "WAIT_POSITION_CHANGED",
                        f"Robot {trajectory.robot_id} moved {lengths[index]:.6g} mm during W.",
                        **context,
                    )
                continue

            speed = float(lengths[index] / durations[index])
            if mode == int(MODE_D):
                if xy_lengths[index] <= geometry_epsilon:
                    raise InputValidationError(
                        "DEPOSITION_ZERO_LENGTH",
                        f"Robot {trajectory.robot_id} has a zero-length D interval "
                        f"at {start_s:.6g} s.",
                    )
                z_start = float(trajectory.xyz_mm[index, 2])
                z_end = float(trajectory.xyz_mm[index + 1, 2])
                if abs(z_end - z_start) > validation.layer_z_tolerance_mm:
                    raise InputValidationError(
                        "DEPOSITION_VERTICAL_MOVE",
                        f"Robot {trajectory.robot_id} D interval at {start_s:.6g} s "
                        "moves vertically beyond layer tolerance.",
                    )
                representative_z = (z_start + z_end) / 2.0
                if representative_z < process.build_plane_z_mm - validation.layer_z_tolerance_mm:
                    raise InputValidationError(
                        "DEPOSITION_BELOW_BUILD_PLANE",
                        f"Robot {trajectory.robot_id} deposits below the build plane.",
                    )
                try:
                    determine_layer_index(representative_z, config)
                except ValueError as exc:
                    raise InputValidationError("DEPOSITION_LAYER_MISMATCH", str(exc)) from exc
                relative_error = abs(speed - process.deposition_speed_mm_s) / (
                    process.deposition_speed_mm_s
                )
                if relative_error > validation.speed_relative_tolerance:
                    _speed_issue(
                        messages,
                        validation.fail_on_speed_violation,
                        "DEPOSITION_SPEED_VIOLATION",
                        f"Robot {trajectory.robot_id} deposition speed {speed:.6g} mm/s "
                        f"differs from reference {process.deposition_speed_mm_s:.6g} mm/s.",
                        context,
                    )
            elif mode == int(MODE_T):
                if lengths[index] <= geometry_epsilon:
                    messages.warning(
                        "STATIONARY_TRAVEL_WARNING",
                        f"Robot {trajectory.robot_id} has stationary T interval at "
                        f"{start_s:.6g} s.",
                        **context,
                    )
                maximum = process.travel_speed_mm_s * (
                    1.0 + validation.speed_relative_tolerance
                )
                if speed > maximum:
                    _speed_issue(
                        messages,
                        validation.fail_on_speed_violation,
                        "TRAVEL_SPEED_VIOLATION",
                        f"Robot {trajectory.robot_id} travel speed {speed:.6g} mm/s "
                        f"exceeds tolerated maximum {maximum:.6g} mm/s.",
                        context,
                    )
    return messages


def _speed_issue(
    messages: ValidationMessages,
    fail: bool,
    code: str,
    message: str,
    context: dict[str, int | float],
) -> None:
    if fail:
        messages.error(code, message, **context)
    else:
        messages.warning(code, message, **context)

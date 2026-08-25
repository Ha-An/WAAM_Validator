"""End-to-end WAAM validation orchestration."""

from __future__ import annotations

import logging
from pathlib import Path

from .collision.simulator import run_collision_analysis
from .config.loader import load_config
from .config.models import Config
from .errors import (
    ComputationError,
    InputValidationError,
    ValidationMessages,
    WaamValidatorError,
)
from .models import ShapeMetrics, ValidationResult
from .reporting.writers import (
    configure_file_logging,
    prepare_output_directory,
    write_error_json,
    write_result_files,
)
from .schedule.metrics import compute_schedule_metrics
from .shape.deposition import build_deposited_layers
from .shape.mesh_export import export_deposited_stl
from .shape.metrics import compute_shape_metrics
from .shape.target import (
    determine_evaluation_layers,
    load_target_mesh,
    slice_target_layers,
    validate_coordinate_consistency,
)
from .trajectory.loader import load_trajectory_csv
from .trajectory.validator import validate_trajectory_set
from .visualization.plots import generate_static_plots
from .visualization.replay import generate_replay_html

LOGGER = logging.getLogger("waam_validator")


def _resolve_input(input_dir: Path) -> tuple[Path, Path, Path, Path]:
    resolved = input_dir.expanduser().resolve()
    if not resolved.is_dir():
        raise InputValidationError(
            "MISSING_CONFIG", f"Input directory does not exist: {resolved}"
        )
    config_path = resolved / "config.yaml"
    trajectory_path = resolved / "trajectory.csv"
    target_path = resolved / "target.stl"
    if not config_path.is_file():
        raise InputValidationError("MISSING_CONFIG", "config.yaml is required.")
    if not trajectory_path.is_file():
        raise InputValidationError("MISSING_TRAJECTORY", "trajectory.csv is required.")
    if not target_path.is_file():
        raise InputValidationError("MISSING_TARGET", "target.stl is required.")
    return resolved, config_path, trajectory_path, target_path


def _build_failure_reasons(
    shape: ShapeMetrics,
    messages: ValidationMessages,
    arm_event_count: int,
    tcp_event_count: int,
    config: Config,
) -> list[str]:
    reasons: list[str] = []
    speed_codes = {"DEPOSITION_SPEED_VIOLATION", "TRAVEL_SPEED_VIOLATION"}
    for issue in messages.errors:
        if issue.code not in speed_codes:
            reasons.append(f"Input/process validation failed: {issue.code} - {issue.message}")
    if arm_event_count:
        reasons.append(f"ARM_CROSS: {arm_event_count} event(s) detected.")
    if tcp_event_count:
        reasons.append(f"TCP_RADIUS: {tcp_event_count} event(s) detected.")
    thresholds = config.shape_validation
    if shape.coverage < thresholds.minimum_overall_coverage:
        reasons.append(
            f"Overall coverage {shape.coverage:.2%} is below minimum "
            f"{thresholds.minimum_overall_coverage:.2%}."
        )
    if shape.overfill_ratio > thresholds.maximum_overall_overfill_ratio:
        reasons.append(
            f"Overall overfill {shape.overfill_ratio:.2%} exceeds maximum "
            f"{thresholds.maximum_overall_overfill_ratio:.2%}."
        )
    if shape.iou < thresholds.minimum_overall_iou:
        reasons.append(
            f"Overall IoU {shape.iou:.2%} is below minimum "
            f"{thresholds.minimum_overall_iou:.2%}."
        )
    if shape.failed_layer_ratio > thresholds.maximum_failed_layer_ratio:
        reasons.append(
            f"Failed-layer ratio {shape.failed_layer_ratio:.2%} exceeds maximum "
            f"{thresholds.maximum_failed_layer_ratio:.2%}."
        )
    speed_violations = sum(issue.code in speed_codes for issue in messages.errors)
    if speed_violations:
        reasons.append(f"Speed validation failed: {speed_violations} violating interval(s).")
    return reasons


def run_validation(
    input_dir: Path,
    output_dir: Path | None = None,
    *,
    headless: bool = False,
) -> ValidationResult:
    """Execute the complete validation pipeline and write configured artifacts."""
    resolved_input = input_dir.expanduser().resolve()
    if not resolved_input.is_dir():
        raise InputValidationError(
            "MISSING_CONFIG", f"Input directory does not exist: {resolved_input}"
        )
    run_output = prepare_output_directory(resolved_input, output_dir)
    configure_file_logging(run_output)
    try:
        resolved_input, config_path, trajectory_path, target_path = _resolve_input(
            resolved_input
        )
        LOGGER.info("Loading configuration")
        config = load_config(config_path)
        LOGGER.info("Loading trajectory")
        trajectories = load_trajectory_csv(trajectory_path, config)
        messages = validate_trajectory_set(trajectories, config)
        LOGGER.info("Loaded %d trajectory rows", trajectories.row_count)

        target_mesh = load_target_mesh(target_path, config, messages)
        validate_coordinate_consistency(trajectories, target_mesh, config)
        LOGGER.info(
            "Loaded target: %d faces, watertight=%s",
            len(target_mesh.faces),
            target_mesh.is_watertight,
        )

        schedule = compute_schedule_metrics(trajectories)
        collision = run_collision_analysis(trajectories, config)
        LOGGER.info(
            "Collision scan: %d samples, %d events",
            collision.sample_count,
            len(collision.events),
        )

        if not config.collision.check_arm_crossing:
            messages.warning("ARM_CROSS_CHECK_DISABLED", "Base–TCP crossing check is disabled.")
        if not config.collision.check_tcp_radius:
            messages.warning("TCP_RADIUS_CHECK_DISABLED", "TCP radius check is disabled.")

        deposited_layers = build_deposited_layers(trajectories, config)
        layer_indices = determine_evaluation_layers(target_mesh, deposited_layers, config)
        target_layers = slice_target_layers(target_mesh, layer_indices, config, messages)
        shape, layer_metrics = compute_shape_metrics(
            deposited_layers,
            target_layers,
            config,
            target_mesh_volume_mm3=float(abs(target_mesh.volume)),
        )
        if (
            shape.target_volume_discrepancy_ratio
            > config.validation.target_volume_discrepancy_warning_ratio
        ):
            messages.warning(
                "TARGET_VOLUME_DISCREPANCY_WARNING",
                f"Target mesh and layer-integrated volumes differ by "
                f"{shape.target_volume_discrepancy_ratio:.2%}.",
            )

        failure_reasons = _build_failure_reasons(
            shape,
            messages,
            collision.arm_cross_event_count,
            collision.tcp_radius_event_count,
            config,
        )
        status = "PASS" if not failure_reasons else "FAIL"
        result = ValidationResult(
            status=status,
            input_dir=resolved_input,
            output_dir=run_output,
            trajectory_rows=trajectories.row_count,
            target_watertight=bool(target_mesh.is_watertight),
            schedule=schedule,
            collision=collision,
            shape=shape,
            layer_metrics=layer_metrics,
            warnings=messages.warnings,
            errors=messages.errors,
            failure_reasons=failure_reasons,
            checks_enabled={
                "arm_crossing": config.collision.check_arm_crossing,
                "tcp_radius": config.collision.check_tcp_radius,
            },
        )

        if config.output.save_deposited_stl:
            export_deposited_stl(deposited_layers, config, run_output / "deposited.stl")
        if not headless and config.output.save_static_plots:
            generate_static_plots(
                trajectories,
                config,
                deposited_layers,
                target_layers,
                collision,
                schedule,
                layer_metrics,
                run_output,
            )
        if not headless and config.output.save_interactive_html:
            generate_replay_html(
                trajectories,
                target_mesh,
                collision,
                config,
                run_output / "replay.html",
            )
        write_result_files(result, config)
        LOGGER.info("Validation completed with status %s", status)
        return result
    except WaamValidatorError as exc:
        exc.output_dir = run_output
        LOGGER.error("%s - %s", exc.code, exc.message)
        write_error_json(run_output, exc, resolved_input)
        raise
    except Exception as exc:
        LOGGER.exception("Unexpected calculation error")
        wrapped = ComputationError("INTERNAL_CALCULATION_ERROR", str(exc), output_dir=run_output)
        write_error_json(run_output, wrapped, resolved_input)
        raise wrapped from exc


def check_input(input_dir: Path) -> tuple[int, bool, int]:
    """Validate required inputs without collision, shape slicing, or output creation."""
    _, config_path, trajectory_path, target_path = _resolve_input(input_dir)
    config = load_config(config_path)
    trajectories = load_trajectory_csv(trajectory_path, config)
    messages = validate_trajectory_set(trajectories, config)
    if messages.errors:
        issue = messages.errors[0]
        raise InputValidationError(issue.code, issue.message)
    mesh = load_target_mesh(target_path, config)
    validate_coordinate_consistency(trajectories, mesh, config)
    return trajectories.row_count, bool(mesh.is_watertight), len(mesh.faces)

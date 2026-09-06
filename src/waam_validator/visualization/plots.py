"""Headless-safe static plots."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.patches import Circle  # noqa: E402
from shapely import difference, intersection, union_all  # noqa: E402
from shapely.geometry import LineString  # noqa: E402
from shapely.geometry.base import BaseGeometry  # noqa: E402

from ..config.models import Config  # noqa: E402
from ..constants import MODE_D, MODE_T, MODE_TO_TEXT  # noqa: E402
from ..errors import ComputationError, OutputWriteError  # noqa: E402
from ..models import (  # noqa: E402
    CollisionSimulationResult,
    LayerMetrics,
    ScheduleMetrics,
    TrajectorySet,
)
from ..shape.polygon_utils import polygon_components  # noqa: E402

_ROBOT_COLORS = {1: "tab:blue", 2: "tab:orange", 3: "tab:green"}
_MODE_COLORS = {"T": "tab:blue", "D": "tab:red", "W": "tab:gray"}


def _draw_geometry(
    axes: Axes,
    geometry: BaseGeometry,
    *,
    facecolor: str = "none",
    edgecolor: str = "black",
    alpha: float = 1.0,
    label: str | None = None,
) -> None:
    first = True
    for polygon in polygon_components(geometry):
        x_values, y_values = polygon.exterior.xy
        axes.fill(
            x_values,
            y_values,
            facecolor=facecolor,
            edgecolor=edgecolor,
            alpha=alpha,
            label=label if first else None,
        )
        first = False
        for interior in polygon.interiors:
            x_hole, y_hole = interior.xy
            axes.fill(x_hole, y_hole, facecolor="white", edgecolor=edgecolor)


def generate_static_plots(
    trajectories: TrajectorySet,
    config: Config,
    deposited_layers: dict[int, BaseGeometry],
    target_layers: dict[int, BaseGeometry],
    collision: CollisionSimulationResult,
    schedule: ScheduleMetrics,
    layer_metrics: list[LayerMetrics],
    output_dir: Path,
) -> None:
    """Generate all mandatory PNG analysis views."""
    try:
        _overview(trajectories, config, target_layers, collision, output_dir)
        _arm_envelope_worst_case(config, collision, output_dir)
        _gantt(trajectories, collision, schedule, output_dir)
        _shape_metrics(layer_metrics, output_dir)
        _worst_layer(deposited_layers, target_layers, layer_metrics, output_dir)
    except OSError as exc:
        raise OutputWriteError("OUTPUT_WRITE_FAILED", str(exc)) from exc
    except Exception as exc:
        raise ComputationError("VISUALIZATION_FAILED", str(exc)) from exc


def _overview(
    trajectories: TrajectorySet,
    config: Config,
    target_layers: dict[int, BaseGeometry],
    collision: CollisionSimulationResult,
    output_dir: Path,
) -> None:
    figure, axes = plt.subplots(figsize=(9, 8), constrained_layout=True)
    target = union_all(list(target_layers.values())) if target_layers else None
    if target is not None:
        _draw_geometry(axes, target, edgecolor="black", alpha=0.6, label="Target outline")
    for trajectory in trajectories.robots:
        color = _ROBOT_COLORS[trajectory.robot_id]
        for mode, style, label in (
            (MODE_D, "solid", f"R{trajectory.robot_id} D"),
            (MODE_T, "dashed", f"R{trajectory.robot_id} T"),
        ):
            mask = trajectory.mode[:-1] == mode
            if not bool(np.any(mask)):
                continue
            starts = trajectory.xyz_mm[:-1, :2][mask]
            ends = trajectory.xyz_mm[1:, :2][mask]
            segments = np.stack((starts, ends), axis=1)
            axes.add_collection(
                LineCollection(
                    segments,  # type: ignore[arg-type]
                    colors=color,
                    linewidths=1.0,
                    linestyles=style,
                    label=label,
                )
            )
        base = config.robot(trajectory.robot_id).base_xyz_mm
        axes.scatter(
            base[0],
            base[1],
            marker="s",
            s=70,
            color=color,
            label=f"R{trajectory.robot_id}",
        )
        final = trajectory.xyz_mm[-1]
        axes.scatter(final[0], final[1], marker="x", s=55, color=color)
    for event in collision.events:
        x_value = (event.closest_a_x_mm + event.closest_b_x_mm) / 2.0
        y_value = (event.closest_a_y_mm + event.closest_b_y_mm) / 2.0
        color = "crimson" if event.collision_type == "ARM_ENVELOPE" else "darkorange"
        axes.scatter(x_value, y_value, marker="*", s=100, color=color)
    axes.set_title("WAAM trajectory and collision overview (XY)")
    axes.set_xlabel("X [mm]")
    axes.set_ylabel("Y [mm]")
    axes.axis("equal")
    axes.grid(True, alpha=0.25)
    axes.legend(loc="best")
    figure.savefig(output_dir / "overview_xy.png", dpi=160)
    plt.close(figure)


def _arm_envelope_worst_case(
    config: Config,
    collision: CollisionSimulationResult,
    output_dir: Path,
) -> None:
    figure, axes = plt.subplots(figsize=(9, 8), constrained_layout=True)
    bases = np.asarray([robot.base_xyz_mm[:2] for robot in config.robots], dtype=np.float64)
    triangle = np.vstack((bases, bases[0]))
    axes.plot(triangle[:, 0], triangle[:, 1], color="#64748b", linestyle=":", label="Base triangle")
    workspace = Circle(
        config.workspace.center_xy_mm,
        config.workspace.radius_mm,
        facecolor="#38bdf8",
        edgecolor="#0ea5e9",
        alpha=0.08,
        label="Workspace",
    )
    axes.add_patch(workspace)
    worst_pair = collision.minimum_arm_pair
    for robot, tcp in zip(config.robots, collision.minimum_arm_tcp_positions_xy, strict=True):
        color = _ROBOT_COLORS[robot.id]
        segment = LineString([robot.base_xyz_mm[:2], tcp])
        physical = segment.buffer(robot.arm_envelope_radius_mm, cap_style="round")
        decision = segment.buffer(
            robot.arm_envelope_radius_mm + config.collision.arm_clearance_mm / 2.0,
            cap_style="round",
        )
        _draw_geometry(
            axes,
            physical,
            facecolor=color,
            edgecolor=color,
            alpha=0.22,
            label=f"R{robot.id} physical Capsule",
        )
        for component_index, polygon in enumerate(polygon_components(decision)):
            x_values, y_values = polygon.exterior.xy
            axes.plot(
                x_values,
                y_values,
                color=color,
                linestyle="--",
                linewidth=1.0,
                label=(f"R{robot.id} decision outline" if component_index == 0 else None),
            )
        axes.plot(
            [robot.base_xyz_mm[0], tcp[0]],
            [robot.base_xyz_mm[1], tcp[1]],
            color=color,
            linewidth=1.5,
        )
        axes.scatter(*robot.base_xyz_mm[:2], marker="^", s=80, color=color)
        axes.scatter(*tcp, marker="o", s=45, color=color)

    closest_a = collision.minimum_arm_closest_a_xy
    closest_b = collision.minimum_arm_closest_b_xy
    if not config.collision.check_arm_envelope:
        status = "DISABLED"
        status_color = "dimgray"
    elif config.collision.touching_is_collision:
        failed = collision.minimum_arm_safety_margin_mm <= config.collision.geometry_epsilon_mm
        status = "FAIL" if failed else "PASS"
        status_color = "crimson" if failed else "forestgreen"
    else:
        failed = collision.minimum_arm_safety_margin_mm < -config.collision.geometry_epsilon_mm
        status = "FAIL" if failed else "PASS"
        status_color = "crimson" if failed else "forestgreen"
    axes.plot(
        [closest_a[0], closest_b[0]],
        [closest_a[1], closest_b[1]],
        color=status_color,
        linewidth=3.0,
        marker="o",
        label="Closest centerline points",
    )
    axes.set_title(
        f"Arm Envelope worst case — {status}\n"
        f"R{worst_pair[0]}-R{worst_pair[1]} at {collision.minimum_arm_time_s:.3f} s | "
        f"distance {collision.arm_centerline_distance_at_worst_mm:.3f} mm | "
        f"required {collision.arm_required_distance_at_worst_mm:.3f} mm | "
        f"margin {collision.minimum_arm_safety_margin_mm:.3f} mm"
    )
    axes.set_xlabel("X [mm]")
    axes.set_ylabel("Y [mm]")
    axes.axis("equal")
    axes.grid(True, alpha=0.25)
    axes.legend(loc="best", fontsize=8)
    figure.savefig(output_dir / "arm_envelope_worst_case.png", dpi=160)
    plt.close(figure)


def _gantt(
    trajectories: TrajectorySet,
    collision: CollisionSimulationResult,
    schedule: ScheduleMetrics,
    output_dir: Path,
) -> None:
    figure, axes = plt.subplots(figsize=(11, 4.5), constrained_layout=True)
    for row, trajectory in enumerate(trajectories.robots):
        intervals: dict[str, list[tuple[float, float]]] = {"T": [], "D": [], "W": []}
        interval_modes = trajectory.mode[:-1]
        start_index = 0
        for end_index in range(1, len(interval_modes) + 1):
            if (
                end_index < len(interval_modes)
                and interval_modes[end_index] == interval_modes[start_index]
            ):
                continue
            label = MODE_TO_TEXT[int(interval_modes[start_index])]
            start = float(trajectory.time_s[start_index])
            end = float(trajectory.time_s[end_index])
            intervals[label].append((start, end - start))
            start_index = end_index
        for label, spans in intervals.items():
            if spans:
                axes.broken_barh(
                    spans,
                    (row - 0.35, 0.7),
                    facecolors=_MODE_COLORS[label],
                )
    for event in collision.events:
        color = "crimson" if event.collision_type == "ARM_ENVELOPE" else "darkorange"
        axes.axvspan(event.start_s, event.end_s, color=color, alpha=0.15)
    axes.set_yticks(range(3), ["Robot 1", "Robot 2", "Robot 3"])
    axes.set_xlim(0, schedule.makespan_s)
    axes.set_xlabel("Time [s]")
    axes.set_title("Robot mode Gantt chart")
    axes.grid(True, axis="x", alpha=0.25)
    figure.savefig(output_dir / "gantt.png", dpi=160)
    plt.close(figure)


def _shape_metrics(layer_metrics: list[LayerMetrics], output_dir: Path) -> None:
    figure, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True, constrained_layout=True)
    layers = [item.layer_index for item in layer_metrics]
    values = (
        ([item.coverage for item in layer_metrics], "Coverage"),
        (
            [0.0 if item.overfill_ratio is None else item.overfill_ratio for item in layer_metrics],
            "Overfill",
        ),
        ([item.iou for item in layer_metrics], "IoU"),
    )
    for axis, (series, label) in zip(axes, values, strict=True):
        axis.plot(layers, series, marker="o", markersize=3)
        axis.set_ylabel(label)
        axis.set_ylim(-0.02, max(1.02, max(series, default=1.0) * 1.05))
        axis.grid(True, alpha=0.25)
    axes[-1].set_xlabel("Layer index")
    figure.suptitle("Shape metrics by layer")
    figure.savefig(output_dir / "shape_metrics_by_layer.png", dpi=160)
    plt.close(figure)


def _worst_layer(
    deposited_layers: dict[int, BaseGeometry],
    target_layers: dict[int, BaseGeometry],
    layer_metrics: list[LayerMetrics],
    output_dir: Path,
) -> None:
    candidates = [item for item in layer_metrics if item.target_area_mm2 > 0]
    figure, axes = plt.subplots(figsize=(8, 8), constrained_layout=True)
    if candidates:
        worst = min(candidates, key=lambda item: (item.iou, item.layer_index))
        target = target_layers[worst.layer_index]
        deposited = deposited_layers.get(worst.layer_index)
        if deposited is None:
            from shapely import Polygon

            deposited = Polygon()
        _draw_geometry(
            axes,
            difference(target, deposited),
            facecolor="tab:blue",
            edgecolor="tab:blue",
            alpha=0.5,
            label="Underfill",
        )
        _draw_geometry(
            axes,
            difference(deposited, target),
            facecolor="tab:red",
            edgecolor="tab:red",
            alpha=0.5,
            label="Overfill",
        )
        _draw_geometry(
            axes,
            intersection(target, deposited),
            facecolor="tab:green",
            edgecolor="tab:green",
            alpha=0.5,
            label="Intersection",
        )
        _draw_geometry(axes, target, edgecolor="black", label="Target boundary")
        _draw_geometry(axes, deposited, edgecolor="orange", label="Deposited boundary")
        axes.set_title(f"Worst target layer: {worst.layer_index} (IoU={worst.iou:.3f})")
        axes.legend(loc="best")
    else:
        axes.text(0.5, 0.5, "No target layer", ha="center", va="center")
    axes.set_xlabel("X [mm]")
    axes.set_ylabel("Y [mm]")
    axes.axis("equal")
    axes.grid(True, alpha=0.25)
    figure.savefig(output_dir / "worst_layer_comparison.png", dpi=160)
    plt.close(figure)

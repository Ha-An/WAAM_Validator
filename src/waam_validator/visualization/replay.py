"""Self-contained Plotly replay generation."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import trimesh

from ..config.models import Config
from ..constants import MODE_D
from ..errors import ComputationError, OutputWriteError
from ..models import CollisionSimulationResult, TrajectorySet
from ..trajectory.interpolation import interpolate_all_states

_COLORS = ("#1f77b4", "#ff7f0e", "#2ca02c")


def _circle(center: np.ndarray, radius: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    angles = np.linspace(0.0, 2.0 * math.pi, 33)
    return (
        center[0] + radius * np.cos(angles),
        center[1] + radius * np.sin(angles),
        np.full_like(angles, center[2]),
    )


def _deposition_segments(trajectories: TrajectorySet) -> list[tuple[float, np.ndarray, np.ndarray]]:
    segments: list[tuple[float, np.ndarray, np.ndarray]] = []
    for trajectory in trajectories.robots:
        for index, mode in enumerate(trajectory.mode[:-1]):
            if int(mode) == int(MODE_D):
                segments.append(
                    (
                        float(trajectory.time_s[index + 1]),
                        trajectory.xyz_mm[index].copy(),
                        trajectory.xyz_mm[index + 1].copy(),
                    )
                )
    return sorted(segments, key=lambda item: item[0])


def _deposition_trace_data(
    segments: list[tuple[float, np.ndarray, np.ndarray]], time_s: float
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    x_values: list[float | None] = []
    y_values: list[float | None] = []
    z_values: list[float | None] = []
    for end_s, start, end in segments:
        if end_s > time_s:
            break
        x_values.extend((float(start[0]), float(end[0]), None))
        y_values.extend((float(start[1]), float(end[1]), None))
        z_values.extend((float(start[2]), float(end[2]), None))
    return x_values, y_values, z_values


def _dynamic_traces(
    time_s: float,
    trajectories: TrajectorySet,
    config: Config,
    collision: CollisionSimulationResult,
    segments: list[tuple[float, np.ndarray, np.ndarray]],
) -> list[go.Scatter3d]:
    xyz, _ = interpolate_all_states(trajectories, time_s)
    active_robots = {
        robot_id
        for event in collision.events
        if event.start_s <= time_s <= event.end_s
        for robot_id in (event.robot_a, event.robot_b)
    }
    traces: list[go.Scatter3d] = []
    for index, robot in enumerate(config.robots):
        base = np.asarray(robot.base_xyz_mm, dtype=np.float64)
        tcp = xyz[robot.id - 1].astype(np.float64)
        color = "crimson" if robot.id in active_robots else _COLORS[index]
        traces.append(
            go.Scatter3d(
                x=[base[0], tcp[0]],
                y=[base[1], tcp[1]],
                z=[base[2], tcp[2]],
                mode="lines+markers",
                line={"color": color, "width": 7},
                marker={"size": 4, "color": color},
                name=f"Robot {robot.id}",
            )
        )
        circle_x, circle_y, circle_z = _circle(tcp, robot.tcp_radius_mm)
        traces.append(
            go.Scatter3d(
                x=circle_x,
                y=circle_y,
                z=circle_z,
                mode="lines",
                line={"color": color, "width": 2},
                name=f"R{robot.id} TCP radius",
                showlegend=False,
            )
        )
    dep_x, dep_y, dep_z = _deposition_trace_data(segments, time_s)
    traces.append(
        go.Scatter3d(
            x=dep_x,
            y=dep_y,
            z=dep_z,
            mode="lines",
            line={"color": "#8c564b", "width": 5},
            name="Completed D path",
        )
    )
    return traces


def generate_replay_html(
    trajectories: TrajectorySet,
    target_mesh: trimesh.Trimesh,
    collision: CollisionSimulationResult,
    config: Config,
    path: Path,
) -> None:
    """Write an offline Plotly timeline with event boundaries preserved."""
    try:
        makespan = max(float(item.time_s[-1]) for item in trajectories.robots)
        interval = config.output.animation_sample_interval_s
        regular = np.arange(0.0, makespan, interval, dtype=np.float64).tolist()
        event_times = [
            value
            for event in collision.events
            for value in (event.start_s, event.end_s)
        ]
        frame_times = sorted(set(regular + event_times + [makespan]))
        segments = _deposition_segments(trajectories)
        vertices = np.asarray(target_mesh.vertices)
        faces = np.asarray(target_mesh.faces)
        target_trace = go.Mesh3d(
            x=vertices[:, 0],
            y=vertices[:, 1],
            z=vertices[:, 2],
            i=faces[:, 0],
            j=faces[:, 1],
            k=faces[:, 2],
            color="lightgray",
            opacity=0.25,
            name="Target",
        )
        initial_dynamic = _dynamic_traces(
            frame_times[0], trajectories, config, collision, segments
        )
        frames = [
            go.Frame(
                name=f"{time_s:.6f}",
                data=_dynamic_traces(time_s, trajectories, config, collision, segments),
                traces=list(range(1, 8)),
            )
            for time_s in frame_times
        ]
        figure = go.Figure(data=[target_trace, *initial_dynamic], frames=frames)
        figure.update_layout(
            title="WAAM three-robot replay",
            scene={
                "xaxis_title": "X [mm]",
                "yaxis_title": "Y [mm]",
                "zaxis_title": "Z [mm]",
                "aspectmode": "data",
            },
            updatemenus=[
                {
                    "type": "buttons",
                    "buttons": [
                        {
                            "label": "Play",
                            "method": "animate",
                            "args": [None, {"frame": {"duration": 80, "redraw": True}}],
                        },
                        {
                            "label": "Pause",
                            "method": "animate",
                            "args": [[None], {"mode": "immediate"}],
                        },
                    ],
                }
            ],
            sliders=[
                {
                    "steps": [
                        {
                            "label": f"{time_s:.2f}",
                            "method": "animate",
                            "args": [
                                [f"{time_s:.6f}"],
                                {"mode": "immediate", "frame": {"redraw": True}},
                            ],
                        }
                        for time_s in frame_times
                    ],
                    "currentvalue": {"prefix": "Time [s]: "},
                }
            ],
        )
        figure.write_html(path, include_plotlyjs=True, full_html=True, auto_play=False)
    except OSError as exc:
        raise OutputWriteError("OUTPUT_WRITE_FAILED", str(exc)) from exc
    except Exception as exc:
        raise ComputationError("VISUALIZATION_FAILED", str(exc)) from exc

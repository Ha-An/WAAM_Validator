"""Compact self-contained Plotly replay generation."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import trimesh

from ..config.models import Config
from ..constants import MODE_D
from ..errors import ComputationError, OutputWriteError
from ..models import CollisionEvent, CollisionSimulationResult, RobotTrajectory, TrajectorySet
from ..trajectory.interpolation import interpolate_all_states

_COLORS = ("#2f8fff", "#ff9d42", "#34d399")
_MAX_REPLAY_FRAMES = 2_000


@dataclass(slots=True, frozen=True)
class ReplayGenerationStats:
    """Small generation summary persisted by the dashboard worker."""

    frame_count: int
    deposition_point_count: int


def _events(
    collision: CollisionSimulationResult | Sequence[CollisionEvent],
) -> Sequence[CollisionEvent]:
    return collision.events if isinstance(collision, CollisionSimulationResult) else collision


def build_replay_frame_times(
    trajectories: TrajectorySet,
    collision: CollisionSimulationResult | Sequence[CollisionEvent],
    interval_s: float,
    *,
    max_frames: int = _MAX_REPLAY_FRAMES,
) -> list[float]:
    """Build bounded regular frames while preserving collision boundaries."""
    if not math.isfinite(interval_s) or interval_s <= 0:
        raise ValueError("Replay frame interval must be a positive finite number.")
    makespan = max(float(item.time_s[-1]) for item in trajectories.robots)
    event_times = {
        float(value)
        for event in _events(collision)
        for value in (event.start_s, event.end_s)
        if 0.0 <= float(value) <= makespan
    }
    essential = {0.0, makespan, *event_times}
    if len(essential) > max_frames:
        raise ValueError(
            f"Collision boundaries require {len(essential):,} frames, exceeding the "
            f"{max_frames:,}-frame safety limit."
        )

    regular = np.arange(0.0, makespan, interval_s, dtype=np.float64).tolist()
    regular_with_essential = {*regular, *essential}
    if len(regular_with_essential) > max_frames:
        raise ValueError(
            f"Replay interval creates {len(regular_with_essential):,} regular frames, "
            f"exceeding the {max_frames:,}-frame safety limit."
        )
    mode_changes: set[float] = set()
    for trajectory in trajectories.robots:
        changed = np.flatnonzero(trajectory.mode[1:] != trajectory.mode[:-1]) + 1
        mode_changes.update(float(trajectory.time_s[index]) for index in changed)
    base_frames = regular_with_essential
    optional = sorted(mode_changes - base_frames)
    mode_budget = min(
        max_frames - len(base_frames),
        max(0, math.ceil(len(base_frames) * 0.1)),
    )
    if len(optional) > mode_budget:
        indices = np.linspace(0, len(optional) - 1, num=mode_budget, dtype=np.int64)
        optional = [optional[int(index)] for index in np.unique(indices)]
    return sorted([*base_frames, *optional])


def _deposition_path(trajectory: RobotTrajectory) -> dict[str, list[float | None]]:
    x_values: list[float | None] = []
    y_values: list[float | None] = []
    z_values: list[float | None] = []
    times: list[float | None] = []
    active = False
    last_d_index = len(trajectory.mode) - 2
    for index, mode in enumerate(trajectory.mode[:-1]):
        if int(mode) != int(MODE_D):
            if active:
                end_time = float(trajectory.time_s[index])
                x_values.append(None)
                y_values.append(None)
                z_values.append(None)
                times.append(end_time)
                active = False
            continue
        if not active:
            start = trajectory.xyz_mm[index]
            x_values.append(round(float(start[0]), 4))
            y_values.append(round(float(start[1]), 4))
            z_values.append(round(float(start[2]), 4))
            times.append(float(trajectory.time_s[index]))
            active = True
        end = trajectory.xyz_mm[index + 1]
        x_values.append(round(float(end[0]), 4))
        y_values.append(round(float(end[1]), 4))
        z_values.append(round(float(end[2]), 4))
        times.append(float(trajectory.time_s[index + 1]))
        if index == last_d_index:
            x_values.append(None)
            y_values.append(None)
            z_values.append(None)
            times.append(float(trajectory.time_s[index + 1]))
    return {"x": x_values, "y": y_values, "z": z_values, "times": times}


def _circle(center: Sequence[float], radius: float) -> tuple[list[float], list[float], list[float]]:
    angles = np.linspace(0.0, 2.0 * math.pi, 33)
    return (
        (float(center[0]) + radius * np.cos(angles)).tolist(),
        (float(center[1]) + radius * np.sin(angles)).tolist(),
        np.full_like(angles, float(center[2])).tolist(),
    )


def _active_colors(time_s: float, events: Sequence[CollisionEvent]) -> list[str]:
    active = {
        robot_id
        for event in events
        if event.start_s <= time_s <= event.end_s
        for robot_id in (event.robot_a, event.robot_b)
    }
    return ["#ff4057" if robot_id in active else _COLORS[robot_id - 1] for robot_id in (1, 2, 3)]


def _post_script(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""
const replay = {encoded};
const graph = document.getElementById('{{plot_id}}');
const controls = document.createElement('div');
controls.style.cssText = 'display:flex;gap:12px;align-items:center;padding:12px 4px;' +
  'color:#dce8f5;font-family:Segoe UI,sans-serif';
controls.innerHTML = '<button type="button" style="background:#19c37d;color:#06130e;' +
  'border:0;border-radius:5px;padding:8px 16px;font-weight:700;cursor:pointer">재생</button>' +
  '<input type="range" min="0" max="' + (replay.times.length - 1) + '" value="0" style="flex:1">' +
  '<strong style="min-width:170px;text-align:right"></strong>';
graph.parentNode.insertBefore(controls, graph);
const playButton = controls.querySelector('button');
const slider = controls.querySelector('input');
const label = controls.querySelector('strong');
let timer = null;
function upperBound(values, target) {{
  let low = 0, high = values.length;
  while (low < high) {{
    const mid = (low + high) >>> 1;
    const value = values[mid] === null ? -Infinity : values[mid];
    if (value <= target) low = mid + 1; else high = mid;
  }}
  return low;
}}
function showFrame(frameIndex) {{
  const index = Math.max(0, Math.min(replay.times.length - 1, Number(frameIndex)));
  const time = replay.times[index];
  const positions = replay.positions[index];
  const colors = replay.colors[index];
  for (let robot = 0; robot < 3; robot += 1) {{
    const base = replay.bases[robot];
    const tcp = positions[robot];
    const armTrace = 1 + robot * 2;
    const circleTrace = armTrace + 1;
    Plotly.restyle(graph, {{x:[[base[0],tcp[0]]],y:[[base[1],tcp[1]]],z:[[base[2],tcp[2]]],
      'line.color':[colors[robot]],'marker.color':[colors[robot]]}}, [armTrace]);
    const cx = [], cy = [], cz = [];
    for (let point = 0; point <= 32; point += 1) {{
      const angle = 2 * Math.PI * point / 32;
      cx.push(tcp[0] + replay.radii[robot] * Math.cos(angle));
      cy.push(tcp[1] + replay.radii[robot] * Math.sin(angle));
      cz.push(tcp[2]);
    }}
    Plotly.restyle(graph, {{x:[cx],y:[cy],z:[cz],'line.color':[colors[robot]]}}, [circleTrace]);
    const path = replay.deposition[robot];
    const end = upperBound(path.times, time);
    Plotly.restyle(graph, {{
      x:[path.x.slice(0,end)],
      y:[path.y.slice(0,end)],
      z:[path.z.slice(0,end)]
    }}, [7 + robot]);
  }}
  slider.value = String(index);
  label.textContent = '시간 ' +
    time.toLocaleString(undefined, {{maximumFractionDigits:1}}) +
    ' s · ' + (index + 1) + '/' + replay.times.length;
}}
slider.addEventListener('input', () => showFrame(slider.value));
playButton.addEventListener('click', () => {{
  if (timer !== null) {{
    clearInterval(timer);
    timer = null;
    playButton.textContent = '재생';
    return;
  }}
  playButton.textContent = '일시정지';
  timer = setInterval(() => {{
    const next = Number(slider.value) + 1;
    if (next >= replay.times.length) {{
      clearInterval(timer);
      timer = null;
      playButton.textContent = '재생';
      return;
    }}
    showFrame(next);
  }}, 100);
}});
showFrame(0);
"""


def generate_replay_html(
    trajectories: TrajectorySet,
    target_mesh: trimesh.Trimesh,
    collision: CollisionSimulationResult | Sequence[CollisionEvent],
    config: Config,
    path: Path,
    *,
    interval_s: float | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> ReplayGenerationStats:
    """Write an offline replay without duplicating the complete D path per frame."""
    try:
        interval = config.output.animation_sample_interval_s if interval_s is None else interval_s
        events = list(_events(collision))
        frame_times = build_replay_frame_times(trajectories, events, interval)
        positions: list[list[list[float]]] = []
        colors: list[list[str]] = []
        total = len(frame_times)
        report_every = max(1, total // 100)
        for index, time_s in enumerate(frame_times, start=1):
            xyz, _ = interpolate_all_states(trajectories, time_s)
            positions.append(np.round(xyz.astype(np.float64), 4).tolist())
            colors.append(_active_colors(time_s, events))
            if progress_callback is not None and (index == total or index % report_every == 0):
                progress_callback(index, total)

        deposition = [_deposition_path(trajectory) for trajectory in trajectories.robots]
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
        bases = [list(map(float, robot.base_xyz_mm)) for robot in config.robots]
        radii = [float(robot.tcp_radius_mm) for robot in config.robots]
        initial = positions[0]
        traces: list[go.BaseTraceType] = [target_trace]
        for index, robot in enumerate(config.robots):
            base = bases[index]
            tcp = initial[index]
            color = colors[0][index]
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
            circle_x, circle_y, circle_z = _circle(tcp, radii[index])
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
        for index, robot in enumerate(config.robots):
            traces.append(
                go.Scatter3d(
                    x=[],
                    y=[],
                    z=[],
                    mode="lines",
                    line={"color": _COLORS[index], "width": 5},
                    name=f"Robot {robot.id} completed D path",
                )
            )
        figure = go.Figure(data=traces)
        figure.update_layout(
            title=f"WAAM three-robot replay · {total:,} frames · {interval:g} s interval",
            paper_bgcolor="#08111d",
            plot_bgcolor="#08111d",
            font={"color": "#dce8f5"},
            scene={
                "xaxis_title": "X [mm]",
                "yaxis_title": "Y [mm]",
                "zaxis_title": "Z [mm]",
                "aspectmode": "data",
            },
            margin={"l": 0, "r": 0, "t": 54, "b": 0},
        )
        payload: dict[str, object] = {
            "times": [round(value, 6) for value in frame_times],
            "positions": positions,
            "colors": colors,
            "bases": bases,
            "radii": radii,
            "deposition": deposition,
        }
        html_text = figure.to_html(
            include_plotlyjs=True,
            full_html=True,
            auto_play=False,
            post_script=_post_script(payload),
        )
        path.write_text(html_text, encoding="utf-8")
        point_count = sum(len(item["x"]) for item in deposition)
        return ReplayGenerationStats(total, point_count)
    except OSError as exc:
        raise OutputWriteError("OUTPUT_WRITE_FAILED", str(exc)) from exc
    except (OutputWriteError, ComputationError):
        raise
    except Exception as exc:
        raise ComputationError("VISUALIZATION_FAILED", str(exc)) from exc

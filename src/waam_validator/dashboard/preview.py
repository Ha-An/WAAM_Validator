"""Bounded, deterministic Plotly previews for one inspected WAAM input set."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import plotly.graph_objects as go
import trimesh

from ..config.models import Config
from ..constants import MODE_D, MODE_T, MODE_W
from ..models import TrajectorySet
from ..schedule.metrics import compute_schedule_metrics
from ..trajectory.reach import compute_reach_metrics

TARGET_FACE_LIMIT: Final = 50_000
TRAJECTORY_POINT_LIMIT_PER_ROBOT: Final = 10_000
_ROBOT_COLORS: Final = {1: "#38bdf8", 2: "#f59e0b", 3: "#a78bfa"}
_MODE_LABELS: Final = {
    int(MODE_D): "Deposition",
    int(MODE_T): "Travel",
    int(MODE_W): "Wait",
}
_MODE_DASH: Final = {int(MODE_D): "solid", int(MODE_T): "dash", int(MODE_W): "dot"}


@dataclass(slots=True, frozen=True)
class InputPreview:
    scene: go.Figure
    gantt_figure: go.Figure
    time_figure: go.Figure
    motion_figure: go.Figure
    reach_figure: go.Figure
    warnings: list[str]
    target_faces_shown: int
    target_faces_total: int
    trajectory_points_shown: dict[int, int]


def _bounded_interval_indices(mode: np.ndarray, max_intervals: int) -> np.ndarray:
    count = max(0, len(mode) - 1)
    if count <= max_intervals:
        return np.arange(count, dtype=np.int64)
    changes = np.flatnonzero(mode[1:-1] != mode[:-2]) + 1
    mandatory = np.unique(
        np.clip(np.concatenate(([0, count - 1], changes - 1, changes)), 0, count - 1)
    )
    if len(mandatory) >= max_intervals:
        positions = np.linspace(0, len(mandatory) - 1, max_intervals, dtype=np.int64)
        return mandatory[positions]
    remaining = np.setdiff1d(np.arange(count, dtype=np.int64), mandatory, assume_unique=True)
    needed = max_intervals - len(mandatory)
    positions = np.linspace(0, len(remaining) - 1, needed, dtype=np.int64)
    return np.sort(np.concatenate((mandatory, remaining[positions])))


def _preview_mesh(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray, list[str]]:
    warnings: list[str] = []
    if len(mesh.faces) <= TARGET_FACE_LIMIT:
        return (
            np.asarray(mesh.vertices, dtype=np.float64),
            np.asarray(mesh.faces, dtype=np.int64),
            warnings,
        )
    try:
        simplified = mesh.simplify_quadric_decimation(face_count=TARGET_FACE_LIMIT)
        if not 0 < len(simplified.faces) <= TARGET_FACE_LIMIT:
            raise ValueError("mesh simplifier did not honor the preview face limit")
        warnings.append(
            f"Target preview를 {len(mesh.faces):,} face에서 "
            f"{len(simplified.faces):,} face로 단순화했습니다."
        )
        return (
            np.asarray(simplified.vertices, dtype=np.float64),
            np.asarray(simplified.faces, dtype=np.int64),
            warnings,
        )
    except Exception:
        selected = np.linspace(0, len(mesh.faces) - 1, TARGET_FACE_LIMIT, dtype=np.int64)
        faces = np.asarray(mesh.faces[selected], dtype=np.int64)
        used, inverse = np.unique(faces.reshape(-1), return_inverse=True)
        warnings.append("Target preview 단순화에 실패하여 결정론적 face sampling을 사용했습니다.")
        return (
            np.asarray(mesh.vertices[used], dtype=np.float64),
            inverse.reshape((-1, 3)),
            warnings,
        )


def _workspace_trace(config: Config) -> go.Mesh3d:
    theta = np.linspace(0.0, 2.0 * math.pi, 73)
    center_x, center_y = config.workspace.center_xy_mm
    radius = config.workspace.radius_mm
    x = np.concatenate(([center_x], center_x + radius * np.cos(theta)))
    y = np.concatenate(([center_y], center_y + radius * np.sin(theta)))
    z = np.full_like(x, config.process.build_plane_z_mm)
    indices = np.arange(1, len(theta) + 1, dtype=np.int64)
    return go.Mesh3d(
        x=x,
        y=y,
        z=z,
        i=np.zeros(len(indices) - 1, dtype=np.int64),
        j=indices[:-1],
        k=indices[1:],
        name="Workspace",
        color="#22c55e",
        opacity=0.16,
        hovertemplate="Workspace<extra></extra>",
    )


def _reach_trace(robot_id: int, base: np.ndarray, radius: float) -> go.Surface:
    longitude, latitude = np.meshgrid(
        np.linspace(0.0, 2.0 * math.pi, 33),
        np.linspace(-math.pi / 2.0, math.pi / 2.0, 17),
    )
    color = _ROBOT_COLORS[robot_id]
    return go.Surface(
        x=base[0] + radius * np.cos(latitude) * np.cos(longitude),
        y=base[1] + radius * np.cos(latitude) * np.sin(longitude),
        z=base[2] + radius * np.sin(latitude),
        surfacecolor=np.zeros_like(longitude),
        colorscale=[[0.0, color], [1.0, color]],
        cmin=0.0,
        cmax=1.0,
        name=f"R{robot_id} reach",
        opacity=0.09,
        showscale=False,
        showlegend=True,
        hoverinfo="skip",
    )


def _scene(
    config: Config, trajectories: TrajectorySet, mesh: trimesh.Trimesh
) -> tuple[go.Figure, list[str], int, dict[int, int]]:
    vertices, faces, warnings = _preview_mesh(mesh)
    figure = go.Figure()
    figure.add_trace(
        go.Mesh3d(
            x=vertices[:, 0],
            y=vertices[:, 1],
            z=vertices[:, 2],
            i=faces[:, 0],
            j=faces[:, 1],
            k=faces[:, 2],
            name="Target STL",
            color="#cbd5e1",
            opacity=0.34,
            flatshading=True,
            hoverinfo="skip",
        )
    )
    figure.add_trace(_workspace_trace(config))
    bases = np.asarray([robot.base_xyz_mm for robot in config.robots], dtype=np.float64)
    closed_bases = np.vstack((bases, bases[0]))
    figure.add_trace(
        go.Scatter3d(
            x=closed_bases[:, 0],
            y=closed_bases[:, 1],
            z=closed_bases[:, 2],
            mode="lines",
            name="Robot base triangle",
            line={"color": "#64748b", "width": 3, "dash": "dot"},
            hoverinfo="skip",
        )
    )
    points_shown: dict[int, int] = {}
    max_intervals = max(1, TRAJECTORY_POINT_LIMIT_PER_ROBOT // 3)
    for trajectory in trajectories.robots:
        robot = config.robot(trajectory.robot_id)
        base = np.asarray(robot.base_xyz_mm, dtype=np.float64)
        figure.add_trace(
            go.Scatter3d(
                x=[base[0]],
                y=[base[1]],
                z=[base[2]],
                mode="markers+text",
                name=f"R{robot.id} base",
                text=[f"R{robot.id}"],
                textposition="top center",
                marker={"size": 8, "color": _ROBOT_COLORS[robot.id], "symbol": "diamond"},
                hovertemplate=(
                    f"R{robot.id} Base<br>로봇 설치 기준점"
                    f"<br>X {base[0]:,.2f} mm · Y {base[1]:,.2f} mm · Z {base[2]:,.2f} mm"
                    "<extra></extra>"
                ),
            )
        )
        if robot.home_xyz_mm is not None:
            home = np.asarray(robot.home_xyz_mm, dtype=np.float64)
            figure.add_trace(
                go.Scatter3d(
                    x=[home[0]],
                    y=[home[1]],
                    z=[home[2]],
                    mode="markers+text",
                    name=f"R{robot.id} home TCP",
                    text=[f"R{robot.id} Home"],
                    textposition="bottom center",
                    marker={
                        "size": 7,
                        "color": _ROBOT_COLORS[robot.id],
                        "symbol": "x",
                    },
                    hovertemplate=(
                        f"R{robot.id} Home TCP<br>공구 기준점의 명목 대기 위치"
                        f"<br>X {home[0]:,.2f} mm · Y {home[1]:,.2f} mm · Z {home[2]:,.2f} mm"
                        "<extra></extra>"
                    ),
                )
            )
        figure.add_trace(_reach_trace(robot.id, base, robot.reach_radius_mm))
        selected = _bounded_interval_indices(trajectory.mode, max_intervals)
        points_shown[robot.id] = min(TRAJECTORY_POINT_LIMIT_PER_ROBOT, int(len(selected) * 3))
        for mode_value in (int(MODE_D), int(MODE_T), int(MODE_W)):
            x: list[float | None] = []
            y: list[float | None] = []
            z: list[float | None] = []
            for index in selected[trajectory.mode[selected] == mode_value]:
                segment = trajectory.xyz_mm[index : index + 2]
                x.extend([float(segment[0, 0]), float(segment[1, 0]), None])
                y.extend([float(segment[0, 1]), float(segment[1, 1]), None])
                z.extend([float(segment[0, 2]), float(segment[1, 2]), None])
            if x:
                figure.add_trace(
                    go.Scatter3d(
                        x=x,
                        y=y,
                        z=z,
                        mode="lines+markers" if mode_value == int(MODE_W) else "lines",
                        name=f"R{robot.id} {_MODE_LABELS[mode_value]}",
                        line={
                            "color": _ROBOT_COLORS[robot.id],
                            "width": 5 if mode_value == int(MODE_D) else 2,
                            "dash": _MODE_DASH[mode_value],
                        },
                        marker={"size": 2, "color": _ROBOT_COLORS[robot.id]},
                        opacity=1.0 if mode_value == int(MODE_D) else 0.65,
                        hoverinfo="skip",
                    )
                )
    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor="#07101b",
        plot_bgcolor="#07101b",
        height=680,
        margin={"l": 0, "r": 0, "t": 20, "b": 0},
        legend={"orientation": "h", "y": 1.02, "x": 0.0},
        scene={
            "aspectmode": "data",
            "xaxis_title": "X [mm]",
            "yaxis_title": "Y [mm]",
            "zaxis_title": "Z [mm]",
            "bgcolor": "#07101b",
        },
    )
    return figure, warnings, len(faces), points_shown


def _duration_label(seconds: float) -> str:
    hours, remainder = divmod(int(round(seconds)), 3600)
    minutes, seconds_rounded = divmod(remainder, 60)
    return f"{hours:,}시간 {minutes:02d}분 {seconds_rounded:02d}초"


def _statistics_figures(
    config: Config, trajectories: TrajectorySet
) -> tuple[go.Figure, go.Figure, go.Figure]:
    schedule = compute_schedule_metrics(trajectories)
    reach = compute_reach_metrics(trajectories, config)
    labels = [f"R{item.robot_id}" for item in schedule.robots]
    times = go.Figure()
    for name, field, color in (
        ("Deposition", "deposition_time_s", "#f97316"),
        ("Travel", "travel_time_s", "#38bdf8"),
        ("Wait", "wait_time_s", "#64748b"),
    ):
        seconds = [float(getattr(item, field)) for item in schedule.robots]
        percentages = [
            value / schedule.makespan_s * 100.0 if schedule.makespan_s > 0 else 0.0
            for value in seconds
        ]
        times.add_bar(
            x=labels,
            y=percentages,
            name=name,
            marker_color=color,
            text=[f"{value:.1f}%" for value in percentages],
            textposition="inside",
            customdata=seconds,
            hovertemplate=(
                f"%{{x}}<br>비율 %{{y:.2f}}%<br>시간 %{{customdata:,.2f}} s<extra>{name}</extra>"
            ),
        )
    inactive_seconds = [
        max(0.0, schedule.makespan_s - item.completion_s) for item in schedule.robots
    ]
    inactive_percentages = [
        value / schedule.makespan_s * 100.0 if schedule.makespan_s > 0 else 0.0
        for value in inactive_seconds
    ]
    times.add_bar(
        x=labels,
        y=inactive_percentages,
        name="완료 후 비활성",
        marker_color="#334155",
        text=[f"{value:.1f}%" for value in inactive_percentages],
        textposition="inside",
        customdata=inactive_seconds,
        hovertemplate=(
            "%{x}<br>비율 %{y:.2f}%<br>시간 %{customdata:,.2f} s"
            "<extra>완료 후 비활성</extra>"
        ),
    )
    times.update_layout(
        template="plotly_dark",
        barmode="stack",
        height=320,
        margin={"l": 55, "r": 15, "t": 50, "b": 40},
        yaxis={"title": "Makespan 대비 비율 [%]", "range": [0, 100], "ticksuffix": "%"},
        uniformtext={"mode": "show", "minsize": 10},
        annotations=[
            {
                "text": (
                    f"기준 Makespan: {schedule.makespan_s:,.2f} s "
                    f"({_duration_label(schedule.makespan_s)}) = 100%"
                ),
                "xref": "paper",
                "yref": "paper",
                "x": 1.0,
                "y": 1.16,
                "xanchor": "right",
                "showarrow": False,
                "font": {"color": "#aebed0", "size": 12},
            }
        ],
        paper_bgcolor="#0d1927",
        plot_bgcolor="#0d1927",
    )
    motion = go.Figure()
    for name, length_field, time_field, speed_field, color in (
        (
            "적층 거리 [mm]",
            "deposition_length_mm",
            "deposition_time_s",
            "mean_deposition_speed_mm_s",
            "#f97316",
        ),
        (
            "이동 거리 [mm]",
            "travel_length_mm",
            "travel_time_s",
            "mean_travel_speed_mm_s",
            "#38bdf8",
        ),
    ):
        motion.add_bar(
            x=labels,
            y=[float(getattr(item, length_field)) for item in schedule.robots],
            name=name,
            marker_color=color,
            customdata=[
                [
                    float(getattr(item, time_field)),
                    float(getattr(item, speed_field) or 0.0),
                ]
                for item in schedule.robots
            ],
            hovertemplate=(
                "%{x}<br>거리 %{y:,.2f} mm"
                "<br>시간 %{customdata[0]:,.2f} s"
                "<br>평균 속도 %{customdata[1]:,.2f} mm/s<extra>"
                f"{name}</extra>"
            ),
        )
    motion.update_layout(
        template="plotly_dark",
        barmode="group",
        height=320,
        margin={"l": 55, "r": 15, "t": 50, "b": 40},
        yaxis_title="거리 [mm]",
        annotations=[
            {
                "text": (
                    f"설정 속도: Deposition {config.process.deposition_speed_mm_s:g} mm/s · "
                    f"Travel {config.process.travel_speed_mm_s:g} mm/s"
                ),
                "xref": "paper",
                "yref": "paper",
                "x": 1.0,
                "y": 1.16,
                "xanchor": "right",
                "showarrow": False,
                "font": {"color": "#aebed0", "size": 12},
            }
        ],
        paper_bgcolor="#0d1927",
        plot_bgcolor="#0d1927",
    )
    reach_figure = go.Figure()
    reach_percentages = [item.utilization_ratio * 100.0 for item in reach.robots]
    reach_figure.add_bar(
        x=reach_percentages,
        y=labels,
        orientation="h",
        marker_color=["#22c55e" if value <= 100.0 else "#ef4444" for value in reach_percentages],
        text=[f"{value:.1f}%" for value in reach_percentages],
        textposition="inside",
        customdata=[
            [
                item.maximum_reach_mm,
                item.reach_radius_mm,
                item.minimum_margin_mm,
                item.violation_point_count,
            ]
            for item in reach.robots
        ],
        hovertemplate=(
            "%{y}<br>Reach 사용률 %{x:.2f}%"
            "<br>최대 Base–TCP 거리 %{customdata[0]:,.2f} mm"
            "<br>설정 Reach %{customdata[1]:,.2f} mm"
            "<br>최소 여유 %{customdata[2]:,.2f} mm"
            "<br>위반 절점 %{customdata[3]:,.0f}개<extra></extra>"
        ),
        showlegend=False,
    )
    reach_maximum = max([110.0, *(value * 1.1 for value in reach_percentages)])
    reach_figure.add_vline(
        x=100.0,
        line_dash="dash",
        line_color="#f59e0b",
        annotation_text="설정 Reach 한계 100%",
        annotation_position="top left",
    )
    reach_figure.update_layout(
        template="plotly_dark",
        height=320,
        margin={"l": 55, "r": 15, "t": 45, "b": 40},
        xaxis={"title": "Reach 사용률 [%]", "range": [0, reach_maximum], "ticksuffix": "%"},
        yaxis={"autorange": "reversed"},
        paper_bgcolor="#0d1927",
        plot_bgcolor="#0d1927",
    )
    return times, motion, reach_figure


def _gantt_figure(trajectories: TrajectorySet) -> go.Figure:
    """Build one duration-accurate, state-changing horizontal bar per robot."""
    makespan = max(float(item.time_s[-1]) for item in trajectories.robots)
    state_specs = (
        (int(MODE_D), "Deposition · 적층", "#f97316"),
        (int(MODE_T), "Travel · 비적층 이동", "#38bdf8"),
        (int(MODE_W), "Wait · 위치 유지 대기", "#64748b"),
        (None, "완료 후 비활성 · 작업 종료", "#1e293b"),
    )
    segments: dict[int | None, tuple[list[float], list[float], list[float], list[str]]] = {
        mode_value: ([], [], [], [])
        for mode_value, _, _ in state_specs
    }
    for trajectory in trajectories.robots:
        interval_modes = trajectory.mode[:-1]
        changes = np.flatnonzero(interval_modes[1:] != interval_modes[:-1]) + 1
        run_starts = np.concatenate((np.array([0]), changes))
        run_stops = np.concatenate((changes, np.array([len(interval_modes)])))
        robot_label = f"R{trajectory.robot_id}"
        for start_raw, stop_raw in zip(run_starts, run_stops, strict=True):
            start_index = int(start_raw)
            stop_index = int(stop_raw)
            start_s = float(trajectory.time_s[start_index])
            end_s = float(trajectory.time_s[stop_index])
            starts, ends, durations, robots = segments[int(interval_modes[start_index])]
            starts.append(start_s)
            ends.append(end_s)
            durations.append(end_s - start_s)
            robots.append(robot_label)

        completion_s = float(trajectory.time_s[-1])
        if completion_s < makespan:
            starts, ends, durations, robots = segments[None]
            starts.append(completion_s)
            ends.append(makespan)
            durations.append(makespan - completion_s)
            robots.append(robot_label)

    figure = go.Figure()
    for mode_value, name, color in state_specs:
        starts, ends, durations, robots = segments[mode_value]
        display_starts = starts if starts else [0.0]
        display_ends = ends if ends else [0.0]
        display_durations = durations if durations else [0.0]
        display_robots = robots if robots else ["R1"]
        customdata = np.column_stack((display_starts, display_ends, display_durations))
        figure.add_bar(
            x=display_durations,
            base=display_starts,
            y=display_robots,
            orientation="h",
            width=0.66,
            name=name,
            marker={"color": color, "line": {"color": "#0d1927", "width": 0.25}},
            customdata=customdata,
            hovertemplate=(
                "%{y}<br>상태: "
                + name
                + "<br>시작 %{customdata[0]:,.2f} s"
                "<br>종료 %{customdata[1]:,.2f} s"
                "<br>지속시간 %{customdata[2]:,.2f} s<extra></extra>"
            ),
        )

    active_durations = segments[int(MODE_D)][2] + segments[int(MODE_T)][2]
    typical_active_duration = (
        float(np.percentile(active_durations, 75)) if active_durations else makespan
    )
    detail_window_s = min(makespan, max(300.0, typical_active_duration * 80.0))
    first_active_times: list[float] = []
    for trajectory in trajectories.robots:
        active_indices = np.flatnonzero(
            (trajectory.mode[:-1] == MODE_D) | (trajectory.mode[:-1] == MODE_T)
        )
        if len(active_indices):
            first_active_times.append(float(trajectory.time_s[int(active_indices[0])]))
    focus_time_s = max(first_active_times, default=0.0)
    detail_start_s = min(
        max(0.0, focus_time_s - detail_window_s * 0.1),
        max(0.0, makespan - detail_window_s),
    )
    detail_end_s = detail_start_s + detail_window_s
    figure.update_layout(
        template="plotly_dark",
        barmode="overlay",
        height=420,
        margin={"l": 65, "r": 20, "t": 105, "b": 55},
        xaxis={
            "range": [detail_start_s, detail_end_s],
            "title": "시간 [s]",
            "rangeslider": {"visible": True, "thickness": 0.12},
        },
        yaxis={
            "autorange": "reversed",
            "categoryorder": "array",
            "categoryarray": [f"R{item.robot_id}" for item in trajectories.robots],
            "fixedrange": True,
        },
        legend={
            "orientation": "h",
            "x": 0.0,
            "xanchor": "left",
            "y": 1.08,
            "yanchor": "bottom",
        },
        updatemenus=[
            {
                "type": "buttons",
                "direction": "left",
                "x": 1.0,
                "xanchor": "right",
                "y": 1.24,
                "yanchor": "top",
                "showactive": False,
                "bgcolor": "#19304c",
                "bordercolor": "#5caeff",
                "font": {"color": "#f3f8ff"},
                "buttons": [
                    {
                        "label": "상세 보기",
                        "method": "relayout",
                        "args": [{"xaxis.range": [detail_start_s, detail_end_s]}],
                    },
                    {
                        "label": "전체 보기",
                        "method": "relayout",
                        "args": [{"xaxis.range": [0.0, makespan]}],
                    },
                ],
            }
        ],
        hovermode="closest",
        paper_bgcolor="#0d1927",
        plot_bgcolor="#0d1927",
    )
    return figure


def build_input_preview(config: Config, trajectories: TrajectorySet, mesh: Any) -> InputPreview:
    """Build bounded input preview figures without modifying validation inputs."""
    scene, warnings, shown_faces, shown_points = _scene(config, trajectories, mesh)
    gantt_figure = _gantt_figure(trajectories)
    time_figure, motion_figure, reach_figure = _statistics_figures(config, trajectories)
    return InputPreview(
        scene=scene,
        gantt_figure=gantt_figure,
        time_figure=time_figure,
        motion_figure=motion_figure,
        reach_figure=reach_figure,
        warnings=warnings,
        target_faces_shown=shown_faces,
        target_faces_total=int(len(mesh.faces)),
        trajectory_points_shown=shown_points,
    )

"""Dash application for browsing and running local WAAM validation jobs."""

from __future__ import annotations

import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote
from urllib.request import urlopen

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html, no_update
from flask import abort, send_file
from plotly.subplots import make_subplots

from .data import (
    DashboardDataError,
    JobRecord,
    RunRecord,
    discover_jobs,
    load_latest_run,
    load_thresholds,
    read_csv_page,
    read_csv_records,
    resolve_artifact,
    resolve_job,
)
from .input_inspector import (
    inspect_dashboard_inputs,
    inspection_is_current,
    resolve_dashboard_inputs,
)
from .replay_service import estimate_replay, preset_interval_s, read_replay_manifest
from .runner import (
    ReplayAlreadyRunningError,
    ReplayRunManager,
    ValidationAlreadyRunningError,
    ValidationRunManager,
)

JsonDict = dict[str, Any]
DataTable: Any = dash_table.DataTable  # type: ignore[attr-defined]
_GRAPH_CONFIG: Any = {
    "displaylogo": False,
    "responsive": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
}
_STAGE_LABELS = {
    "idle": "실행 대기",
    "starting": "프로세스 준비",
    "loading_inputs": "입력 로딩",
    "collision": "충돌 검사",
    "shape": "형상 검사",
    "artifacts": "산출물 생성",
    "completed": "검증 완료",
    "failed": "검증 오류",
    "process_exit": "프로세스 종료",
}
_ARTIFACT_LABELS = {
    "summary.json": "Summary JSON",
    "validation_report.md": "검증 보고서",
    "robot_metrics.csv": "로봇 지표 CSV",
    "collision_events.csv": "충돌 이벤트 CSV",
    "layer_metrics.csv": "Layer 지표 CSV",
    "warnings.csv": "경고 CSV",
    "deposited.stl": "적층 형상 STL",
    "run.log": "실행 로그",
    "overview_xy.png": "XY 경로 이미지",
    "gantt.png": "Gantt 이미지",
    "shape_metrics_by_layer.png": "Layer 지표 이미지",
    "worst_layer_comparison.png": "최악 Layer 비교 이미지",
    "replay.html": "3D Replay",
    "replay_manifest.json": "Replay 생성 정보",
    "error.json": "Error JSON",
}


def _section(payload: JsonDict, name: str) -> JsonDict:
    value = payload.get(name)
    return value if isinstance(value, dict) else {}


def _number(payload: JsonDict, section: str, field: str) -> float | None:
    raw = _section(payload, section).get(field)
    return float(raw) if isinstance(raw, int | float) else None


def _metric_card(label: str, value: str, detail: str = "") -> html.Div:
    return html.Div(
        [
            html.Div(label, className="metric-label"),
            html.Div(value, className="metric-value"),
            html.Div(detail, className="metric-detail"),
        ],
        className="metric-card",
    )


def _style_figure(figure: go.Figure, *, height: int = 330) -> go.Figure:
    figure.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#dce8f5", "family": "Segoe UI, Malgun Gothic, sans-serif"},
        legend={"orientation": "h", "y": 1.12, "x": 0},
        margin={"l": 56, "r": 24, "t": 54, "b": 48},
        height=height,
        hoverlabel={"bgcolor": "#122338", "font_color": "#e8f1fb"},
    )
    figure.update_xaxes(gridcolor="#203249", zerolinecolor="#314861")
    figure.update_yaxes(gridcolor="#203249", zerolinecolor="#314861")
    return figure


def _empty_figure(message: str, *, height: int = 330) -> go.Figure:
    figure = go.Figure()
    figure.add_annotation(text=message, showarrow=False, font={"color": "#8fa3b8"})
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    return _style_figure(figure, height=height)


def _status_result(
    job: JobRecord | None,
) -> tuple[RunRecord | None, JsonDict, str | None]:
    if job is None:
        return None, {}, "검증 가능한 작업이 없습니다."
    try:
        run = load_latest_run(job.path)
    except DashboardDataError as exc:
        return None, {}, str(exc)
    if run is None:
        return None, {}, None
    return run, run.payload, run.load_error


def _artifact_url(job: JobRecord, run: RunRecord, filename: str) -> str:
    return "/artifacts/{}/{}/{}".format(
        quote(job.name, safe=""),
        quote(run.run_name, safe=""),
        quote(filename, safe=""),
    )


def _summary_content(run: RunRecord, payload: JsonDict) -> html.Div:
    if run.status == "ERROR":
        return html.Div(
            [
                html.P(str(payload.get("code", "VALIDATION_ERROR")), className="error-code"),
                html.P(str(payload.get("message", "검증 실행 중 오류가 발생했습니다."))),
            ],
            className="verdict-copy",
        )
    reasons = payload.get("failure_reasons", [])
    warnings = payload.get("warnings", [])
    errors = payload.get("errors", [])
    if isinstance(reasons, list) and reasons:
        headline = "검증 기준을 충족하지 못했습니다."
        body: Any = html.Ol([html.Li(str(item)) for item in reasons])
        tone = "fail-copy"
    else:
        headline = "모든 활성 검증 기준을 충족했습니다."
        body = html.P("Failure reason이 없습니다.", className="muted-copy")
        tone = "success-copy"
    warning_count = len(warnings) if isinstance(warnings, list) else 0
    error_count = len(errors) if isinstance(errors, list) else 0
    return html.Div(
        [
            html.P(headline, className=tone),
            body,
            html.Div(
                [
                    html.Span(f"경고 {warning_count}건"),
                    html.Span(f"비치명 오류 {error_count}건"),
                ],
                className="inline-stats",
            ),
        ],
        className="verdict-copy",
    )


def _threshold_content(thresholds: JsonDict) -> list[html.Div | html.P]:
    if "error" in thresholds:
        return [html.P(str(thresholds["error"]), className="error-code")]
    rows = [
        ("전체 Coverage ≥", thresholds.get("minimum_overall_coverage")),
        ("전체 Overfill ≤", thresholds.get("maximum_overall_overfill_ratio")),
        ("전체 IoU ≥", thresholds.get("minimum_overall_iou")),
        ("Layer IoU ≥", thresholds.get("minimum_layer_iou")),
        ("실패 Layer 비율 ≤", thresholds.get("maximum_failed_layer_ratio")),
    ]
    return [
        html.Div(
            [html.Span(label), html.Strong(f"{float(value):.2%}")],
            className="threshold-row",
        )
        for label, value in rows
        if isinstance(value, int | float)
    ]


def _robot_figure(rows: list[JsonDict]) -> go.Figure:
    if not rows:
        return _empty_figure("robot_metrics.csv가 없습니다")
    robots = [f"R{int(row['robot_id'])}" for row in rows]
    figure = go.Figure()
    for field, name, color in (
        ("deposition_time_s", "Deposition", "#f97316"),
        ("travel_time_s", "Travel", "#38bdf8"),
        ("wait_time_s", "Wait", "#637589"),
    ):
        figure.add_bar(
            y=robots,
            x=[float(row.get(field, 0.0) or 0.0) for row in rows],
            name=name,
            orientation="h",
            marker_color=color,
            hovertemplate="%{y}<br>%{x:,.2f} s<extra>" + name + "</extra>",
        )
    figure.update_layout(barmode="stack", xaxis_title="누적 시간 [s]", yaxis_title="Robot")
    return _style_figure(figure)


def _optional_number(value: object) -> str:
    return "—" if not isinstance(value, int | float) else f"{float(value):,.2f} mm/s"


def _robot_table(rows: list[JsonDict]) -> html.Div:
    if not rows:
        return html.Div("표시할 로봇 지표가 없습니다.", className="empty-state")
    headers = [
        "Robot",
        "완료 [s]",
        "Deposition 거리 [mm]",
        "Travel 거리 [mm]",
        "Deposition 속도",
        "Travel 속도",
    ]
    body = []
    for row in rows:
        body.append(
            html.Tr(
                [
                    html.Td(f"R{int(row['robot_id'])}"),
                    html.Td(f"{float(row.get('completion_s', 0.0)):,.2f}"),
                    html.Td(f"{float(row.get('deposition_length_mm', 0.0)):,.2f}"),
                    html.Td(f"{float(row.get('travel_length_mm', 0.0)):,.2f}"),
                    html.Td(_optional_number(row.get("mean_deposition_speed_mm_s"))),
                    html.Td(_optional_number(row.get("mean_travel_speed_mm_s"))),
                ]
            )
        )
    return html.Div(
        html.Table(
            [html.Thead(html.Tr([html.Th(item) for item in headers])), html.Tbody(body)],
            className="data-table compact-table",
        ),
        className="table-scroll",
    )


def _collision_gauge(payload: JsonDict) -> go.Figure:
    distance = _number(payload, "collision", "minimum_tcp_distance_mm")
    required = _number(payload, "collision", "minimum_required_distance_mm")
    if distance is None or required is None:
        return _empty_figure("TCP 거리 지표가 없습니다", height=300)
    maximum = max(distance, required) * 1.35 or 1.0
    collision = payload.get("collision", {})
    collision_data = collision if isinstance(collision, dict) else {}
    checks = collision_data.get("checks_enabled", {})
    tcp_enabled = bool(checks.get("tcp_radius", True)) if isinstance(checks, dict) else True
    tcp_events = int(collision_data.get("tcp_radius_event_count", 0) or 0)
    passed = tcp_events == 0
    bar_color = "#21d4a3" if passed else "#ff6376"
    if not tcp_enabled:
        bar_color = "#8fa3b8"
    figure = go.Figure(
        go.Indicator(
            mode="gauge+number+delta",
            value=distance,
            number={"suffix": " mm", "font": {"color": "#e8f1fb"}},
            delta={"reference": required, "suffix": " mm"},
            title={"text": "최소 TCP 거리", "font": {"color": "#8fa3b8"}},
            gauge={
                "axis": {"range": [0, maximum], "tickcolor": "#8fa3b8"},
                "bar": {"color": bar_color},
                "bgcolor": "#0d1927",
                "bordercolor": "#20344b",
                "steps": [
                    {"range": [0, required], "color": "rgba(255,99,118,.16)"},
                    {"range": [required, maximum], "color": "rgba(33,212,163,.08)"},
                ],
                "threshold": {
                    "line": {"color": "#f5b942", "width": 4},
                    "thickness": 0.8,
                    "value": required,
                },
            },
        )
    )
    return _style_figure(figure, height=300)


def _collision_timeline(rows: list[JsonDict]) -> go.Figure:
    if not rows:
        return _empty_figure("충돌 이벤트 없음", height=300)
    limited = rows[:1000]
    labels = [
        f"{row.get('type', 'EVENT')} · R{row.get('robot_a')}-R{row.get('robot_b')}"
        for row in limited
    ]
    colors = ["#ff6376" if row.get("type") == "ARM_CROSS" else "#f5b942" for row in limited]
    figure = go.Figure(
        go.Bar(
            y=labels,
            x=[float(row.get("duration_s", 0.0) or 0.0) for row in limited],
            base=[float(row.get("start_s", 0.0) or 0.0) for row in limited],
            orientation="h",
            marker_color=colors,
            hovertemplate="%{y}<br>시작 %{base:,.3f} s<br>지속 %{x:,.3f} s<extra></extra>",
        )
    )
    figure.update_layout(xaxis_title="시간 [s]", yaxis_title="Event", showlegend=False)
    return _style_figure(figure, height=max(300, min(650, 115 + 26 * len(limited))))


def _shape_figure(rows: list[JsonDict], thresholds: JsonDict) -> go.Figure:
    if not rows:
        return _empty_figure("layer_metrics.csv가 없습니다", height=430)
    x_values = [int(row.get("layer_index", 0)) for row in rows]
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.16,
        subplot_titles=("Coverage · IoU", "Underfill · Overfill"),
    )
    for field, name, color in (
        ("coverage", "Coverage", "#21d4a3"),
        ("iou", "IoU", "#5caeff"),
    ):
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=[float(row.get(field, 0.0) or 0.0) for row in rows],
                name=name,
                mode="lines",
                line={"color": color, "width": 2},
                hovertemplate=f"Layer %{{x}}<br>{name} %{{y:.2%}}<extra></extra>",
            ),
            row=1,
            col=1,
        )
    for field, name, color in (
        ("underfill_ratio", "Underfill", "#f5b942"),
        ("overfill_ratio", "Overfill", "#ff6376"),
    ):
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=[float(row.get(field, 0.0) or 0.0) for row in rows],
                name=name,
                mode="lines",
                line={"color": color, "width": 1.6},
                hovertemplate=f"Layer %{{x}}<br>{name} %{{y:.2%}}<extra></extra>",
            ),
            row=2,
            col=1,
        )
    iou_min = thresholds.get("minimum_layer_iou")
    if isinstance(iou_min, int | float):
        figure.add_hline(
            y=float(iou_min),
            line_dash="dot",
            line_color="#5caeff",
            annotation_text="Layer IoU 기준",
            annotation_position="bottom right",
            row=1,
            col=1,
        )
    failed_rows = [row for row in rows if not bool(row.get("passed", False))]
    if failed_rows:
        figure.add_trace(
            go.Scatter(
                x=[int(row.get("layer_index", 0)) for row in failed_rows],
                y=[float(row.get("iou", 0.0) or 0.0) for row in failed_rows],
                name="실패 Layer",
                mode="markers",
                marker={"color": "#ff6376", "size": 9, "symbol": "x"},
                hovertemplate="실패 Layer %{x}<br>IoU %{y:.2%}<extra></extra>",
            ),
            row=1,
            col=1,
        )
    figure.update_yaxes(title_text="일치 비율", tickformat=".1%", row=1, col=1)
    figure.update_yaxes(title_text="오차 비율", tickformat=".1%", row=2, col=1)
    figure.update_xaxes(title_text="Layer 번호", row=2, col=1)
    return _style_figure(figure, height=460)


def _issues_content(rows: list[JsonDict], payload: JsonDict) -> html.Div:
    if not rows:
        warnings = payload.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            return html.Div(
                [html.Div(str(item), className="issue-item issue-warning") for item in warnings],
                className="issue-list",
            )
        return html.Div("경고와 비치명 오류가 없습니다.", className="empty-state success-border")
    return html.Div(
        [
            html.Div(
                [
                    html.Span(str(row.get("severity", "warning")).upper(), className="issue-level"),
                    html.Strong(str(row.get("code", "UNKNOWN"))),
                    html.P(str(row.get("message", ""))),
                ],
                className=f"issue-item issue-{row.get('severity', 'warning')}",
            )
            for row in rows
        ],
        className="issue-list",
    )


def _file_size(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return "크기 확인 불가"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _duration_range(low_s: float, high_s: float) -> str:
    def one(value: float) -> str:
        if value >= 60:
            return f"{value / 60:.1f}분"
        return f"{value:.0f}초"

    return f"{one(low_s)} ~ {one(high_s)}"


def _size_range(low_bytes: int, high_bytes: int) -> str:
    return f"{_file_size_value(low_bytes)} ~ {_file_size_value(high_bytes)}"


def _file_size_value(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _artifact_content(job: JobRecord, run: RunRecord) -> list[html.A]:
    links = []
    for filename, label in _ARTIFACT_LABELS.items():
        path = run.directory / filename
        if path.is_file():
            links.append(
                html.A(
                    [html.Span(label), html.Small(_file_size(path))],
                    href=_artifact_url(job, run, filename),
                    target="_blank",
                    rel="noopener noreferrer",
                    className="artifact-link",
                )
            )
    return links


def _gallery_content(job: JobRecord, run: RunRecord) -> list[html.Figure]:
    gallery = []
    for filename in (
        "overview_xy.png",
        "gantt.png",
        "shape_metrics_by_layer.png",
        "worst_layer_comparison.png",
    ):
        if (run.directory / filename).is_file():
            gallery.append(
                html.Figure(
                    [
                        html.Img(
                            src=_artifact_url(job, run, filename), alt=_ARTIFACT_LABELS[filename]
                        ),
                        html.Figcaption(_ARTIFACT_LABELS[filename]),
                    ]
                )
            )
    return gallery


def _display_path(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1].strip()
    return text


def _detail_rows(items: list[tuple[str, object]]) -> html.Dl:
    children: list[Any] = []
    for label, value in items:
        children.extend([html.Dt(label), html.Dd("—" if value is None else str(value))])
    return html.Dl(children, className="input-detail-list")


def _vector(value: object) -> str:
    if not isinstance(value, list | tuple):
        return "—"
    return "[" + ", ".join(f"{float(item):,.3f}" for item in value) + "]"


def _duration_units(seconds: float) -> str:
    return f"{seconds:,.2f}초 · {seconds / 60.0:,.2f}분 · {seconds / 3600.0:,.2f}시간"


def _inspection_issues(payload: JsonDict) -> Any:
    groups = (
        ("차단 오류", payload.get("blocking_errors", []), "error"),
        ("예상 FAIL", payload.get("expected_failures", []), "expected-fail"),
        ("경고", payload.get("warnings", []), "warning"),
    )
    sections: list[Any] = []
    for label, raw_issues, tone in groups:
        issues = raw_issues if isinstance(raw_issues, list) else []
        if not issues:
            continue
        rows = []
        for issue in issues:
            item = issue if isinstance(issue, dict) else {}
            rows.append(
                html.Li(
                    [
                        html.Code(str(item.get("code", "UNKNOWN"))),
                        html.Span(str(item.get("message", ""))),
                    ]
                )
            )
        sections.append(
            html.Div(
                [html.H4(f"{label} · {len(rows)}"), html.Ul(rows)],
                className=f"input-issues {tone}",
            )
        )
    return sections or html.Div("오류나 경고가 없습니다.", className="empty-state")


def _config_inspection(config: JsonDict) -> Any:
    simulation = _section(config, "simulation")
    process = _section(config, "process")
    workspace = _section(config, "workspace")
    collision = _section(config, "collision")
    validation = _section(config, "validation")
    shape = _section(config, "shape_validation")
    output = _section(config, "output")
    rows: list[tuple[str, object]] = [
        (
            "충돌 샘플 최대 시간 간격",
            f"{simulation.get('max_time_step_s', '—')} s",
        ),
        (
            "충돌 샘플당 최대 TCP 이동거리",
            f"{simulation.get('max_tcp_step_mm', '—')} mm",
        ),
        (
            "충돌 이벤트 병합 최대 간격",
            f"{simulation.get('event_merge_gap_s', '—')} s",
        ),
        ("스트리밍 처리 묶음 크기", simulation.get("batch_size", "—")),
        (
            "Deposition(적층) / Travel(비적층 이동) 기준 속도",
            f"{process.get('deposition_speed_mm_s', '—')} / "
            f"{process.get('travel_speed_mm_s', '—')} mm/s",
        ),
        (
            "레이어 높이 / 명목 비드 폭",
            f"{process.get('layer_height_mm', '—')} / {process.get('bead_width_mm', '—')} mm",
        ),
        (
            "빌드 평면 높이 / TCP Z 해석 기준",
            f"{process.get('build_plane_z_mm', '—')} mm / {process.get('tcp_z_reference', '—')}",
        ),
        ("비적층 이동 안전 높이", f"{process.get('safe_travel_z_mm', '—')} mm"),
        (
            "아크 시작 / 종료 대기시간",
            f"{process.get('arc_on_time_s', '—')} / {process.get('arc_off_time_s', '—')} s",
        ),
        (
            "Base–TCP 선분 교차 / TCP 안전 반경 검사",
            f"{collision.get('check_arm_crossing', '—')} / "
            f"{collision.get('check_tcp_radius', '—')}",
        ),
        ("경계 접촉을 충돌로 판정", collision.get("touching_is_collision", "—")),
        (
            "충돌 기하 계산 허용 오차",
            f"{collision.get('geometry_epsilon_mm', '—')} mm",
        ),
        (
            "원형 적층 작업영역",
            (
                f"{workspace.get('shape')} · center "
                f"{_vector(workspace.get('center_xy_mm'))} · "
                f"R {workspace.get('radius_mm')} mm"
                if workspace
                else "설정 없음"
            ),
        ),
        (
            "속도 위반을 FAIL로 처리 / 상대 허용 오차",
            f"{validation.get('fail_on_speed_violation', '—')} / "
            f"{validation.get('speed_relative_tolerance', '—')}",
        ),
        (
            "Wait(위치 유지 대기) 허용 이동량",
            f"{validation.get('wait_position_tolerance_mm', '—')} mm",
        ),
        (
            "Deposition(적층) 레이어 Z 허용 오차",
            f"{validation.get('layer_z_tolerance_mm', '—')} mm",
        ),
        (
            "Target 밀폐 필수 / 제한적 복구 시도",
            f"{validation.get('require_watertight_target', '—')} / "
            f"{validation.get('attempt_target_repair', '—')}",
        ),
        (
            "Target 체적 차이 경고 기준",
            validation.get("target_volume_discrepancy_warning_ratio", "—"),
        ),
        (
            "Coverage / IoU / Overfill",
            f"≥ {shape.get('minimum_overall_coverage', '—')} / "
            f"≥ {shape.get('minimum_overall_iou', '—')} / "
            f"≤ {shape.get('maximum_overall_overfill_ratio', '—')}",
        ),
        (
            "Layer IoU / 실패 Layer 비율",
            f"≥ {shape.get('minimum_layer_iou', '—')} / "
            f"≤ {shape.get('maximum_failed_layer_ratio', '—')}",
        ),
        (
            "폴리곤 곡선 근사 해상도 / 좌표 정밀도",
            f"{shape.get('polygon_buffer_resolution', '—')} / "
            f"{shape.get('polygon_snap_tolerance_mm', '—')} mm",
        ),
        ("미소 면적 판정 기준", f"{shape.get('area_epsilon_mm2', '—')} mm²"),
        (
            "핵심 결과 저장",
            "Summary JSON {} · 보고서 {} · 적층 STL {}".format(
                output.get("save_summary_json", "—"),
                output.get("save_report_markdown", "—"),
                output.get("save_deposited_stl", "—"),
            ),
        ),
        (
            "지표 CSV 저장",
            "로봇 {} · 충돌 이벤트 {} · 레이어 {}".format(
                output.get("save_robot_metrics_csv", "—"),
                output.get("save_collision_events_csv", "—"),
                output.get("save_layer_metrics_csv", "—"),
            ),
        ),
        ("정적 그래프 저장", output.get("save_static_plots", "—")),
        (
            "직접 Replay 생성 시 기본 프레임 간격",
            f"{output.get('animation_sample_interval_s', '—')} s",
        ),
    ]
    robots = config.get("robots", [])
    for robot in robots if isinstance(robots, list) else []:
        if isinstance(robot, dict):
            rows.append(
                (
                    f"Robot {robot.get('id')} Base 위치",
                    _vector(robot.get("base_xyz_mm")),
                )
            )
            rows.append(
                (
                    f"Robot {robot.get('id')} Home TCP 위치",
                    _vector(robot.get("home_xyz_mm")) if robot.get("home_xyz_mm") else "미설정",
                )
            )
            rows.append(
                (
                    f"Robot {robot.get('id')} TCP 안전 반경 / Reach 반경",
                    f"{robot.get('tcp_radius_mm', '—')} / "
                    f"{robot.get('reach_radius_mm', '—')} mm",
                )
            )
    return _detail_rows(rows)


def _trajectory_inspection(trajectory: JsonDict) -> Any:
    makespan_s = float(trajectory.get("makespan_s", 0.0))
    table_rows = []
    robots = trajectory.get("robots", [])
    for robot in robots if isinstance(robots, list) else []:
        if not isinstance(robot, dict):
            continue
        modes = robot.get("mode_interval_counts", {})
        mode_text = (
            f"D {modes.get('D', 0)} / T {modes.get('T', 0)} / W {modes.get('W', 0)}"
            if isinstance(modes, dict)
            else "—"
        )
        table_rows.append(
            html.Tr(
                [
                    html.Td(f"R{robot.get('robot_id')}"),
                    html.Td(f"{int(robot.get('row_count', 0)):,}"),
                    html.Td(f"{float(robot.get('end_s', 0.0)):,.2f}"),
                    html.Td(mode_text),
                    html.Td(_vector(robot.get("xyz_min_mm"))),
                    html.Td(_vector(robot.get("xyz_max_mm"))),
                    html.Td(
                        f"{float(robot.get('deposition_time_s', 0.0)):,.2f} / "
                        f"{float(robot.get('travel_time_s', 0.0)):,.2f} / "
                        f"{float(robot.get('wait_time_s', 0.0)):,.2f}"
                    ),
                    html.Td(f"{float(robot.get('deposition_length_mm', 0.0)):,.2f}"),
                    html.Td(f"{float(robot.get('travel_length_mm', 0.0)):,.2f}"),
                    html.Td(
                        f"{float(robot.get('mean_deposition_speed_mm_s') or 0.0):,.2f} / "
                        f"{float(robot.get('mean_travel_speed_mm_s') or 0.0):,.2f}"
                    ),
                ]
            )
        )
    return html.Section(
        [
            html.H3("Trajectory"),
            _detail_rows(
                [
                    ("전체 행", f"{int(trajectory.get('row_count', 0)):,}"),
                    ("Makespan", _duration_units(makespan_s)),
                    (
                        "Workload imbalance",
                        f"{float(trajectory.get('workload_imbalance_s', 0.0)):,.2f} s",
                    ),
                ]
            ),
            html.Div(
                html.Table(
                    [
                        html.Thead(
                            html.Tr(
                                [
                                    html.Th("Robot"),
                                    html.Th("행"),
                                    html.Th("완료 [s]"),
                                    html.Th("구간 수"),
                                    html.Th("XYZ 최소"),
                                    html.Th("XYZ 최대"),
                                    html.Th("D/T/W 시간 [s]"),
                                    html.Th("D 거리 [mm]"),
                                    html.Th("T 거리 [mm]"),
                                    html.Th("평균 D/T 속도"),
                                ]
                            )
                        ),
                        html.Tbody(table_rows),
                    ],
                    className="input-trajectory-table",
                ),
                className="input-table-wrap",
            ),
        ],
        className="input-panel input-panel-wide",
    )


def _inspection_content(payload: JsonDict) -> Any:
    status = str(payload.get("status", "BLOCKED"))
    status_copy = {
        "READY": ("실행 준비 완료", "입력 구조와 좌표계 검사가 완료되었습니다."),
        "WARNING": ("실행 가능 · 경고", "경고를 검토한 뒤 Validation을 실행할 수 있습니다."),
        "EXPECTED_FAIL": (
            "실행 가능 · FAIL 예상",
            "공정 규칙 위반이 있어 Validation 결과가 FAIL일 가능성이 있습니다.",
        ),
        "BLOCKED": ("실행 불가", "차단 오류를 수정하고 입력 확인을 다시 실행하세요."),
    }
    title, description = status_copy.get(status, status_copy["BLOCKED"])
    files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
    file_cards = []
    for filename in ("config.yaml", "trajectory.csv", "target.stl"):
        details = files.get(filename, {}) if isinstance(files, dict) else {}
        size = details.get("size_bytes") if isinstance(details, dict) else None
        file_cards.append(
            html.Div(
                [
                    html.Strong(filename),
                    html.Span(
                        _file_size_value(int(size)) if isinstance(size, int | float) else "—"
                    ),
                    html.Small(f"{details.get('status', '—')} · {details.get('modified_at', '—')}"),
                    html.Code(str(details.get("path", "—"))),
                ],
                className="input-file-card",
            )
        )
    config_value = payload.get("config")
    config: JsonDict = config_value if isinstance(config_value, dict) else {}
    trajectory_value = payload.get("trajectory")
    trajectory: JsonDict = trajectory_value if isinstance(trajectory_value, dict) else {}
    target_value = payload.get("target")
    target: JsonDict = target_value if isinstance(target_value, dict) else {}
    volume = target.get("volume_mm3")
    return html.Div(
        [
            html.Div(
                [
                    html.Div([html.Strong(title), html.P(description)]),
                    html.Span(status, className=f"input-status-pill status-{status.lower()}"),
                ],
                className=f"input-verdict is-{status.lower()}",
            ),
            html.Div(file_cards, className="input-file-grid"),
            html.Div(
                [
                    html.Section(
                        [html.H3("Config"), _config_inspection(config)],
                        className="input-panel",
                    ),
                    html.Section(
                        [
                            html.H3("Target STL"),
                            _detail_rows(
                                [
                                    (
                                        "정점 / 면 / Body",
                                        f"{target.get('vertex_count', '—')} / "
                                        f"{target.get('face_count', '—')} / "
                                        f"{target.get('body_count', '—')}",
                                    ),
                                    ("최소 XYZ [mm]", _vector(target.get("bounds_min_mm"))),
                                    ("최대 XYZ [mm]", _vector(target.get("bounds_max_mm"))),
                                    ("치수 [mm]", _vector(target.get("dimensions_mm"))),
                                    (
                                        "체적 [mm³]",
                                        f"{float(volume):,.3f}"
                                        if isinstance(volume, int | float)
                                        else "—",
                                    ),
                                    ("Watertight", target.get("watertight")),
                                ]
                            ),
                        ],
                        className="input-panel",
                    ),
                ],
                className="input-detail-grid",
            ),
            _trajectory_inspection(trajectory),
            html.Section(
                [html.H3("종합 검사"), _inspection_issues(payload)],
                className="input-panel input-panel-wide",
            ),
            html.P(
                f"검사 완료: {payload.get('checked_at', '—')}",
                className="input-checked-at",
            ),
        ],
        className="input-inspection-result",
    )


def _input_error_content(message: str) -> Any:
    return html.Div(
        [html.Strong("입력 확인 실패"), html.P(message)],
        className="input-verdict is-blocked",
    )


def _table_style() -> dict[str, Any]:
    return {
        "page_action": "custom",
        "sort_action": "custom",
        "sort_mode": "single",
        "page_current": 0,
        "page_size": 12,
        "page_count": 1,
        "style_as_list_view": True,
        "style_table": {"overflowX": "auto"},
        "style_header": {
            "backgroundColor": "#122338",
            "color": "#8fa3b8",
            "border": "0",
            "fontWeight": "700",
        },
        "style_cell": {
            "backgroundColor": "#0d1927",
            "color": "#dce8f5",
            "border": "0",
            "borderBottom": "1px solid #20344b",
            "padding": "10px",
            "fontFamily": "Segoe UI, Malgun Gothic, sans-serif",
            "fontSize": "14px",
            "textAlign": "left",
        },
    }


def _build_layout(jobs_root: Path, jobs: list[JobRecord]) -> html.Div:
    options = [{"label": job.name, "value": job.name} for job in jobs]
    selected = jobs[0].name if jobs else None
    collision_columns = [
        {"name": "ID", "id": "event_id", "type": "numeric"},
        {"name": "유형", "id": "type"},
        {"name": "Robot A", "id": "robot_a", "type": "numeric"},
        {"name": "Robot B", "id": "robot_b", "type": "numeric"},
        {"name": "시작 [s]", "id": "start_s", "type": "numeric"},
        {"name": "종료 [s]", "id": "end_s", "type": "numeric"},
        {"name": "지속 [s]", "id": "duration_s", "type": "numeric"},
        {"name": "최소 TCP [mm]", "id": "min_tcp_distance_mm", "type": "numeric"},
    ]
    layer_columns = [
        {"name": "Layer", "id": "layer_index", "type": "numeric"},
        {"name": "Z [mm]", "id": "z_slice_mm", "type": "numeric"},
        {"name": "Coverage", "id": "coverage", "type": "numeric"},
        {"name": "Underfill", "id": "underfill_ratio", "type": "numeric"},
        {"name": "Overfill", "id": "overfill_ratio", "type": "numeric"},
        {"name": "IoU", "id": "iou", "type": "numeric"},
        {"name": "판정", "id": "passed"},
    ]
    return html.Div(
        [
            dcc.Store(id="runtime-store", data={"state": "IDLE", "running": False}),
            dcc.Store(id="replay-runtime-store", data={"state": "IDLE", "running": False}),
            dcc.Store(id="result-refresh", data={}),
            dcc.Store(id="replay-refresh", data={}),
            dcc.Store(id="input-inspection-store", data={}),
            dcc.Store(id="jobs-root-store", data={"path": str(jobs_root)}),
            dcc.Store(id="job-use-request", data={}),
            dcc.Interval(
                id="validation-poller",
                interval=2000,
                n_intervals=0,
                disabled=True,
            ),
            dcc.Interval(
                id="replay-poller",
                interval=2000,
                n_intervals=0,
                disabled=True,
            ),
            html.Header(
                [
                    html.Div(
                        [
                            html.Div("WV", className="brand-mark"),
                            html.Div(
                                [html.H1("WAAM Validator"), html.P("검증 관제 대시보드")],
                                className="brand-copy",
                            ),
                        ],
                        className="brand",
                    ),
                    html.Div(
                        [
                            html.Label("작업", htmlFor="job-select"),
                            dcc.Dropdown(
                                id="job-select",
                                options=options,
                                value=selected,
                                clearable=False,
                                searchable=False,
                                placeholder="검증 작업이 없습니다",
                            ),
                            html.Div(
                                [
                                    html.Button(
                                        "Validation 실행",
                                        id="run-button",
                                        disabled=not jobs,
                                    ),
                                    html.Small(id="run-button-hint"),
                                ],
                                className="run-control",
                            ),
                        ],
                        className="command-bar",
                    ),
                ],
                className="topbar",
            ),
            html.Main(
                [
                    html.Div(id="run-banner", className="run-banner is-idle"),
                    html.Pre(id="live-log", className="live-log is-hidden"),
                    html.Section(
                        [
                            html.Label("JOBS_ROOT", htmlFor="jobs-root-input"),
                            dcc.Input(
                                id="jobs-root-input",
                                type="text",
                                debounce=True,
                                value=str(jobs_root),
                            ),
                            html.Button("적용", id="jobs-root-apply"),
                            html.Span(
                                "대시보드가 탐색할 작업 루트를 설정합니다.",
                                id="jobs-root-message",
                            ),
                        ],
                        className="jobs-root-settings",
                    ),
                    html.Details(
                        [
                            html.Summary("＋ 새 입력 불러오기"),
                            html.Section(
                                [
                                    html.Div(
                                        [
                                            html.Label("작업 폴더", htmlFor="input-job-dir"),
                                            dcc.Input(
                                                id="input-job-dir",
                                                type="text",
                                                debounce=True,
                                                placeholder=str(jobs_root / "new_model"),
                                            ),
                                            html.Small(
                                                "JOBS_ROOT 자체 또는 바로 아래 폴더만 허용됩니다."
                                            ),
                                        ],
                                        className="input-path-field input-path-field-wide",
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Label(
                                                        "Config", htmlFor="input-config-path"
                                                    ),
                                                    dcc.Input(
                                                        id="input-config-path",
                                                        type="text",
                                                        debounce=True,
                                                        placeholder="config.yaml 경로",
                                                    ),
                                                ],
                                                className="input-path-field",
                                            ),
                                            html.Div(
                                                [
                                                    html.Label(
                                                        "Trajectory",
                                                        htmlFor="input-trajectory-path",
                                                    ),
                                                    dcc.Input(
                                                        id="input-trajectory-path",
                                                        type="text",
                                                        debounce=True,
                                                        placeholder="trajectory.csv 경로",
                                                    ),
                                                ],
                                                className="input-path-field",
                                            ),
                                            html.Div(
                                                [
                                                    html.Label(
                                                        "Target STL", htmlFor="input-target-path"
                                                    ),
                                                    dcc.Input(
                                                        id="input-target-path",
                                                        type="text",
                                                        debounce=True,
                                                        placeholder="target.stl 경로",
                                                    ),
                                                ],
                                                className="input-path-field",
                                            ),
                                        ],
                                        className="input-path-grid",
                                    ),
                                    html.Div(
                                        [
                                            html.Button("입력 확인", id="input-check-button"),
                                            html.Span(id="input-readiness-message"),
                                        ],
                                        className="input-actions",
                                    ),
                                    dcc.Loading(
                                        html.Div(
                                            "경로를 설정하고 입력 확인을 누르세요.",
                                            id="input-inspection-content",
                                            className="input-inspection-empty",
                                        ),
                                        type="circle",
                                    ),
                                ],
                                className="input-preparation-body",
                            ),
                        ],
                        id="input-preparation",
                        className="input-preparation",
                    ),
                    html.Section(
                        [
                            html.Div(
                                [
                                    html.P("LATEST VALIDATION", className="eyebrow"),
                                    html.H2(id="result-title"),
                                    html.P(id="result-meta", className="result-meta"),
                                ]
                            ),
                            html.Div(id="status-pill", className="status-pill status-empty"),
                        ],
                        className="result-heading",
                    ),
                    html.Section(id="metric-grid", className="metric-grid"),
                    dcc.Tabs(
                        id="detail-tabs",
                        value="overview",
                        className="detail-tabs",
                        children=[
                            dcc.Tab(
                                label="종합",
                                value="overview",
                                children=html.Section(
                                    [
                                        html.Div(
                                            [html.H3("판정 요약"), html.Div(id="result-summary")],
                                            className="panel",
                                        ),
                                        html.Div(
                                            [html.H3("적용 임계값"), html.Div(id="threshold-list")],
                                            className="panel",
                                        ),
                                        html.Div(
                                            [html.H3("경고 및 오류"), html.Div(id="issues-list")],
                                            className="panel panel-span",
                                        ),
                                    ],
                                    className="overview-grid",
                                ),
                            ),
                            dcc.Tab(
                                label="로봇 작업",
                                value="robots",
                                children=html.Section(
                                    [
                                        html.Div(
                                            [
                                                html.H3("작업 시간 구성"),
                                                dcc.Graph(id="robot-figure", config=_GRAPH_CONFIG),
                                            ],
                                            className="panel panel-chart",
                                        ),
                                        html.Div(
                                            [html.H3("로봇별 지표"), html.Div(id="robot-table")],
                                            className="panel",
                                        ),
                                    ],
                                    className="stack-grid",
                                ),
                            ),
                            dcc.Tab(
                                label="충돌",
                                value="collision",
                                children=html.Section(
                                    [
                                        html.Div(
                                            [
                                                html.H3("TCP 안전 여유"),
                                                dcc.Graph(
                                                    id="collision-gauge", config=_GRAPH_CONFIG
                                                ),
                                            ],
                                            className="panel panel-chart",
                                        ),
                                        html.Div(
                                            [
                                                html.H3("충돌 시간축"),
                                                dcc.Graph(
                                                    id="collision-timeline", config=_GRAPH_CONFIG
                                                ),
                                            ],
                                            className="panel panel-chart",
                                        ),
                                        html.Div(
                                            [
                                                html.Div(
                                                    [
                                                        html.H3("충돌 이벤트"),
                                                        html.Div(
                                                            [
                                                                dcc.Dropdown(
                                                                    id="collision-type-filter",
                                                                    options=[
                                                                        {
                                                                            "label": "전체 유형",
                                                                            "value": "ALL",
                                                                        },
                                                                        {
                                                                            "label": "ARM_CROSS",
                                                                            "value": "ARM_CROSS",
                                                                        },
                                                                        {
                                                                            "label": "TCP_RADIUS",
                                                                            "value": "TCP_RADIUS",
                                                                        },
                                                                    ],
                                                                    value="ALL",
                                                                    clearable=False,
                                                                ),
                                                                dcc.Dropdown(
                                                                    id="collision-pair-filter",
                                                                    options=[
                                                                        {
                                                                            "label": "전체 Pair",
                                                                            "value": "ALL",
                                                                        },
                                                                        {
                                                                            "label": "R1–R2",
                                                                            "value": "1-2",
                                                                        },
                                                                        {
                                                                            "label": "R1–R3",
                                                                            "value": "1-3",
                                                                        },
                                                                        {
                                                                            "label": "R2–R3",
                                                                            "value": "2-3",
                                                                        },
                                                                    ],
                                                                    value="ALL",
                                                                    clearable=False,
                                                                ),
                                                            ],
                                                            className="table-filters",
                                                        ),
                                                    ],
                                                    className="panel-heading",
                                                ),
                                                DataTable(
                                                    id="collision-table",
                                                    columns=collision_columns,
                                                    data=[],
                                                    **_table_style(),
                                                ),
                                            ],
                                            className="panel panel-span",
                                        ),
                                    ],
                                    className="overview-grid",
                                ),
                            ),
                            dcc.Tab(
                                label="형상",
                                value="shape",
                                children=html.Section(
                                    [
                                        html.Div(
                                            [
                                                html.H3("Layer별 형상 품질"),
                                                dcc.Graph(id="shape-figure", config=_GRAPH_CONFIG),
                                            ],
                                            className="panel panel-chart",
                                        ),
                                        html.Div(
                                            [
                                                html.Div(
                                                    [
                                                        html.H3("Layer 지표"),
                                                        dcc.Checklist(
                                                            id="failed-only",
                                                            options=[
                                                                {
                                                                    "label": "실패 Layer만",
                                                                    "value": "failed",
                                                                }
                                                            ],
                                                            value=[],
                                                            className="failed-only",
                                                        ),
                                                    ],
                                                    className="panel-heading",
                                                ),
                                                DataTable(
                                                    id="layer-table",
                                                    columns=layer_columns,
                                                    data=[],
                                                    **_table_style(),
                                                ),
                                            ],
                                            className="panel",
                                        ),
                                    ],
                                    className="stack-grid",
                                ),
                            ),
                            dcc.Tab(
                                label="3D 및 산출물",
                                value="artifacts",
                                children=html.Section(
                                    [
                                        html.Div(
                                            [
                                                html.H3("3D Replay"),
                                                html.P(
                                                    "검증 결과와 별도로 필요할 때만 생성합니다. "
                                                    "프레임 간격은 검증 정밀도에 영향을 주지 "
                                                    "않습니다.",
                                                    className="muted-copy",
                                                ),
                                                html.Div(
                                                    [
                                                        html.Label(
                                                            [
                                                                html.Span("품질 프리셋"),
                                                                dcc.RadioItems(
                                                                    id="replay-preset",
                                                                    options=[
                                                                        {
                                                                            "label": "빠름",
                                                                            "value": "fast",
                                                                        },
                                                                        {
                                                                            "label": "표준",
                                                                            "value": "standard",
                                                                        },
                                                                        {
                                                                            "label": "상세",
                                                                            "value": "detail",
                                                                        },
                                                                        {
                                                                            "label": "사용자 지정",
                                                                            "value": "custom",
                                                                        },
                                                                    ],
                                                                    value="standard",
                                                                    inline=True,
                                                                ),
                                                            ],
                                                            className="replay-field replay-presets",
                                                        ),
                                                        html.Label(
                                                            [
                                                                html.Span("프레임 간격 [초]"),
                                                                dcc.Input(
                                                                    id="replay-interval",
                                                                    type="number",
                                                                    min=1,
                                                                    step=1,
                                                                    value=1,
                                                                    disabled=True,
                                                                ),
                                                            ],
                                                            className="replay-field",
                                                        ),
                                                    ],
                                                    className="replay-settings",
                                                ),
                                                html.Div(
                                                    id="replay-estimate",
                                                    className="replay-estimate",
                                                ),
                                                html.Div(
                                                    [
                                                        html.Button(
                                                            "Replay 생성",
                                                            id="replay-generate-button",
                                                            disabled=True,
                                                        ),
                                                        html.Button(
                                                            "취소",
                                                            id="replay-cancel-button",
                                                            disabled=True,
                                                            className="secondary-button",
                                                        ),
                                                    ],
                                                    className="replay-actions",
                                                ),
                                                html.Div(
                                                    id="replay-status",
                                                    className="replay-status is-idle",
                                                ),
                                                html.Progress(
                                                    id="replay-progress",
                                                    value="0",
                                                    max=1,
                                                    className="replay-progress",
                                                ),
                                                html.Div(id="replay-container"),
                                            ],
                                            className="panel replay-panel",
                                        ),
                                        html.Div(
                                            [
                                                html.H3("결과 파일"),
                                                html.Div(
                                                    id="artifact-list", className="artifact-list"
                                                ),
                                            ],
                                            className="panel",
                                        ),
                                        html.Div(
                                            id="image-gallery", className="image-gallery panel-span"
                                        ),
                                    ],
                                    className="artifact-grid",
                                ),
                            ),
                        ],
                    ),
                    html.Footer(str(jobs_root), id="root-path", className="root-path"),
                ],
                className="workspace",
            ),
        ],
        className="dashboard-shell",
    )


def create_dashboard_app(
    jobs_root: Path,
    *,
    run_manager: ValidationRunManager | None = None,
    replay_run_manager: ReplayRunManager | None = None,
) -> Dash:
    """Create the local Dash app without starting a server."""
    initial_root = jobs_root.expanduser().resolve()
    jobs = discover_jobs(initial_root)
    root_lock = threading.Lock()
    root_state = {"path": initial_root}
    manager = run_manager or ValidationRunManager()
    replay_manager = replay_run_manager or ReplayRunManager()
    app = Dash(
        __name__,
        assets_folder=str(Path(__file__).with_name("assets")),
        title="WAAM Validator",
        update_title=cast(str, None),
        meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
    )
    app.layout = _build_layout(initial_root, jobs)
    app.server.config["WAAM_JOBS_ROOT"] = str(initial_root)
    app.server.config["WAAM_RUN_MANAGER"] = manager
    app.server.config["WAAM_REPLAY_RUN_MANAGER"] = replay_manager

    def current_root() -> Path:
        with root_lock:
            return root_state["path"]

    def set_current_root(path: Path) -> None:
        with root_lock:
            root_state["path"] = path
        app.server.config["WAAM_JOBS_ROOT"] = str(path)

    def find_job(job_name: str | None) -> JobRecord | None:
        if job_name is None:
            return None
        try:
            return resolve_job(current_root(), job_name)
        except DashboardDataError:
            return None

    @app.server.get("/artifacts/<job_name>/<run_name>/<filename>")  # type: ignore[untyped-decorator]
    def serve_artifact(job_name: str, run_name: str, filename: str) -> Any:
        try:
            path = resolve_artifact(current_root(), job_name, run_name, filename)
        except DashboardDataError:
            abort(404)
        inline = path.suffix.lower() in {".html", ".png", ".md", ".log"}
        return send_file(path, as_attachment=not inline, download_name=path.name)

    @app.callback(
        Output("jobs-root-store", "data"),
        Output("jobs-root-input", "value"),
        Output("jobs-root-message", "children"),
        Output("jobs-root-message", "className"),
        Output("root-path", "children"),
        Output("input-job-dir", "value"),
        Input("jobs-root-apply", "n_clicks"),
        State("jobs-root-input", "value"),
        prevent_initial_call=True,
    )
    def apply_jobs_root(
        n_clicks: int | None,
        value: str | None,
    ) -> tuple[Any, str, str, str, Any, Any]:
        active_root = current_root()
        if not n_clicks:
            return no_update, str(active_root), "", "", no_update, no_update
        if manager.snapshot().get("running") or replay_manager.snapshot().get("running"):
            return (
                no_update,
                str(active_root),
                "실행 중에는 JOBS_ROOT를 변경할 수 없습니다.",
                "is-error",
                no_update,
                no_update,
            )
        try:
            if value is None or not value.strip():
                raise DashboardDataError("JOBS_ROOT 경로를 입력하세요.")
            candidate = Path(_display_path(value)).expanduser().resolve()
            if not candidate.is_dir():
                raise DashboardDataError(f"JOBS_ROOT가 존재하지 않습니다: {candidate}")
            discover_jobs(candidate)
        except (DashboardDataError, OSError) as exc:
            return (
                no_update,
                str(active_root),
                str(exc),
                "is-error",
                no_update,
                no_update,
            )
        set_current_root(candidate)
        return (
            {"path": str(candidate), "nonce": time.time_ns()},
            str(candidate),
            "JOBS_ROOT를 적용했습니다.",
            "is-ready",
            str(candidate),
            "",
        )

    @app.callback(
        Output("jobs-root-apply", "disabled"),
        Input("runtime-store", "data"),
        Input("replay-runtime-store", "data"),
    )
    def jobs_root_button_state(
        runtime: JsonDict | None,
        replay_runtime: JsonDict | None,
    ) -> bool:
        return bool((runtime or {}).get("running")) or bool((replay_runtime or {}).get("running"))

    @app.callback(
        Output("job-select", "options"),
        Output("job-select", "value"),
        Input("jobs-root-store", "data"),
        Input("job-use-request", "data"),
        State("job-select", "value"),
    )
    def refresh_job_options(
        _root_data: JsonDict,
        request: JsonDict | None,
        selected: str | None,
    ) -> tuple[list[JsonDict], str | None]:
        jobs_now = discover_jobs(current_root())
        options: list[JsonDict] = [{"label": job.name, "value": job.name} for job in jobs_now]
        requested_path = (request or {}).get("job_dir")
        if isinstance(requested_path, str):
            requested = next(
                (job.name for job in jobs_now if str(job.path) == requested_path),
                None,
            )
            if requested is not None:
                return options, requested
        if selected is not None and any(job.name == selected for job in jobs_now):
            return options, selected
        return options, jobs_now[0].name if jobs_now else None

    @app.callback(
        Output("input-config-path", "value"),
        Output("input-trajectory-path", "value"),
        Output("input-target-path", "value"),
        Input("input-job-dir", "value"),
        prevent_initial_call=True,
    )
    def fill_input_paths(job_dir: str | None) -> tuple[str, str, str]:
        if not job_dir or not job_dir.strip():
            return "", "", ""
        directory = Path(_display_path(job_dir)).expanduser().resolve()
        return (
            str(directory / "config.yaml"),
            str(directory / "trajectory.csv"),
            str(directory / "target.stl"),
        )

    @app.callback(
        Output("input-inspection-store", "data"),
        Output("input-inspection-content", "children"),
        Output("job-use-request", "data"),
        Input("input-check-button", "n_clicks"),
        State("input-job-dir", "value"),
        State("input-config-path", "value"),
        State("input-trajectory-path", "value"),
        State("input-target-path", "value"),
        prevent_initial_call=True,
    )
    def inspect_inputs(
        n_clicks: int | None,
        job_dir: str | None,
        config_path: str | None,
        trajectory_path: str | None,
        target_path: str | None,
    ) -> tuple[JsonDict, Any, JsonDict]:
        if not n_clicks or not job_dir:
            return {}, _input_error_content("작업 폴더를 입력하세요."), {}
        try:
            paths = resolve_dashboard_inputs(
                current_root(),
                job_dir,
                config_path,
                trajectory_path,
                target_path,
            )
            inspection = inspect_dashboard_inputs(paths).to_dict()
            request = (
                {"job_dir": str(paths.job_dir), "nonce": time.time_ns()}
                if inspection.get("can_run")
                else {}
            )
            return inspection, _inspection_content(inspection), request
        except (DashboardDataError, OSError, ValueError) as exc:
            payload: JsonDict = {
                "status": "BLOCKED",
                "can_run": False,
                "blocking_errors": [{"code": "DASHBOARD_INPUT_ERROR", "message": str(exc)}],
            }
            return payload, _input_error_content(str(exc)), {}

    @app.callback(
        Output("input-readiness-message", "children"),
        Output("input-readiness-message", "className"),
        Input("input-inspection-store", "data"),
        Input("input-job-dir", "value"),
        Input("input-config-path", "value"),
        Input("input-trajectory-path", "value"),
        Input("input-target-path", "value"),
        Input("jobs-root-store", "data"),
    )
    def enable_inspected_job(
        inspection: JsonDict | None,
        job_dir: str | None,
        config_path: str | None,
        trajectory_path: str | None,
        target_path: str | None,
        _root_data: JsonDict,
    ) -> tuple[str, str]:
        if not inspection or not job_dir:
            return "입력 확인이 완료되면 이 작업이 자동 선택됩니다.", "is-idle"
        if not inspection.get("can_run"):
            return "차단 오류를 수정하고 입력 확인을 다시 실행하세요.", "is-error"
        try:
            paths = resolve_dashboard_inputs(
                current_root(),
                job_dir,
                config_path,
                trajectory_path,
                target_path,
            )
            if not inspection_is_current(inspection, paths):
                return (
                    "경로나 파일이 변경되었습니다. 입력 확인을 다시 실행하세요.",
                    "is-error",
                )
            return (
                f"입력 확인 완료 · {paths.job_dir.name} 작업이 자동 선택되었습니다.",
                "is-ready",
            )
        except (DashboardDataError, OSError, ValueError) as exc:
            return str(exc), "is-error"

    @app.callback(
        Output("runtime-store", "data"),
        Output("run-banner", "children"),
        Output("run-banner", "className"),
        Output("live-log", "children"),
        Output("live-log", "className"),
        Output("validation-poller", "disabled"),
        Output("result-refresh", "data"),
        Input("run-button", "n_clicks"),
        Input("validation-poller", "n_intervals"),
        State("job-select", "value"),
        State("input-inspection-store", "data"),
        State("input-job-dir", "value"),
        State("input-config-path", "value"),
        State("input-trajectory-path", "value"),
        State("input-target-path", "value"),
    )
    def manage_validation(
        n_clicks: int | None,
        _interval: int,
        selected_job: str | None,
        inspection: JsonDict | None,
        input_job_dir: str | None,
        input_config: str | None,
        input_trajectory: str | None,
        input_target: str | None,
    ) -> tuple[Any, ...]:
        if ctx.triggered_id == "run-button" and n_clicks:
            try:
                if replay_manager.snapshot().get("running"):
                    raise ValidationAlreadyRunningError(
                        "Replay 생성이 끝난 뒤 Validation을 실행하세요."
                    )
                job = resolve_job(current_root(), selected_job or "")
                inspected_paths = inspection.get("paths", {}) if inspection else {}
                if isinstance(inspected_paths, dict) and inspected_paths.get("job_dir") == str(
                    job.path
                ):
                    paths = resolve_dashboard_inputs(
                        current_root(),
                        input_job_dir or job.path,
                        input_config,
                        input_trajectory,
                        input_target,
                    )
                    if not inspection_is_current(inspection or {}, paths):
                        raise DashboardDataError(
                            "입력 파일이 변경되었습니다. 입력 확인을 다시 실행하세요."
                        )
                manager.start(job)
            except (DashboardDataError, ValidationAlreadyRunningError, OSError) as exc:
                return (
                    {"state": "ERROR", "running": False, "message": str(exc)},
                    [html.Span("ERROR", className="run-state"), html.Strong(str(exc))],
                    "run-banner is-error",
                    "",
                    "live-log is-hidden",
                    True,
                    no_update,
                )
        snapshot = manager.snapshot()
        running = bool(snapshot.get("running"))
        if running or snapshot.get("state") == "FINISHED":
            stage = str(snapshot.get("stage", "starting"))
            label = _STAGE_LABELS.get(stage, stage)
            elapsed = float(snapshot.get("elapsed_s", 0.0) or 0.0)
            verdict = str(snapshot.get("verdict", ""))
            message = str(snapshot.get("message", ""))
            banner = [
                html.Span("RUNNING" if running else verdict or "FINISHED", className="run-state"),
                html.Strong(f"{snapshot.get('job_name', '')} · {label}"),
                html.Span(f"{elapsed:,.1f} s · {message}", className="run-message"),
            ]
            tone = "running" if running else verdict.lower() or "finished"
            log = str(snapshot.get("log", ""))
            return (
                snapshot,
                banner,
                f"run-banner is-{tone}",
                log,
                "live-log" if log else "live-log is-hidden",
                not running,
                no_update
                if running
                else {"job_name": snapshot.get("job_name"), "nonce": time.time_ns()},
            )
        return (
            snapshot,
            html.Span("Validation을 실행하거나 최신 결과를 검토하세요."),
            "run-banner is-idle",
            "",
            "live-log is-hidden",
            True,
            no_update,
        )

    @app.callback(
        Output("run-button", "disabled"),
        Output("run-button", "children"),
        Output("run-button-hint", "children"),
        Output("run-button-hint", "className"),
        Input("job-select", "value"),
        Input("runtime-store", "data"),
        Input("replay-runtime-store", "data"),
    )
    def validation_button_state(
        job_name: str | None,
        runtime: JsonDict | None,
        replay_runtime: JsonDict | None,
    ) -> tuple[bool, str, str, str]:
        running = bool((runtime or {}).get("running"))
        replay_running = bool((replay_runtime or {}).get("running"))
        if job_name is None:
            return (
                True,
                "Validation 실행",
                "입력 확인을 완료하면 실행할 작업이 자동 선택됩니다.",
                "is-error",
            )
        if running:
            return True, "Validation 실행 중…", f"{job_name} 작업 처리 중", "is-running"
        if replay_running:
            return True, "Validation 실행", "Replay 생성이 끝난 뒤 실행할 수 있습니다.", "is-idle"
        if (runtime or {}).get("state") == "ERROR":
            message = str((runtime or {}).get("message", "실행을 시작하지 못했습니다."))
            return False, "Validation 다시 실행", f"실행 실패: {message}", "is-error"
        return (
            False,
            "Validation 실행",
            f"선택된 작업: {job_name}",
            "is-ready",
        )

    @app.callback(
        Output("replay-interval", "value"),
        Output("replay-interval", "disabled"),
        Input("replay-preset", "value"),
        Input("job-select", "value"),
        Input("result-refresh", "data"),
        State("replay-interval", "value"),
    )
    def select_replay_interval(
        preset: str,
        job_name: str | None,
        _refresh: JsonDict,
        current: float | None,
    ) -> tuple[Any, bool]:
        if preset == "custom":
            return current if current is not None else 1.0, False
        job = find_job(job_name)
        run = load_latest_run(job.path) if job is not None else None
        if run is None:
            return 1.0, True
        schedule = _section(run.payload, "schedule")
        makespan = schedule.get("makespan_s")
        if not isinstance(makespan, int | float):
            return 1.0, True
        return preset_interval_s(float(makespan), preset), True

    @app.callback(
        Output("replay-runtime-store", "data"),
        Output("replay-status", "children"),
        Output("replay-status", "className"),
        Output("replay-progress", "value"),
        Output("replay-poller", "disabled"),
        Output("replay-refresh", "data"),
        Input("replay-generate-button", "n_clicks"),
        Input("replay-cancel-button", "n_clicks"),
        Input("replay-poller", "n_intervals"),
        State("job-select", "value"),
        State("replay-interval", "value"),
    )
    def manage_replay(
        generate_clicks: int | None,
        cancel_clicks: int | None,
        _interval: int,
        job_name: str | None,
        interval_s: float | None,
    ) -> tuple[Any, ...]:
        if ctx.triggered_id == "replay-cancel-button" and cancel_clicks:
            if not replay_manager.cancel():
                return (
                    {"state": "ERROR", "running": False},
                    "취소할 Replay가 없습니다.",
                    "replay-status is-error",
                    0,
                    True,
                    no_update,
                )
        elif ctx.triggered_id == "replay-generate-button" and generate_clicks:
            try:
                if manager.snapshot().get("running"):
                    raise ReplayAlreadyRunningError("Validation이 끝난 뒤 Replay를 생성하세요.")
                if job_name is None or interval_s is None:
                    raise DashboardDataError("작업과 프레임 간격을 선택하세요.")
                job = resolve_job(current_root(), job_name)
                run = load_latest_run(job.path)
                if run is None or run.status == "ERROR":
                    raise DashboardDataError(
                        "완료된 PASS/FAIL 결과가 있어야 Replay를 생성할 수 있습니다."
                    )
                estimate = estimate_replay(job, run, float(interval_s))
                if not estimate.allowed:
                    raise DashboardDataError(estimate.warning)
                replay_manager.start(job, run, float(interval_s))
            except (
                DashboardDataError,
                ReplayAlreadyRunningError,
                OSError,
                ValueError,
            ) as exc:
                return (
                    {"state": "ERROR", "running": False, "message": str(exc)},
                    str(exc),
                    "replay-status is-error",
                    0,
                    True,
                    no_update,
                )
        snapshot = replay_manager.snapshot()
        running = bool(snapshot.get("running"))
        if running or snapshot.get("state") == "FINISHED":
            progress = float(snapshot.get("progress", 0.0) or 0.0)
            elapsed = float(snapshot.get("elapsed_s", 0.0) or 0.0)
            verdict = str(snapshot.get("verdict", "RUNNING" if running else "FINISHED"))
            content = [
                html.Strong(verdict),
                html.Span(
                    f"{snapshot.get('job_name', '')} · {elapsed:,.1f}초 · "
                    f"{snapshot.get('message', '')}"
                ),
            ]
            tone = "running" if running else verdict.lower()
            return (
                snapshot,
                content,
                f"replay-status is-{tone}",
                min(1.0, max(0.0, progress)),
                not running,
                no_update
                if running
                else {"job_name": snapshot.get("job_name"), "nonce": time.time_ns()},
            )
        return (
            snapshot,
            "Replay를 생성하지 않은 상태입니다.",
            "replay-status is-idle",
            0,
            True,
            no_update,
        )

    @app.callback(
        Output("replay-generate-button", "disabled"),
        Output("replay-cancel-button", "disabled"),
        Output("replay-generate-button", "children"),
        Input("job-select", "value"),
        Input("replay-interval", "value"),
        Input("runtime-store", "data"),
        Input("replay-runtime-store", "data"),
        Input("result-refresh", "data"),
    )
    def replay_button_state(
        job_name: str | None,
        interval_s: float | None,
        runtime: JsonDict | None,
        replay_runtime: JsonDict | None,
        _refresh: JsonDict,
    ) -> tuple[bool, bool, str]:
        running = bool((replay_runtime or {}).get("running"))
        validation_running = bool((runtime or {}).get("running"))
        job = find_job(job_name)
        run = load_latest_run(job.path) if job is not None else None
        allowed = False
        if job is not None and run is not None and run.status != "ERROR" and interval_s is not None:
            try:
                allowed = estimate_replay(job, run, float(interval_s)).allowed
            except (OSError, ValueError):
                allowed = False
        return (
            running or validation_running or not allowed,
            not running,
            "Replay 생성 중…" if running else "Replay 다시 생성" if run else "Replay 생성",
        )

    @app.callback(
        Output("replay-estimate", "children"),
        Input("job-select", "value"),
        Input("replay-interval", "value"),
        Input("replay-refresh", "data"),
    )
    def render_replay_estimate(
        job_name: str | None,
        interval_s: float | None,
        _refresh: JsonDict,
    ) -> Any:
        job = find_job(job_name)
        run = load_latest_run(job.path) if job is not None else None
        if job is None or run is None or interval_s is None:
            return html.Div("완료된 Validation 결과가 필요합니다.", className="empty-state")
        estimate = estimate_replay(job, run, float(interval_s))
        cards = [
            _metric_card("예상 프레임", f"약 {estimate.frame_count:,}개"),
            _metric_card(
                "예상 생성 시간",
                _duration_range(estimate.time_low_s, estimate.time_high_s),
                "첫 생성은 보수적 추정",
            ),
            _metric_card(
                "예상 용량",
                _size_range(estimate.size_low_bytes, estimate.size_high_bytes),
                "Self-contained HTML",
            ),
        ]
        manifest = read_replay_manifest(run.directory)
        details: list[Any] = []
        if estimate.warning:
            details.append(
                html.P(
                    estimate.warning,
                    className="estimate-warning" if estimate.allowed else "estimate-error",
                )
            )
        if manifest is not None:
            duration = manifest.get("duration_s")
            size = manifest.get("size_bytes")
            frames = manifest.get("frame_count")
            if isinstance(duration, int | float) and isinstance(size, int | float):
                details.append(
                    html.P(
                        f"최근 실측: {float(duration):.1f}초 · "
                        f"{_file_size_value(int(size))} · {int(frames or 0):,}프레임",
                        className="actual-replay-metric",
                    )
                )
        return html.Div([html.Div(cards, className="replay-estimate-grid"), *details])

    @app.callback(
        Output("result-title", "children"),
        Output("result-meta", "children"),
        Output("status-pill", "children"),
        Output("status-pill", "className"),
        Output("metric-grid", "children"),
        Output("result-summary", "children"),
        Output("threshold-list", "children"),
        Output("issues-list", "children"),
        Output("robot-figure", "figure"),
        Output("robot-table", "children"),
        Output("collision-gauge", "figure"),
        Output("collision-timeline", "figure"),
        Output("shape-figure", "figure"),
        Output("artifact-list", "children"),
        Output("image-gallery", "children"),
        Input("job-select", "value"),
        Input("result-refresh", "data"),
        Input("replay-refresh", "data"),
    )
    def render_result(
        job_name: str | None,
        _result_refresh: JsonDict,
        _replay_refresh: JsonDict,
    ) -> tuple[Any, ...]:
        job = find_job(job_name)
        run, payload, load_error = _status_result(job)
        empty = _empty_figure("완료된 Validation 결과가 없습니다")
        if job is None:
            return (
                "검증 가능한 작업이 없습니다",
                "입력 파일 3개를 가진 작업 폴더를 추가하세요.",
                "NO JOB",
                "status-pill status-empty",
                [],
                html.Div("표시할 결과가 없습니다.", className="empty-state"),
                [],
                html.Div("표시할 경고가 없습니다.", className="empty-state"),
                empty,
                html.Div("표시할 지표가 없습니다.", className="empty-state"),
                empty,
                empty,
                empty,
                [],
                [],
            )
        if run is None:
            message = load_error or "아직 완료된 validation이 없습니다."
            return (
                job.name,
                message,
                "READY",
                "status-pill status-ready",
                [_metric_card("상태", "검증 전", "Validation 실행 버튼으로 시작")],
                html.Div("최신 결과가 생성되면 판정 근거가 표시됩니다.", className="empty-state"),
                _threshold_content(load_thresholds(job.path)),
                html.Div("표시할 경고가 없습니다.", className="empty-state"),
                empty,
                html.Div("표시할 지표가 없습니다.", className="empty-state"),
                empty,
                empty,
                empty,
                [],
                [],
            )
        makespan = _number(payload, "schedule", "makespan_s")
        collisions = _number(payload, "collision", "collision_event_count")
        min_tcp = _number(payload, "collision", "minimum_tcp_distance_mm")
        required_tcp = _number(payload, "collision", "minimum_required_distance_mm")
        coverage = _number(payload, "shape", "coverage")
        iou = _number(payload, "shape", "iou")
        failed = _number(payload, "shape", "failed_layer_count")
        evaluated = _number(payload, "shape", "evaluated_layer_count")
        cards = [
            _metric_card("Makespan", f"{makespan:,.1f} s" if makespan is not None else "—"),
            _metric_card("충돌 이벤트", f"{int(collisions):,}" if collisions is not None else "—"),
            _metric_card(
                "최소 TCP 거리",
                f"{min_tcp:,.2f} mm" if min_tcp is not None else "—",
                f"요구 ≥ {required_tcp:,.2f} mm" if required_tcp is not None else "",
            ),
            _metric_card("Coverage", f"{coverage:.2%}" if coverage is not None else "—"),
            _metric_card("IoU", f"{iou:.2%}" if iou is not None else "—"),
            _metric_card(
                "실패 Layer",
                f"{int(failed)} / {int(evaluated)}"
                if failed is not None and evaluated is not None
                else "—",
            ),
        ]
        thresholds = load_thresholds(job.path)
        try:
            robot_rows = read_csv_records(run.directory, "robot_metrics.csv")
            collision_rows = read_csv_records(run.directory, "collision_events.csv")
            layer_rows = read_csv_records(run.directory, "layer_metrics.csv")
            issue_rows = read_csv_records(run.directory, "warnings.csv")
        except DashboardDataError as exc:
            robot_rows, collision_rows, layer_rows, issue_rows = [], [], [], []
            load_error = str(exc)
        issues = _issues_content(issue_rows, payload)
        if load_error:
            issues = html.Div(
                [html.P(load_error, className="error-code"), issues], className="issue-list"
            )
        return (
            job.name,
            f"최신 완료 · {run.completed_label} · {run.run_name}",
            run.status,
            f"status-pill status-{run.status.lower()}",
            cards,
            _summary_content(run, payload),
            _threshold_content(thresholds),
            issues,
            _robot_figure(robot_rows),
            _robot_table(robot_rows),
            _collision_gauge(payload),
            _collision_timeline(collision_rows),
            _shape_figure(layer_rows, thresholds),
            _artifact_content(job, run),
            _gallery_content(job, run),
        )

    @app.callback(
        Output("collision-table", "data"),
        Output("collision-table", "page_count"),
        Input("job-select", "value"),
        Input("result-refresh", "data"),
        Input("collision-table", "page_current"),
        Input("collision-table", "page_size"),
        Input("collision-table", "sort_by"),
        Input("collision-type-filter", "value"),
        Input("collision-pair-filter", "value"),
    )
    def update_collision_table(
        job_name: str | None,
        _refresh: JsonDict,
        page: int,
        page_size: int,
        sort_by: list[dict[str, str]] | None,
        collision_type: str,
        pair: str,
    ) -> tuple[list[JsonDict], int]:
        job = find_job(job_name)
        if job is None:
            return [], 1
        run = load_latest_run(job.path)
        if run is None:
            return [], 1
        equals: dict[str, object] = {"type": collision_type}
        if pair != "ALL":
            left, right = pair.split("-", maxsplit=1)
            equals.update({"robot_a": int(left), "robot_b": int(right)})
        try:
            return read_csv_page(
                run.directory,
                "collision_events.csv",
                page=page,
                page_size=page_size,
                sort_by=sort_by,
                equals=equals,
            )
        except DashboardDataError:
            return [], 1

    @app.callback(
        Output("layer-table", "data"),
        Output("layer-table", "page_count"),
        Input("job-select", "value"),
        Input("result-refresh", "data"),
        Input("layer-table", "page_current"),
        Input("layer-table", "page_size"),
        Input("layer-table", "sort_by"),
        Input("failed-only", "value"),
    )
    def update_layer_table(
        job_name: str | None,
        _refresh: JsonDict,
        page: int,
        page_size: int,
        sort_by: list[dict[str, str]] | None,
        failed_only: list[str],
    ) -> tuple[list[JsonDict], int]:
        job = find_job(job_name)
        if job is None:
            return [], 1
        run = load_latest_run(job.path)
        if run is None:
            return [], 1
        equals: dict[str, object] = {"passed": False} if "failed" in failed_only else {}
        try:
            return read_csv_page(
                run.directory,
                "layer_metrics.csv",
                page=page,
                page_size=page_size,
                sort_by=sort_by,
                equals=equals,
            )
        except DashboardDataError:
            return [], 1

    @app.callback(
        Output("replay-container", "children"),
        Input("detail-tabs", "value"),
        Input("job-select", "value"),
        Input("result-refresh", "data"),
        Input("replay-refresh", "data"),
    )
    def load_replay(
        tab: str,
        job_name: str | None,
        _result_refresh: JsonDict,
        _replay_refresh: JsonDict,
    ) -> Any:
        if tab != "artifacts":
            return html.Div("3D 및 산출물 탭에서 필요할 때 불러옵니다.", className="empty-state")
        job = find_job(job_name)
        if job is None:
            return html.Div("선택된 작업이 없습니다.", className="empty-state")
        run = load_latest_run(job.path)
        replay_runtime = replay_manager.snapshot()
        if replay_runtime.get("running") and replay_runtime.get("job_name") == job.name:
            return html.Div(
                "Replay를 생성 중입니다. 완료되면 자동으로 표시됩니다.",
                className="empty-state replay-empty",
            )
        if run is None or not (run.directory / "replay.html").is_file():
            return html.Div(
                [
                    html.Strong("이 결과에는 replay가 없습니다."),
                    html.P("위의 간격과 예상치를 확인한 뒤 Replay 생성 버튼을 누르세요."),
                ],
                className="empty-state replay-empty",
            )
        return html.Iframe(
            src=_artifact_url(job, run, "replay.html"),
            title=f"{job.name} 3D replay",
            className="replay-frame",
        )

    return app


def _open_browser_when_ready(url: str) -> None:
    for _ in range(50):
        try:
            with urlopen(url, timeout=0.5) as response:  # noqa: S310 - fixed local URL.
                if response.status < 500:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.1)


def run_dashboard(
    jobs_root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8050,
    open_browser: bool = True,
) -> None:
    """Start the local dashboard and optionally open the default browser."""
    app = create_dashboard_app(jobs_root)
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{browser_host}:{port}/"
    if open_browser:
        threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
    app.run(host=host, port=port, debug=False)

"""Single-job WAAM Validator local web interface."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote
from urllib.request import urlopen

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html, no_update
from dash.exceptions import PreventUpdate
from flask import abort, send_file

from .._version import __version__
from ..config.loader import load_config
from ..visualization.replay import _capsule_xy
from .app import (
    _collision_timeline,
    _empty_figure,
    _inspection_content,
    _issues_content,
    _metric_card,
    _robot_figure,
    _shape_figure,
    _style_figure,
    _table_style,
    _threshold_content,
)
from .data import (
    ALLOWED_ARTIFACTS,
    DashboardDataError,
    JobRecord,
    RunRecord,
    load_latest_run,
    load_run_directory,
    load_thresholds,
    read_csv_page,
    read_csv_records,
    resolve_single_job_artifact,
)
from .input_inspector import (
    inspect_dashboard_input_bundle,
    inspection_is_current,
    resolve_input_directory,
)
from .preview import build_input_preview
from .replay_service import (
    estimate_replay,
    preset_interval_s,
    verify_validation_inputs,
)
from .runner import (
    ReplayAlreadyRunningError,
    ReplayRunManager,
    ValidationAlreadyRunningError,
    ValidationRunManager,
)

JsonDict = dict[str, Any]
DataTable: Any = dash_table.DataTable  # type: ignore[attr-defined]
_GRAPH_CONFIG: Any = {"displaylogo": False, "responsive": True}
_ARTIFACT_LABELS = {
    "summary.json": "종합 판정 데이터",
    "validation_report.md": "검증 보고서",
    "robot_metrics.csv": "로봇 작업·Reach 수치",
    "collision_events.csv": "충돌 이벤트 수치",
    "layer_metrics.csv": "Layer 형상 수치",
    "warnings.csv": "경고·세부 판정 기록",
    "run.log": "실행 기록",
    "deposited.stl": "명목 적층 형상 STL",
    "overview_xy.png": "Robot XY 경로 그림",
    "gantt.png": "로봇 작업 일정 그림",
    "shape_metrics_by_layer.png": "Layer 형상 지표 그림",
    "worst_layer_comparison.png": "최악 Layer 비교 그림",
    "arm_envelope_worst_case.png": "Arm Envelope 최악 시점 그림",
    "replay.html": "3D Replay HTML",
    "validation_inputs.json": "입력 파일 지문",
    "error.json": "실행 오류 데이터",
}
_STAGE_LABELS = {
    "starting": "프로세스 시작",
    "loading_inputs": "입력 로딩",
    "collision": "충돌 검사",
    "deposition": "적층 형상 생성",
    "target_slicing": "Target slicing",
    "shape_metrics": "형상 지표 계산",
    "artifacts": "산출물 생성",
    "completed": "완료",
    "failed": "오류",
    "process_exit": "프로세스 종료",
}


class _ContextRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paths: dict[str, Path] = {}

    def add(self, path: Path) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._paths[token] = path.resolve()
        return token

    def get(self, token: str) -> Path:
        with self._lock:
            path = self._paths.get(token)
        if path is None:
            raise DashboardDataError("알 수 없거나 만료된 입력 context입니다.")
        return path


def _screen(class_name: str, screen_id: str, children: list[Any]) -> html.Section:
    return html.Section(children, id=screen_id, className=class_name)


def _layout(initial_job_dir: Path) -> html.Div:
    empty_scene = _empty_figure("입력 확인 후 3D 작업 공간을 표시합니다.", height=680)
    empty_chart = _empty_figure("입력 확인 후 통계를 표시합니다.")
    return html.Div(
        [
            dcc.Store(id="view-store", data={"view": "input"}),
            dcc.Store(id="active-input-store", data={}),
            dcc.Store(id="selected-job-dir-store", data=str(initial_job_dir.resolve())),
            dcc.Store(id="current-run-store", data={}),
            dcc.Store(id="recent-run-store", data={}),
            dcc.Store(id="runtime-store", data={"state": "IDLE", "running": False}),
            dcc.Store(id="replay-runtime-store", data={"state": "IDLE", "running": False}),
            dcc.Store(id="replay-refresh", data={}),
            dcc.Interval(id="validation-poller", interval=1000, disabled=True),
            dcc.Interval(id="replay-poller", interval=1000, disabled=True),
            html.Header(
                [
                    html.Div("WV", className="brand-mark"),
                    html.Div(
                        [
                            html.H1("WAAM Validator"),
                            html.P(f"v{__version__} · 단일 WAAM 작업 검증"),
                        ],
                        className="brand-copy",
                    ),
                    html.Div(
                        [
                            html.Span("1 입력 준비"),
                            html.Span("2 Validation"),
                            html.Span("3 결과"),
                        ],
                        id="workflow-steps",
                        className="workflow-steps step-input",
                    ),
                ],
                className="validator-header",
            ),
            html.Main(
                [
                    _screen(
                        "validator-screen",
                        "input-screen",
                        [
                            html.Div(
                                [
                                    html.P("입력 준비", className="eyebrow"),
                                    html.H2("검증할 입력 폴더를 확인하세요"),
                                    html.P(
                                        "세 개의 고정 입력 파일을 원본 위치에서 직접 읽습니다.",
                                        className="muted-copy",
                                    ),
                                ],
                                className="screen-heading",
                            ),
                            html.Section(
                                [
                                    html.Label("입력 폴더", htmlFor="job-dir-input"),
                                    html.Div(
                                        [
                                            dcc.Input(
                                                 id="job-dir-input",
                                                 value=str(initial_job_dir.resolve()),
                                                 type="text",
                                                 # Keep the callback state synchronized when a
                                                 # pasted path is followed immediately by a click.
                                                 debounce=False,
                                             ),
                                            html.Button(
                                                "찾아보기",
                                                id="browse-button",
                                                className="secondary-button",
                                            ),
                                            html.Button("입력 확인", id="inspect-button"),
                                        ],
                                        className="folder-row",
                                    ),
                                    html.Div(
                                        [
                                            html.Code("config.yaml"),
                                            html.Code("trajectory.csv"),
                                            html.Code("target.stl"),
                                        ],
                                        className="required-files",
                                    ),
                                    html.P(id="folder-message", className="field-message"),
                                ],
                                className="folder-card",
                            ),
                            dcc.Loading(
                                html.Div(
                                    "입력 확인을 실행하면 상세 정보와 preview가 나타납니다.",
                                    id="inspection-content",
                                    className="input-inspection-empty",
                                ),
                                type="circle",
                            ),
                            html.Section(
                                [
                                    html.Div(
                                        [
                                            html.H3("로봇 작업 일정 (Gantt)"),
                                            html.P(
                                                "각 Robot을 하나의 가로 막대로 표시합니다. "
                                                "Deposition은 적층, Travel은 비적층 이동, "
                                                "Wait는 위치 유지 대기, 완료 후 비활성은 "
                                                "해당 Robot의 trajectory 종료 이후를 뜻합니다. "
                                                "짧은 상태도 보이도록 처음에는 자동 확대하며, "
                                                "하단 범위 조절기 또는 전체 보기 버튼으로 시간축을 "
                                                "이동할 수 있습니다. 마우스를 올리면 상태의 "
                                                "시작·종료·지속시간을 볼 수 있습니다.",
                                                className="muted-copy",
                                            ),
                                        ],
                                        className="panel-heading",
                                    ),
                                    dcc.Graph(
                                        id="input-gantt",
                                        figure=empty_chart,
                                        config=_GRAPH_CONFIG,
                                    ),
                                ],
                                className="panel input-gantt-panel",
                            ),
                            html.Section(
                                [
                                    html.Div(
                                        [
                                            html.H3("입력 형상 및 로봇 작업 공간"),
                                            html.P(id="preview-note", className="muted-copy"),
                                        ],
                                        className="panel-heading",
                                    ),
                                    dcc.Graph(
                                        id="input-scene", figure=empty_scene, config=_GRAPH_CONFIG
                                    ),
                                ],
                                className="panel preview-panel",
                            ),
                            html.Div(
                                [
                                    html.Section(
                                        [
                                            html.H3("로봇별 상태 시간 비율"),
                                             html.P(
                                                 "각 로봇의 Deposition / Travel / Wait 시간이 "
                                                 "전체 Makespan에서 차지하는 비율입니다. 먼저 "
                                                 "끝난 로봇의 잔여 시간은 Wait가 아니라 완료 후 "
                                                 "비활성으로 구분합니다.",
                                                 className="muted-copy",
                                             ),
                                            dcc.Graph(
                                                id="input-time-chart",
                                                figure=empty_chart,
                                                config=_GRAPH_CONFIG,
                                            ),
                                        ],
                                        className="panel",
                                    ),
                                    html.Section(
                                        [
                                            html.H3("로봇별 경로 길이"),
                                            html.P(
                                                "거리 = 구간 시간 × 실제 평균 속도입니다. Travel이 "
                                                "더 빠르면 시간 비율이 작아도 누적 거리는 더 클 수 "
                                                "있습니다.",
                                                className="muted-copy",
                                            ),
                                            dcc.Graph(
                                                id="input-motion-chart",
                                                figure=empty_chart,
                                                config=_GRAPH_CONFIG,
                                            ),
                                        ],
                                        className="panel",
                                    ),
                                    html.Section(
                                        [
                                            html.H3("로봇별 Reach 사용률"),
                                            html.P(
                                                "Robot Base에서 trajectory의 가장 먼 TCP까지의 "
                                                "거리 ÷ 설정 Reach입니다. 100%를 초과하면 "
                                                "Reach 판정이 FAIL입니다.",
                                                className="muted-copy",
                                            ),
                                            dcc.Graph(
                                                id="input-reach-chart",
                                                figure=empty_chart,
                                                config=_GRAPH_CONFIG,
                                            ),
                                        ],
                                        className="panel",
                                    ),
                                ],
                                className="input-chart-grid",
                            ),
                            html.Div(
                                [
                                    html.Button(
                                        "Validation 실행",
                                        id="validation-button",
                                        type="button",
                                        disabled=True,
                                    ),
                                    html.Button(
                                        "최근 결과 보기",
                                        id="recent-result-button",
                                        disabled=True,
                                        className="secondary-button",
                                    ),
                                    html.Span(
                                        id="recent-result-readiness",
                                        className="field-message",
                                    ),
                                    html.Span(id="validation-readiness", className="field-message"),
                                    html.Span(
                                        id="validation-start-message",
                                        className="field-message validation-start-message",
                                    ),
                                ],
                                className="input-footer-actions",
                            ),
                        ],
                    ),
                    _screen(
                        "validator-screen is-hidden",
                        "progress-screen",
                        [
                            html.Div(
                                [
                                    html.P("검증 진행", className="eyebrow"),
                                    html.H2(
                                        id="progress-title",
                                        children="Validation을 시작하고 있습니다",
                                    ),
                                    html.P(id="progress-output", className="muted-copy"),
                                ],
                                className="screen-heading",
                            ),
                            html.Section(
                                [
                                    html.Div(
                                        [
                                            html.Strong(id="overall-progress-label", children="0%"),
                                            html.Span(
                                                id="progress-stage", children="프로세스 시작"
                                            ),
                                        ],
                                        className="progress-heading",
                                    ),
                                    html.Progress(id="overall-progress", value="0", max="100"),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Span("단계 진행률"),
                                                    html.Strong(
                                                        id="stage-progress-label", children="0%"
                                                    ),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Span("처리량"),
                                                    html.Strong(id="progress-units", children="—"),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Span("경과 시간"),
                                                    html.Strong(
                                                        id="progress-elapsed", children="0.0 s"
                                                    ),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Span("예상 잔여"),
                                                    html.Strong(
                                                        id="progress-eta", children="계산 중"
                                                    ),
                                                ]
                                            ),
                                        ],
                                        className="progress-metrics",
                                    ),
                                    html.P(id="progress-message", className="progress-message"),
                                ],
                                className="progress-card",
                            ),
                            html.Pre(id="progress-log", className="live-log"),
                        ],
                    ),
                    _screen(
                        "validator-screen is-hidden",
                        "result-screen",
                        [
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.P("검증 결과", className="eyebrow"),
                                            html.H2("검증 결과"),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Button(
                                                "처음으로 돌아가기",
                                                id="new-input-button",
                                                className="secondary-button",
                                            ),
                                        ],
                                        className="result-actions",
                                    ),
                                ],
                                className="result-topline",
                            ),
                            html.Div(id="result-content"),
                        ],
                    ),
                ],
                className="validator-main",
            ),
        ],
        className="validator-shell",
    )


def _format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:,.1f} {unit}"
        size /= 1024.0
    return f"{size:,.1f} GB"


def _replay_estimate_text(job: JobRecord, run: RunRecord, interval_s: float) -> str:
    try:
        estimate = estimate_replay(job, run, interval_s)
    except (DashboardDataError, OSError, ValueError) as exc:
        return str(exc)
    return (
        f"예상 {estimate.frame_count:,} frames · "
        f"생성 시간 {estimate.time_low_s:,.0f}–{estimate.time_high_s:,.0f} s · "
        f"파일 크기 {_format_size(estimate.size_low_bytes)}–"
        f"{_format_size(estimate.size_high_bytes)}"
        + (f" · {estimate.warning}" if estimate.warning else "")
    )


def _preview_note(preview: Any) -> str:
    point_text = ", ".join(
        f"R{robot_id} {count:,}점" for robot_id, count in preview.trajectory_points_shown.items()
    )
    base = (
        f"Target {preview.target_faces_shown:,}/{preview.target_faces_total:,} faces · "
        f"Trajectory preview {point_text}"
    )
    return base + (" · " + " ".join(preview.warnings) if preview.warnings else "")


def _artifact_url(token: str, run: RunRecord, filename: str) -> str:
    return f"/artifacts/{quote(token)}/{quote(run.run_name)}/{quote(filename)}"


def _table_options(page_size: int, page_count: int = 1) -> JsonDict:
    options = _table_style()
    options.update(page_size=page_size, page_count=max(1, page_count))
    return options


def _reach_display_rows(rows: list[JsonDict]) -> list[JsonDict]:
    """Format reach summary values without changing the stored result schema."""
    formatted: list[JsonDict] = []
    for row in rows:
        formatted.append(
            {
                "robot_id": f"R{int(row.get('robot_id', 0))}",
                "passed": "PASS" if bool(row.get("passed")) else "FAIL",
                "reach_radius_mm": f"{float(row.get('reach_radius_mm', 0.0)):,.2f}",
                "maximum_reach_mm": f"{float(row.get('maximum_reach_mm', 0.0)):,.2f}",
                "minimum_margin_mm": f"{float(row.get('minimum_margin_mm', 0.0)):,.2f}",
                "utilization_ratio": f"{float(row.get('utilization_ratio', 0.0)):.2%}",
                "violation_point_count": f"{int(row.get('violation_point_count', 0)):,}",
            }
        )
    return formatted


def _layer_display_rows(rows: list[JsonDict]) -> list[JsonDict]:
    """Format layer metrics for scanning while retaining numeric CSV sorting."""
    formatted: list[JsonDict] = []
    for row in rows:
        formatted.append(
            {
                "layer_index": int(row.get("layer_index", 0)),
                "z_slice_mm": f"{float(row.get('z_slice_mm', 0.0)):,.2f}",
                "coverage": f"{float(row.get('coverage', 0.0)):.4%}",
                "underfill_ratio": f"{float(row.get('underfill_ratio', 0.0)):.4%}",
                "overfill_ratio": (
                    "—"
                    if row.get("overfill_ratio") is None
                    else f"{float(row['overfill_ratio']):.4%}"
                ),
                "iou": f"{float(row.get('iou', 0.0)):.4%}",
                "passed": "PASS" if bool(row.get("passed")) else "FAIL",
            }
        )
    return formatted


def _collision_display_rows(rows: list[JsonDict]) -> list[JsonDict]:
    """Format collision events consistently for initial and paged table data."""
    formatted: list[JsonDict] = []
    for row in rows:
        formatted.append(
            {
                "event_id": int(row.get("event_id", 0)),
                "type": str(row.get("type", "")),
                "robot_a": f"R{int(row.get('robot_a', 0))}",
                "robot_b": f"R{int(row.get('robot_b', 0))}",
                "start_s": f"{float(row.get('start_s', 0.0)):,.3f}",
                "end_s": f"{float(row.get('end_s', 0.0)):,.3f}",
                "duration_s": f"{float(row.get('duration_s', 0.0)):,.3f}",
                "minimum_distance_mm": f"{float(row.get('minimum_distance_mm', 0.0)):,.2f}",
                "required_distance_mm": f"{float(row.get('required_distance_mm', 0.0)):,.2f}",
                "minimum_safety_margin_mm": (
                    f"{float(row.get('minimum_safety_margin_mm', 0.0)):,.2f}"
                ),
                "minimum_capsule_surface_clearance_mm": (
                    "—"
                    if row.get("minimum_capsule_surface_clearance_mm") is None
                    else f"{float(row['minimum_capsule_surface_clearance_mm']):,.2f}"
                ),
                "minimum_distance_time_s": (
                    f"{float(row.get('minimum_distance_time_s', 0.0)):,.3f}"
                ),
                "closest_a_xy_mm": (
                    f"({float(row.get('closest_a_x_mm', 0.0)):,.2f}, "
                    f"{float(row.get('closest_a_y_mm', 0.0)):,.2f})"
                ),
                "closest_b_xy_mm": (
                    f"({float(row.get('closest_b_x_mm', 0.0)):,.2f}, "
                    f"{float(row.get('closest_b_y_mm', 0.0)):,.2f})"
                ),
            }
        )
    return formatted


def _status_metric_card(label: str, passed: object, detail: str) -> html.Div:
    verdict = "✓ PASS" if passed is True else "✕ FAIL" if passed is False else "— 확인 불가"
    tone = "pass" if passed is True else "fail" if passed is False else "unknown"
    return html.Div(
        [
            html.Div(label, className="metric-label"),
            html.Div(verdict, className="metric-value"),
            html.Div(detail, className="metric-detail"),
        ],
        className=f"metric-card result-primary-card check-{tone}",
    )


def _duration_label(seconds: float) -> str:
    rounded = max(0, int(round(seconds)))
    hours, remainder = divmod(rounded, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}시간 {minutes:02d}분 {remaining_seconds:02d}초"
    if minutes:
        return f"{minutes}분 {remaining_seconds:02d}초"
    return f"{remaining_seconds}초"


def _pair_label(value: object) -> str:
    if isinstance(value, list) and len(value) == 2:
        return f"R{value[0]}–R{value[1]}"
    return "Robot pair 확인 불가"


def _robot_result_rows(robot_rows: list[JsonDict], reach_rows: list[JsonDict]) -> list[JsonDict]:
    reach_by_robot = {
        int(row.get("robot_id", 0)): row
        for row in reach_rows
        if isinstance(row.get("robot_id"), int | float | str)
    }
    combined: list[JsonDict] = []
    for robot in robot_rows:
        robot_id = int(robot.get("robot_id", 0))
        reach = reach_by_robot.get(robot_id, {})
        combined.append(
            {
                "robot_id": f"R{robot_id}",
                "completion_s": f"{float(robot.get('completion_s', 0.0)):,.2f}",
                "state_time_s": (
                    f"{float(robot.get('deposition_time_s', 0.0)):,.1f} / "
                    f"{float(robot.get('travel_time_s', 0.0)):,.1f} / "
                    f"{float(robot.get('wait_time_s', 0.0)):,.1f}"
                ),
                "path_length_mm": (
                    f"{float(robot.get('deposition_length_mm', 0.0)):,.1f} / "
                    f"{float(robot.get('travel_length_mm', 0.0)):,.1f}"
                ),
                "mean_speed_mm_s": (
                    f"{float(robot.get('mean_deposition_speed_mm_s', 0.0) or 0.0):,.2f} / "
                    f"{float(robot.get('mean_travel_speed_mm_s', 0.0) or 0.0):,.2f}"
                ),
                "reach_use": (
                    f"{float(reach.get('maximum_reach_mm', 0.0)):,.1f} / "
                    f"{float(reach.get('reach_radius_mm', 0.0)):,.1f} mm · "
                    f"{float(reach.get('utilization_ratio', 0.0)):.1%}"
                ),
                "reach_margin_mm": f"{float(reach.get('minimum_margin_mm', 0.0)):,.2f}",
                "reach_result": "PASS" if bool(reach.get("passed")) else "FAIL",
            }
        )
    return combined


def _collision_evidence_card(label: str, data: JsonDict, *, arm: bool) -> html.Div:
    enabled = bool(data.get("enabled", False))
    passed = bool(data.get("passed", False))
    distance_key = "centerline_distance_at_worst_mm" if arm else "minimum_distance_mm"
    required_key = "required_distance_at_worst_mm" if arm else "required_distance_at_minimum_mm"
    distance = float(data.get(distance_key, 0.0))
    required = float(data.get(required_key, 0.0))
    margin = distance - required
    verdict = "검사 꺼짐" if not enabled else "PASS" if passed else "FAIL"
    tone = "unknown" if not enabled else "pass" if passed else "fail"
    time_s = float(data.get("time_s", 0.0))
    return html.Div(
        [
            html.Div(
                [html.H3(label), html.Strong(verdict)],
                className="safety-card-heading",
            ),
            html.Div(
                [
                    html.Div([html.Span("측정 최소거리"), html.Strong(f"{distance:,.2f} mm")]),
                    html.Div([html.Span("요구 최소거리"), html.Strong(f"{required:,.2f} mm")]),
                    html.Div([html.Span("안전 여유"), html.Strong(f"{margin:,.2f} mm")]),
                ],
                className="safety-values",
            ),
            html.P(
                f"최악 조건: {_pair_label(data.get('pair'))} · {time_s:,.3f} s · "
                f"이벤트 {int(data.get('event_count', 0)):,}건",
                className="muted-copy",
            ),
        ],
        className=f"safety-card check-{tone}",
    )


def _result_findings_content(payload: JsonDict, issue_rows: list[JsonDict]) -> html.Div:
    reasons = payload.get("failure_reasons", [])
    reason_list = [str(reason) for reason in reasons] if isinstance(reasons, list) else []
    warning_rows = [
        row for row in issue_rows if str(row.get("severity", "warning")).lower() != "error"
    ]
    error_rows = [
        row for row in issue_rows if str(row.get("severity", "warning")).lower() == "error"
    ]
    payload_warnings = payload.get("warnings", [])
    payload_errors = payload.get("errors", [])
    warning_count = len(warning_rows) or (
        len(payload_warnings) if isinstance(payload_warnings, list) else 0
    )
    error_count = len(error_rows) or (
        len(payload_errors) if isinstance(payload_errors, list) else 0
    )
    children: list[Any] = []
    if reason_list:
        children.extend(
            [
                html.H4("FAIL 사유"),
                html.Ol([html.Li(reason) for reason in reason_list], className="failure-list"),
            ]
        )
    else:
        children.append(
            html.Div(
                [html.Strong("PASS"), html.Span("활성화된 모든 판정 기준을 충족했습니다.")],
                className="result-clear-message",
            )
        )
    if warning_count:
        warning_payload = {"warnings": payload_warnings, "errors": []}
        children.extend(
            [
                html.H4("경고와 추가 확인사항"),
                _issues_content(warning_rows, warning_payload),
            ]
        )
    if error_count:
        error_payload = {"warnings": [], "errors": payload_errors}
        error_content = _issues_content(error_rows, error_payload)
        if reason_list:
            children.append(
                html.Details(
                    [
                        html.Summary(f"FAIL 세부 판정 기록 {error_count:,}건 보기"),
                        error_content,
                    ],
                    className="result-issue-details",
                )
            )
        else:
            children.extend([html.H4("세부 판정 기록"), error_content])
    if not reason_list and not warning_count and not error_count:
        children.append(html.P("별도로 확인할 경고나 오류가 없습니다.", className="muted-copy"))
    return html.Div(children, className="result-findings")


def _arm_envelope_snapshot(payload: JsonDict, job_dir: Path) -> go.Figure:
    collision = payload.get("collision")
    arm = collision.get("arm_envelope") if isinstance(collision, dict) else None
    if not isinstance(arm, dict):
        return _empty_figure("Arm Envelope 최악 시점 정보가 없습니다", height=520)
    tcp_positions = arm.get("tcp_positions_xy_mm")
    closest = arm.get("closest_points_xy_mm")
    if not isinstance(tcp_positions, list) or len(tcp_positions) != 3:
        return _empty_figure("Arm Envelope TCP 위치 정보가 없습니다", height=520)
    try:
        config = load_config(job_dir / "config.yaml")
        figure = go.Figure()
        workspace_x, workspace_y = _capsule_xy(
            config.workspace.center_xy_mm,
            config.workspace.center_xy_mm,
            config.workspace.radius_mm,
        )
        figure.add_trace(
            go.Scatter(
                x=workspace_x,
                y=workspace_y,
                mode="lines",
                fill="toself",
                fillcolor="rgba(56,189,248,0.06)",
                line={"color": "rgba(56,189,248,0.55)", "dash": "dot"},
                name="Workspace",
            )
        )
        bases = [robot.base_xyz_mm[:2] for robot in config.robots]
        triangle = [*bases, bases[0]]
        figure.add_trace(
            go.Scatter(
                x=[point[0] for point in triangle],
                y=[point[1] for point in triangle],
                mode="lines+markers",
                line={"color": "#64748b", "dash": "dot"},
                marker={"symbol": "triangle-up", "size": 9},
                name="Robot bases",
            )
        )
        colors = ("#2f8fff", "#ff9d42", "#34d399")
        for index, (robot, tcp_raw) in enumerate(
            zip(config.robots, tcp_positions, strict=True)
        ):
            if not isinstance(tcp_raw, list) or len(tcp_raw) != 2:
                continue
            tcp = (float(tcp_raw[0]), float(tcp_raw[1]))
            physical_x, physical_y = _capsule_xy(
                robot.base_xyz_mm[:2], tcp, robot.arm_envelope_radius_mm
            )
            decision_x, decision_y = _capsule_xy(
                robot.base_xyz_mm[:2],
                tcp,
                robot.arm_envelope_radius_mm + config.collision.arm_clearance_mm / 2.0,
            )
            figure.add_trace(
                go.Scatter(
                    x=physical_x,
                    y=physical_y,
                    mode="lines",
                    fill="toself",
                    fillcolor=(
                        f"rgba({int(colors[index][1:3], 16)},"
                        f"{int(colors[index][3:5], 16)},"
                        f"{int(colors[index][5:7], 16)},0.22)"
                    ),
                    line={"color": colors[index], "width": 1},
                    name=f"R{robot.id} 실제 Capsule",
                )
            )
            figure.add_trace(
                go.Scatter(
                    x=decision_x,
                    y=decision_y,
                    mode="lines",
                    line={"color": colors[index], "width": 2, "dash": "dash"},
                    name=f"R{robot.id} 판정 외곽선",
                )
            )
            figure.add_trace(
                go.Scatter(
                    x=[robot.base_xyz_mm[0], tcp[0]],
                    y=[robot.base_xyz_mm[1], tcp[1]],
                    mode="lines+markers",
                    line={"color": colors[index], "width": 2},
                    marker={"size": 6},
                    name=f"R{robot.id} 중심선",
                    showlegend=False,
                )
            )
        if isinstance(closest, list) and len(closest) == 2:
            left, right = closest
            if isinstance(left, list) and isinstance(right, list):
                passed = bool(arm.get("passed", False))
                figure.add_trace(
                    go.Scatter(
                        x=[left[0], right[0]],
                        y=[left[1], right[1]],
                        mode="lines+markers",
                        line={"color": "#21d4a3" if passed else "#ff6376", "width": 4},
                        marker={"size": 8},
                        name="최단 중심선 거리",
                    )
                )
        pair = arm.get("pair", [])
        pair_label = (
            f"R{pair[0]}–R{pair[1]}" if isinstance(pair, list) and len(pair) == 2 else "—"
        )
        figure.update_layout(
            title=(
                f"{pair_label} · {float(arm.get('time_s', 0.0)):,.3f} s · "
                f"안전 여유 {float(arm.get('minimum_safety_margin_mm', 0.0)):,.3f} mm · "
                f"{'PASS' if arm.get('passed') else 'FAIL'}"
            ),
            xaxis_title="X [mm]",
            yaxis_title="Y [mm]",
        )
        figure.update_yaxes(scaleanchor="x", scaleratio=1.0)
        return _style_figure(figure, height=520)
    except (OSError, ValueError):
        return _empty_figure("Arm Envelope 최악 시점을 표시할 수 없습니다", height=520)


def _shape_summary_cards(shape: JsonDict, layer_rows: list[JsonDict]) -> list[html.Div]:
    maximum_underfill = max(
        (float(row.get("underfill_ratio", 0.0) or 0.0) for row in layer_rows),
        default=0.0,
    )
    maximum_overfill = max(
        (float(row.get("overfill_ratio", 0.0) or 0.0) for row in layer_rows),
        default=0.0,
    )
    return [
        _metric_card("전체 Coverage", f"{float(shape.get('coverage', 0.0)):.4%}"),
        _metric_card("전체 IoU", f"{float(shape.get('iou', 0.0)):.4%}"),
        _metric_card("최대 Layer Underfill", f"{maximum_underfill:.4%}"),
        _metric_card("최대 Layer Overfill", f"{maximum_overfill:.4%}"),
        _metric_card(
            "실패 Layer",
            f"{int(shape.get('failed_layer_count', 0)):,} / "
            f"{int(shape.get('evaluated_layer_count', 0)):,}",
            f"비율 {float(shape.get('failed_layer_ratio', 0.0)):.2%}",
        ),
    ]


def _result_view(token: str, job: JobRecord, run: RunRecord) -> html.Div:
    payload = run.payload
    if payload.get("schema_version") != "2.0":
        return html.Div(
            [
                html.Div(
                    [
                        html.Strong("재검증 필요"),
                        html.Span(job.path.name),
                        html.Span(run.completed_label),
                    ],
                    className="result-status result-error",
                ),
                html.Section(
                    [
                        html.H3("현재 Capsule 결과 형식이 아닙니다"),
                        html.P(
                            "이 결과는 WAAM Validator 결과 schema 2.0보다 이전 형식입니다. "
                            "입력 파일은 삭제되거나 변경되지 않았으며, 2D Arm Capsule 판정을 "
                            "포함하려면 Validation을 다시 실행해야 합니다."
                        ),
                    ],
                    className="panel error-result-panel",
                ),
            ]
        )
    if run.status == "ERROR":
        artifact_links = [
            html.A(
                [html.Strong(filename), html.Span(_format_size(path.stat().st_size))],
                href=_artifact_url(token, run, filename),
                target="_blank",
                className="artifact-link",
            )
            for filename in ("error.json", "run.log")
            for path in [run.directory / filename]
            if path.is_file()
        ]
        return html.Div(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.Strong("ERROR"),
                                html.Span(job.path.name),
                                html.Span(run.completed_label),
                            ],
                            className="result-status result-error",
                        ),
                        html.Code(str(run.directory)),
                    ],
                    className="result-banner",
                ),
                html.Section(
                    [
                        html.H3("Validation 실행 오류"),
                        html.Code(
                            str(payload.get("code", "VALIDATION_ERROR")),
                            className="error-code",
                        ),
                        html.P(str(payload.get("message", run.load_error or "알 수 없는 오류"))),
                        html.P(
                            "상세 실행 기록은 아래 error.json과 run.log에서 확인할 수 있습니다.",
                            className="muted-copy",
                        ),
                        html.Div(artifact_links, className="artifact-list"),
                    ],
                    className="panel error-result-panel",
                ),
            ]
        )
    schedule = payload.get("schedule", {}) if isinstance(payload.get("schedule"), dict) else {}
    collision = payload.get("collision", {}) if isinstance(payload.get("collision"), dict) else {}
    shape = payload.get("shape", {}) if isinstance(payload.get("shape"), dict) else {}
    reach = payload.get("reach", {}) if isinstance(payload.get("reach"), dict) else {}
    csv_errors: list[JsonDict] = []

    def load_rows(filename: str) -> list[JsonDict]:
        try:
            return read_csv_records(run.directory, filename)
        except DashboardDataError as exc:
            csv_errors.append(
                {
                    "severity": "WARNING",
                    "code": "RESULT_CSV_LOAD_ERROR",
                    "message": str(exc),
                    "robot_id": None,
                    "start_s": None,
                    "end_s": None,
                }
            )
            return []

    robot_rows = load_rows("robot_metrics.csv")
    collision_rows = load_rows("collision_events.csv")
    layer_rows = load_rows("layer_metrics.csv")
    issue_rows = load_rows("warnings.csv") + csv_errors
    status = run.status
    arm = (
        collision.get("arm_envelope", {})
        if isinstance(collision.get("arm_envelope"), dict)
        else {}
    )
    tcp = (
        collision.get("tcp_radius", {})
        if isinstance(collision.get("tcp_radius"), dict)
        else {}
    )
    minimum_tcp = float(tcp.get("minimum_distance_mm", 0.0))
    required_tcp = float(tcp.get("required_distance_at_minimum_mm", 0.0))
    tcp_margin = minimum_tcp - required_tcp
    failed_layer_count = int(shape.get("failed_layer_count", 0))
    evaluated_layer_count = int(shape.get("evaluated_layer_count", 0))
    makespan_s = float(schedule.get("makespan_s", 0.0))
    reach_rows = reach.get("robots", []) if isinstance(reach.get("robots"), list) else []
    typed_reach_rows = [row for row in reach_rows if isinstance(row, dict)]
    worst_reach = min(
        typed_reach_rows,
        key=lambda row: float(row.get("minimum_margin_mm", 0.0)),
        default={},
    )
    worst_reach_margin = float(worst_reach.get("minimum_margin_mm", 0.0))
    overall_coverage = float(shape.get("coverage", 0.0))
    overall_iou = float(shape.get("iou", 0.0))
    target_volume = float(shape.get("target_volume_mm3", 0.0))
    deposited_volume = float(shape.get("deposited_volume_mm3", 0.0))
    arm_events = int(arm.get("event_count", 0))
    tcp_events = int(tcp.get("event_count", 0))
    cards = [
        _metric_card(
            "전체 작업시간",
            _duration_label(makespan_s),
            f"Makespan {makespan_s:,.2f} s",
        ),
        _status_metric_card(
            "Robot Reach",
            reach.get("passed"),
            f"최소 여유 {worst_reach_margin:,.2f} mm · "
            f"R{int(worst_reach.get('robot_id', 0))}",
        ),
        _status_metric_card(
            "로봇 간 충돌 안전",
            collision.get("passed"),
            f"이벤트 Arm {arm_events:,}건 / TCP {tcp_events:,}건 · "
            f"최소 여유 Arm {float(arm.get('minimum_safety_margin_mm', 0.0)):,.1f} mm / "
            f"TCP {tcp_margin:,.1f} mm",
        ),
        _status_metric_card(
            "적층 형상",
            shape.get("passed"),
            f"Coverage {overall_coverage:.2%} · "
            f"IoU {overall_iou:.2%} · "
            f"실패 Layer {failed_layer_count:,}/{evaluated_layer_count:,}",
        ),
    ]
    robot_result_rows = _robot_result_rows(robot_rows, typed_reach_rows)
    artifact_links = []
    for filename in sorted(ALLOWED_ARTIFACTS):
        path = run.directory / filename
        if path.is_file() and filename not in {"dashboard_status.json", "replay_status.json"}:
            artifact_links.append(
                html.A(
                    [
                        html.Div(
                            [
                                html.Strong(_ARTIFACT_LABELS.get(filename, filename)),
                                html.Small(filename),
                            ]
                        ),
                        html.Span(_format_size(path.stat().st_size)),
                    ],
                    href=_artifact_url(token, run, filename),
                    target="_blank",
                    className="artifact-link",
                )
            )
    replay = (
        html.Iframe(
            src=_artifact_url(token, run, "replay.html"),
            title="WAAM 3D replay",
            className="replay-frame",
        )
        if (run.directory / "replay.html").is_file()
        else html.Div("이 결과에는 Replay가 없습니다.", className="empty-state")
    )
    replay_interval = preset_interval_s(float(schedule.get("makespan_s", 0.0)), "standard")
    if collision_rows:
        collision_event_sections: list[html.Section] = [
            html.Section(
                [
                    html.H3("충돌 이벤트 시간축"),
                    dcc.Graph(
                        figure=_collision_timeline(collision_rows),
                        config=_GRAPH_CONFIG,
                    ),
                ],
                className="panel",
            ),
            html.Section(
                [
                    html.H3("충돌 이벤트 상세"),
                    DataTable(
                        id="collision-table",
                        columns=[
                            {"name": "ID", "id": "event_id"},
                            {"name": "유형", "id": "type"},
                            {"name": "Robot A", "id": "robot_a"},
                            {"name": "Robot B", "id": "robot_b"},
                            {"name": "시작 [s]", "id": "start_s"},
                            {"name": "종료 [s]", "id": "end_s"},
                            {"name": "지속 [s]", "id": "duration_s"},
                            {"name": "최소 거리 [mm]", "id": "minimum_distance_mm"},
                            {"name": "요구 거리 [mm]", "id": "required_distance_mm"},
                            {"name": "최소 여유 [mm]", "id": "minimum_safety_margin_mm"},
                            {"name": "최악 시각 [s]", "id": "minimum_distance_time_s"},
                            {
                                "name": "Capsule 표면 간격 [mm]",
                                "id": "minimum_capsule_surface_clearance_mm",
                            },
                            {"name": "Closest A [mm]", "id": "closest_a_xy_mm"},
                            {"name": "Closest B [mm]", "id": "closest_b_xy_mm"},
                        ],
                        data=_collision_display_rows(collision_rows[:20]),
                        **_table_options(20, (len(collision_rows) + 19) // 20),
                    ),
                ],
                className="panel",
            ),
        ]
    else:
        collision_event_sections = [
            html.Section(
                [
                    html.H3("충돌 이벤트"),
                    html.Div(
                        "기록된 Arm Envelope 또는 TCP Radius 충돌 이벤트가 없습니다.",
                        className="empty-state success-border",
                    ),
                ],
                className="panel panel-span compact-panel",
            )
        ]
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Strong(status),
                            html.Span(job.path.name),
                            html.Span(run.completed_label),
                        ],
                        className=f"result-status result-{status.lower()}",
                    ),
                    html.Code(str(run.directory)),
                ],
                className="result-banner",
            ),
            html.Section(cards, className="metric-grid result-metrics"),
            dcc.Tabs(
                value="overview",
                className="detail-tabs",
                children=[
                    dcc.Tab(
                        label="판정 요약",
                        value="overview",
                        children=html.Div(
                            [
                                html.Section(
                                    [
                                        html.H3("판정 결과와 확인사항"),
                                        _result_findings_content(payload, issue_rows),
                                    ],
                                    className="panel panel-span result-findings-panel",
                                ),
                            ],
                            className="overview-grid",
                        ),
                    ),
                    dcc.Tab(
                        label="로봇 작업",
                        value="robots",
                        children=html.Div(
                            [
                                html.Section(
                                    [
                                        html.H3("로봇별 상태 시간"),
                                        html.P(
                                            "Deposition(적층), Travel(비적층 이동), "
                                            "Wait(위치 유지 대기)의 누적 시간입니다.",
                                            className="muted-copy",
                                        ),
                                        dcc.Graph(
                                            figure=_robot_figure(robot_rows), config=_GRAPH_CONFIG
                                        ),
                                    ],
                                    className="panel panel-span",
                                ),
                                html.Section(
                                    [
                                        html.H3("로봇별 작업·Reach 상세"),
                                        html.P(
                                            "D/T/W 값은 상태별 시간, D/T 값은 적층/비적층 "
                                            "이동을 뜻합니다. Reach는 최대 사용거리와 설정 한계를 "
                                            "한 항목에서 비교합니다.",
                                            className="muted-copy",
                                        ),
                                        DataTable(
                                            columns=[
                                                {"name": "Robot", "id": "robot_id"},
                                                {"name": "완료 [s]", "id": "completion_s"},
                                                {"name": "D/T/W 시간 [s]", "id": "state_time_s"},
                                                {"name": "D/T 거리 [mm]", "id": "path_length_mm"},
                                                {"name": "D/T 평균속도", "id": "mean_speed_mm_s"},
                                                {"name": "최대/한계 Reach", "id": "reach_use"},
                                                {
                                                    "name": "Reach 여유 [mm]",
                                                    "id": "reach_margin_mm",
                                                },
                                                {"name": "Reach 판정", "id": "reach_result"},
                                            ],
                                            data=robot_result_rows,
                                            **_table_options(10),
                                        ),
                                    ],
                                    className="panel panel-span",
                                ),
                            ],
                            className="overview-grid",
                        ),
                    ),
                    dcc.Tab(
                        label="충돌 안전",
                        value="collision",
                        children=html.Div(
                            [
                                html.Section(
                                    [
                                        html.H3("안전거리 판정"),
                                        html.P(
                                            "측정 최소거리가 요구 최소거리보다 크면 안전 여유가 "
                                            "양수입니다. Arm은 Base–TCP Capsule 중심선, TCP는 "
                                            "두 끝점 사이의 XY 거리를 검사합니다.",
                                            className="muted-copy",
                                        ),
                                        html.Div(
                                            [
                                                _collision_evidence_card(
                                                    "Arm Envelope 안전거리", arm, arm=True
                                                ),
                                                _collision_evidence_card(
                                                    "TCP Radius 안전거리", tcp, arm=False
                                                ),
                                            ],
                                            className="safety-card-grid",
                                        ),
                                    ],
                                    className="panel panel-span safety-summary-panel",
                                ),
                                html.Section(
                                    [
                                        html.H3("Arm Envelope 최악 시점"),
                                        html.P(
                                            "채움 영역은 실제 Capsule, 점선은 공통 안전거리의 "
                                            "절반을 추가한 판정 외곽선입니다. 연결선은 두 중심선의 "
                                            "최단거리를 나타냅니다.",
                                            className="muted-copy",
                                        ),
                                        dcc.Graph(
                                            figure=_arm_envelope_snapshot(payload, job.path),
                                            config=_GRAPH_CONFIG,
                                        ),
                                    ],
                                    className="panel panel-span",
                                ),
                                *collision_event_sections,
                            ],
                            className="overview-grid",
                        ),
                    ),
                    dcc.Tab(
                        label="형상 비교",
                        value="shape",
                        children=html.Div(
                            [
                                html.Section(
                                    [
                                        html.H3("형상 판정과 핵심 지표"),
                                        html.P(
                                            "Coverage는 Target이 채워진 비율, IoU는 Target과 "
                                            "Deposition의 전체 겹침 정도입니다. Underfill과 "
                                            "Overfill은 낮을수록 좋습니다.",
                                            className="muted-copy",
                                        ),
                                        html.Div(
                                            _shape_summary_cards(shape, layer_rows),
                                            className="metric-grid shape-summary-grid",
                                        ),
                                        html.P(
                                            f"Layer 누계 체적 · Target "
                                            f"{target_volume:,.1f} mm³ / "
                                            f"Deposition {deposited_volume:,.1f} mm³",
                                            className="shape-volume-note",
                                        ),
                                        html.Details(
                                            [
                                                html.Summary("형상 판정 기준 보기"),
                                                html.Div(
                                                    _threshold_content(load_thresholds(job.path)),
                                                    className="threshold-details",
                                                ),
                                            ],
                                            className="shape-threshold-details",
                                        ),
                                    ],
                                    className="panel panel-span shape-summary-panel",
                                ),
                                html.Section(
                                    [
                                        html.H3("Layer별 일치율과 오차"),
                                        html.P(
                                            "위 그래프는 Coverage·IoU, 아래 그래프는 작은 "
                                            "Underfill·Overfill·IoU 손실을 확대합니다. "
                                            "Layer 판정은 IoU 기준을 사용합니다.",
                                            className="muted-copy",
                                        ),
                                        dcc.Graph(
                                            figure=_shape_figure(
                                                layer_rows, load_thresholds(job.path)
                                            ),
                                            config=_GRAPH_CONFIG,
                                        ),
                                    ],
                                    className="panel panel-span",
                                ),
                                html.Section(
                                    [
                                        html.H3("Layer별 수치"),
                                        DataTable(
                                            id="layer-table",
                                            columns=[
                                                {"name": "Layer", "id": "layer_index"},
                                                {"name": "Z [mm]", "id": "z_slice_mm"},
                                                {"name": "Coverage", "id": "coverage"},
                                                {"name": "Underfill", "id": "underfill_ratio"},
                                                {"name": "Overfill", "id": "overfill_ratio"},
                                                {"name": "IoU", "id": "iou"},
                                                {"name": "판정", "id": "passed"},
                                            ],
                                            data=_layer_display_rows(layer_rows[:25]),
                                            **_table_options(25, (len(layer_rows) + 24) // 25),
                                        ),
                                    ],
                                    className="panel panel-span",
                                ),
                            ],
                            className="overview-grid",
                        ),
                    ),
                    dcc.Tab(
                        label="파일·Replay",
                        value="artifacts",
                        children=html.Div(
                            [
                                html.Section(
                                    [
                                        html.H3("결과 파일"),
                                        html.P(
                                            "보고서, 원본 수치 CSV, 정적 그림과 적층 STL을 "
                                            "새 창에서 열거나 내려받을 수 있습니다.",
                                            className="muted-copy",
                                        ),
                                        html.Div(artifact_links, className="artifact-list"),
                                    ],
                                    className="panel compact-panel",
                                ),
                                html.Section(
                                    [
                                        html.H3("3D Replay"),
                                        html.P(
                                            "기본 검증에서는 Replay를 만들지 않습니다. 필요할 때 "
                                            "프레임 간격을 지정해 생성하세요.",
                                            className="muted-copy",
                                        ),
                                        html.Div(
                                            [
                                                html.Label(
                                                    "프레임 간격 [s]", htmlFor="replay-interval"
                                                ),
                                                dcc.Input(
                                                    id="replay-interval",
                                                    type="number",
                                                    min=1,
                                                    step=1,
                                                    value=replay_interval,
                                                ),
                                                html.Button(
                                                    "Replay 생성", id="replay-generate-button"
                                                ),
                                            ],
                                            className="replay-actions",
                                        ),
                                        html.Div(
                                            _replay_estimate_text(job, run, replay_interval),
                                            id="replay-estimate",
                                        ),
                                        html.Div(id="replay-status"),
                                        html.Progress(id="replay-progress", value="0", max="1"),
                                        replay,
                                    ],
                                    className="panel replay-panel",
                                ),
                            ],
                            className="stack-grid",
                        ),
                    ),
                ],
            ),
        ]
    )


def create_validator_app(
    initial_job_dir: Path,
    *,
    run_manager: ValidationRunManager | None = None,
    replay_run_manager: ReplayRunManager | None = None,
) -> Dash:
    initial = initial_job_dir.expanduser().resolve()
    registry = _ContextRegistry()
    manager = run_manager or ValidationRunManager()
    replay_manager = replay_run_manager or ReplayRunManager()
    app = Dash(
        __name__,
        assets_folder=str(Path(__file__).with_name("assets")),
        title="WAAM Validator",
        update_title=cast(str, None),
        suppress_callback_exceptions=True,
        meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
    )
    app.layout = _layout(initial)
    app.server.config["WAAM_INITIAL_JOB_DIR"] = str(initial)

    @app.server.get("/artifacts/<token>/<run_name>/<filename>")  # type: ignore[untyped-decorator]
    def serve_artifact(token: str, run_name: str, filename: str) -> Any:
        try:
            path = resolve_single_job_artifact(registry.get(token), run_name, filename)
        except DashboardDataError:
            abort(404)
        inline = path.suffix.lower() in {".html", ".png", ".md", ".log"}
        return send_file(path, as_attachment=not inline, download_name=path.name)

    @app.server.get("/artifacts/<path:_invalid>")  # type: ignore[untyped-decorator]
    def reject_malformed_artifact(_invalid: str) -> Any:
        abort(404)

    def context_run(run_data: JsonDict) -> tuple[Path, RunRecord]:
        token = str(run_data.get("context_id", ""))
        job_dir = registry.get(token)
        output_root = (job_dir / "output").resolve()
        requested = Path(str(run_data.get("output_dir", ""))).expanduser().resolve()
        if requested.parent != output_root:
            raise DashboardDataError("입력 context 밖의 결과입니다.")
        return job_dir, load_run_directory(requested)

    @app.callback(
        Output("job-dir-input", "value"),
        Output("folder-message", "children"),
        Input("browse-button", "n_clicks"),
        State("job-dir-input", "value"),
        prevent_initial_call=True,
    )
    def browse_folder(n_clicks: int | None, current: str | None) -> tuple[Any, str]:
        if not n_clicks:
            return no_update, ""
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "waam_validator.dashboard.folder_picker", current or ""],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return no_update, f"폴더 창을 열 수 없습니다. 경로를 직접 입력하세요: {exc}"
        selected = completed.stdout.strip()
        if not selected:
            return no_update, "폴더 선택을 취소했습니다."
        return selected, "폴더를 선택했습니다. 입력 확인을 실행하세요."

    @app.callback(
        Output("selected-job-dir-store", "data"),
        Input("job-dir-input", "value"),
    )
    def remember_job_directory(value: str | None) -> Any:
        # Some browsers restore the visible input before Dash has populated the
        # component's callback State. Preserve the CLI-provided initial path
        # until a real input value arrives.
        return no_update if value is None else value

    @app.callback(
        Output("active-input-store", "data"),
        Output("recent-run-store", "data"),
        Output("inspection-content", "children"),
        Output("input-gantt", "figure"),
        Output("input-scene", "figure"),
        Output("input-time-chart", "figure"),
        Output("input-motion-chart", "figure"),
        Output("input-reach-chart", "figure"),
        Output("preview-note", "children"),
        Input("inspect-button", "n_clicks"),
        State("job-dir-input", "value"),
        State("selected-job-dir-store", "data"),
        prevent_initial_call=True,
    )
    def inspect_input(
        n_clicks: int | None,
        value: str | None,
        remembered_value: str | None,
    ) -> tuple[Any, ...]:
        if not n_clicks:
            # Dynamic screen updates can re-register this callback with a null
            # click count. Never let that initialization erase a valid result.
            raise PreventUpdate
        if value is None:
            value = remembered_value
        if not value:
            empty = _empty_figure("입력 폴더를 지정하세요.")
            return (
                {},
                {},
                html.Div("입력 폴더를 지정하세요."),
                empty,
                empty,
                empty,
                empty,
                empty,
                "",
            )
        try:
            paths = resolve_input_directory(value)
            inspection, config, trajectories, mesh = inspect_dashboard_input_bundle(paths)
            if config is None or trajectories is None or mesh is None:
                empty = _empty_figure("차단 오류를 수정한 뒤 다시 확인하세요.")
                return (
                    inspection.to_dict(),
                    {},
                    _inspection_content(inspection.to_dict()),
                    empty,
                    empty,
                    empty,
                    empty,
                    empty,
                    "Preview를 생성할 수 없습니다.",
                )
            preview = build_input_preview(config, trajectories, mesh)
            token = registry.add(paths.job_dir)
            active = {
                **inspection.to_dict(),
                "context_id": token,
                "recent_result_note": "완료된 Capsule 결과가 없습니다.",
            }
            recent_data: JsonDict = {}
            preview_note = _preview_note(preview)
            recent = load_latest_run(paths.job_dir)
            if recent is not None:
                matches, match_note = verify_validation_inputs(paths.job_dir, recent.directory)
                if matches and recent.payload.get("schema_version") == "2.0":
                    recent_data = {
                        "context_id": token,
                        "job_dir": str(paths.job_dir),
                        "output_dir": str(recent.directory),
                        "run_name": recent.run_name,
                        "status": recent.status,
                    }
                    if match_note:
                        preview_note += f" · {match_note}"
                    active["recent_result_note"] = "현재 입력과 일치하는 최근 결과가 있습니다."
                elif recent.payload.get("schema_version") != "2.0":
                    active["recent_result_note"] = (
                        "최근 결과가 구형 schema라서 새 Capsule Validation이 필요합니다."
                    )
                    preview_note += (
                        " · 최근 결과는 현재 Capsule 결과 형식(schema 2.0)이 아니므로 "
                        "Validation을 다시 실행해야 합니다."
                    )
                elif not matches:
                    active["recent_result_note"] = match_note
            return (
                active,
                recent_data,
                _inspection_content(active),
                preview.gantt_figure,
                preview.scene,
                preview.time_figure,
                preview.motion_figure,
                preview.reach_figure,
                preview_note,
            )
        except (DashboardDataError, OSError, ValueError) as exc:
            empty = _empty_figure("입력 확인에 실패했습니다.")
            content = html.Div(
                [html.Strong("입력 확인 실패"), html.P(str(exc))],
                className="input-verdict is-blocked",
            )
            return {}, {}, content, empty, empty, empty, empty, empty, ""

    @app.callback(
        Output("validation-button", "disabled"),
        Output("validation-readiness", "children"),
        Output("recent-result-button", "disabled"),
        Output("recent-result-readiness", "children"),
        Input("active-input-store", "data"),
        Input("recent-run-store", "data"),
        Input("job-dir-input", "value"),
        Input("selected-job-dir-store", "data"),
        Input("runtime-store", "data"),
    )
    def input_action_state(
        active: JsonDict | None,
        recent: JsonDict | None,
        value: str | None,
        remembered_value: str | None,
        runtime: JsonDict | None,
    ) -> tuple[bool, str, bool, str]:
        if (runtime or {}).get("running"):
            return True, "Validation 실행 중", True, "실행이 끝나면 최근 결과가 갱신됩니다."
        if not active or not active.get("can_run"):
            return (
                True,
                "입력 확인을 완료해야 Validation을 실행할 수 있습니다.",
                True,
                "입력 확인 후 현재 입력과 일치하는 결과를 찾습니다.",
            )
        if value is None:
            value = remembered_value
        try:
            current_path = str(Path(value or "").expanduser().resolve())
        except OSError:
            current_path = ""
        inspected_path = str((active.get("paths") or {}).get("job_dir", ""))
        if current_path != inspected_path:
            return (
                True,
                "폴더가 변경되었습니다. 입력 확인을 다시 실행하세요.",
                True,
                "변경된 폴더의 입력 확인이 필요합니다.",
            )
        status = str(active.get("status", "READY"))
        recent_note = str(
            active.get("recent_result_note") or "현재 입력과 일치하는 최근 결과가 없습니다."
        )
        return False, f"Validation 준비 완료 · 입력 상태 {status}", not bool(recent), recent_note

    @app.callback(
        Output("view-store", "data"),
        Input("recent-result-button", "n_clicks"),
        Input("new-input-button", "n_clicks"),
        State("recent-run-store", "data"),
        prevent_initial_call=True,
    )
    def navigate(
        recent_clicks: int | None,
        new_clicks: int | None,
        recent: JsonDict | None,
    ) -> Any:
        if ctx.triggered_id == "recent-result-button" and recent_clicks and recent:
            return {"view": "result", "source": "recent", "nonce": time.time_ns()}
        if ctx.triggered_id == "new-input-button" and new_clicks:
            return {"view": "input", "nonce": time.time_ns()}
        return no_update

    @app.callback(
        Output("current-run-store", "data", allow_duplicate=True),
        Input("recent-run-store", "data"),
        Input("view-store", "data"),
        prevent_initial_call=True,
    )
    def select_recent_run(recent: JsonDict | None, view: JsonDict | None) -> Any:
        if (view or {}).get("source") == "recent" and recent:
            return recent
        return no_update

    @app.callback(
        Output("runtime-store", "data"),
        Output("validation-poller", "disabled"),
        Output("view-store", "data", allow_duplicate=True),
        Output("current-run-store", "data", allow_duplicate=True),
        Output("recent-run-store", "data", allow_duplicate=True),
        Output("overall-progress", "value"),
        Output("overall-progress-label", "children"),
        Output("stage-progress-label", "children"),
        Output("progress-stage", "children"),
        Output("progress-units", "children"),
        Output("progress-elapsed", "children"),
        Output("progress-eta", "children"),
        Output("progress-message", "children"),
        Output("progress-log", "children"),
        Output("progress-title", "children"),
        Output("progress-output", "children"),
        Output("validation-start-message", "children"),
        Input("validation-button", "n_clicks"),
        Input("validation-poller", "n_intervals"),
        State("active-input-store", "data"),
        prevent_initial_call=True,
    )
    def manage_validation(
        validation_clicks: int | None,
        _ticks: int,
        active: JsonDict | None,
    ) -> tuple[Any, ...]:
        triggered = ctx.triggered_id
        if triggered == "validation-button" and validation_clicks:
            try:
                if not active or not active.get("can_run"):
                    raise DashboardDataError("입력 확인을 다시 실행하세요.")
                token = str(active.get("context_id", ""))
                job_dir = registry.get(token)
                paths = resolve_input_directory(job_dir)
                if not inspection_is_current(active, paths):
                    raise DashboardDataError(
                        "입력 파일이 변경되었습니다. 입력 확인을 다시 실행하세요."
                    )
                if replay_manager.snapshot().get("running"):
                    raise ValidationAlreadyRunningError("Replay 생성이 끝난 뒤 실행하세요.")
                manager.start(JobRecord(job_dir.name, job_dir))
            except (
                DashboardDataError,
                ValidationAlreadyRunningError,
                OSError,
                RuntimeError,
            ) as exc:
                error = {"state": "ERROR", "running": False, "message": str(exc)}
                return (
                    error,
                    True,
                    {"view": "input"},
                    no_update,
                    no_update,
                    0,
                    "0%",
                    "0%",
                    "시작 실패",
                    "—",
                    "0.0 s",
                    "—",
                    str(exc),
                    "",
                    "Validation을 시작하지 못했습니다",
                    "",
                    f"Validation 시작 실패: {exc}",
                )
        snapshot = manager.snapshot()
        running = bool(snapshot.get("running"))
        overall = min(1.0, max(0.0, float(snapshot.get("overall_progress", 0.0) or 0.0)))
        stage_fraction = min(1.0, max(0.0, float(snapshot.get("stage_progress", 0.0) or 0.0)))
        elapsed = float(snapshot.get("elapsed_s", 0.0) or 0.0)
        eta = elapsed * (1.0 - overall) / overall if overall >= 0.02 and running else None
        completed = snapshot.get("completed_units")
        total = snapshot.get("total_units")
        unit = str(snapshot.get("unit") or "")
        units = (
            f"{float(completed):,.0f} / {float(total):,.0f} {unit}"
            if isinstance(completed, int | float) and isinstance(total, int | float)
            else "—"
        )
        run_data: Any = no_update
        recent_data: Any = no_update
        view = {"view": "running", "nonce": time.time_ns()}
        if not running and snapshot.get("state") == "FINISHED":
            output_dir = Path(str(snapshot.get("output_directory", ""))).resolve()
            token = str((active or {}).get("context_id", ""))
            run_data = {
                "context_id": token,
                "job_dir": str(registry.get(token)),
                "output_dir": str(output_dir),
                "run_name": output_dir.name,
                "status": str(snapshot.get("verdict", "ERROR")),
            }
            recent_data = run_data
            view = {"view": "result", "nonce": time.time_ns()}
        stage = str(snapshot.get("stage", "starting"))
        return (
            snapshot,
            not running,
            view,
            run_data,
            recent_data,
            overall * 100.0,
            f"{overall:.0%}",
            f"{stage_fraction:.0%}",
            _STAGE_LABELS.get(stage, stage),
            units,
            f"{elapsed:,.1f} s",
            f"약 {eta:,.0f} s" if eta is not None else "계산 중",
            str(snapshot.get("message", "")),
            str(snapshot.get("log", "")),
            f"{snapshot.get('job_name', '')} Validation",
            str(snapshot.get("output_directory", "")),
            (
                "Validation을 시작했습니다. 진행 화면으로 이동합니다."
                if triggered == "validation-button"
                else no_update
            ),
        )

    @app.callback(
        Output("input-screen", "className"),
        Output("progress-screen", "className"),
        Output("result-screen", "className"),
        Output("workflow-steps", "className"),
        Input("view-store", "data"),
    )
    def switch_screen(view_data: JsonDict | None) -> tuple[str, str, str, str]:
        view = str((view_data or {}).get("view", "input"))
        return (
            "validator-screen" + ("" if view == "input" else " is-hidden"),
            "validator-screen" + ("" if view == "running" else " is-hidden"),
            "validator-screen" + ("" if view == "result" else " is-hidden"),
            f"workflow-steps step-{view}",
        )

    @app.callback(
        Output("result-content", "children"),
        Input("current-run-store", "data"),
        Input("replay-refresh", "data"),
    )
    def render_result(run_data: JsonDict | None, _refresh: JsonDict | None) -> Any:
        if not run_data:
            return html.Div("표시할 결과가 없습니다.", className="empty-state")
        try:
            token = str(run_data.get("context_id", ""))
            job_dir, run = context_run(run_data)
            return _result_view(token, JobRecord(job_dir.name, job_dir), run)
        except (DashboardDataError, OSError, ValueError) as exc:
            return html.Div(
                [html.Strong("결과 로딩 실패"), html.P(str(exc))],
                className="input-verdict is-blocked",
            )

    def result_csv_page(
        run_data: JsonDict | None,
        filename: str,
        page_current: int | None,
        page_size: int | None,
        sort_by: list[dict[str, str]] | None,
    ) -> tuple[list[JsonDict], int]:
        if not run_data:
            return [], 1
        try:
            _, run = context_run(run_data)
            rows, page_count = read_csv_page(
                run.directory,
                filename,
                page=max(0, int(page_current or 0)),
                page_size=max(1, int(page_size or 20)),
                sort_by=sort_by,
            )
            if filename == "collision_events.csv":
                rows = _collision_display_rows(rows)
            elif filename == "layer_metrics.csv":
                rows = _layer_display_rows(rows)
            return rows, page_count
        except (DashboardDataError, OSError, ValueError):
            return [], 1

    @app.callback(
        Output("collision-table", "data"),
        Output("collision-table", "page_count"),
        Input("collision-table", "page_current"),
        Input("collision-table", "page_size"),
        Input("collision-table", "sort_by"),
        State("current-run-store", "data"),
        prevent_initial_call=True,
    )
    def collision_table_page(
        page_current: int | None,
        page_size: int | None,
        sort_by: list[dict[str, str]] | None,
        run_data: JsonDict | None,
    ) -> tuple[list[JsonDict], int]:
        return result_csv_page(run_data, "collision_events.csv", page_current, page_size, sort_by)

    @app.callback(
        Output("layer-table", "data"),
        Output("layer-table", "page_count"),
        Input("layer-table", "page_current"),
        Input("layer-table", "page_size"),
        Input("layer-table", "sort_by"),
        State("current-run-store", "data"),
        prevent_initial_call=True,
    )
    def layer_table_page(
        page_current: int | None,
        page_size: int | None,
        sort_by: list[dict[str, str]] | None,
        run_data: JsonDict | None,
    ) -> tuple[list[JsonDict], int]:
        return result_csv_page(run_data, "layer_metrics.csv", page_current, page_size, sort_by)

    @app.callback(
        Output("replay-estimate", "children"),
        Input("replay-interval", "value"),
        State("current-run-store", "data"),
        prevent_initial_call=True,
    )
    def replay_estimate(interval: float | None, run_data: JsonDict | None) -> str:
        if interval is None or not run_data:
            return "프레임 간격을 입력하세요."
        try:
            job_dir, run = context_run(run_data)
            return _replay_estimate_text(JobRecord(job_dir.name, job_dir), run, float(interval))
        except (DashboardDataError, OSError, ValueError) as exc:
            return str(exc)

    @app.callback(
        Output("replay-runtime-store", "data"),
        Output("replay-poller", "disabled"),
        Output("replay-status", "children"),
        Output("replay-progress", "value"),
        Output("replay-refresh", "data"),
        Input("replay-generate-button", "n_clicks"),
        Input("replay-poller", "n_intervals"),
        State("replay-interval", "value"),
        State("current-run-store", "data"),
        prevent_initial_call=True,
    )
    def manage_replay(
        clicks: int | None,
        _ticks: int,
        interval: float | None,
        run_data: JsonDict | None,
    ) -> tuple[Any, ...]:
        if ctx.triggered_id == "replay-generate-button" and clicks:
            try:
                if manager.snapshot().get("running"):
                    raise ReplayAlreadyRunningError("Validation이 끝난 뒤 Replay를 생성하세요.")
                if not run_data or interval is None:
                    raise DashboardDataError("결과와 프레임 간격을 확인하세요.")
                job_dir, run = context_run(run_data)
                estimate = estimate_replay(JobRecord(job_dir.name, job_dir), run, float(interval))
                if not estimate.allowed:
                    raise DashboardDataError(estimate.warning)
                replay_manager.start(JobRecord(job_dir.name, job_dir), run, float(interval))
            except (DashboardDataError, ReplayAlreadyRunningError, OSError, ValueError) as exc:
                return {"state": "ERROR", "running": False}, True, str(exc), 0, no_update
        snapshot = replay_manager.snapshot()
        running = bool(snapshot.get("running"))
        progress = min(1.0, max(0.0, float(snapshot.get("progress", 0.0) or 0.0)))
        return (
            snapshot,
            not running,
            str(snapshot.get("message", "")),
            progress,
            no_update if running else {"nonce": time.time_ns()},
        )

    return app


def _open_browser_when_ready(url: str) -> None:
    for _ in range(50):
        time.sleep(0.1)
        try:
            with urlopen(url, timeout=0.2):  # noqa: S310 - fixed local server URL.
                pass
        except OSError:
            continue
        webbrowser.open_new_tab(url)
        return


def run_validator_ui(
    job_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8050,
    open_browser: bool = True,
) -> None:
    resolved = job_dir.expanduser().resolve()
    if not resolved.is_dir():
        raise DashboardDataError(f"입력 폴더가 존재하지 않습니다: {resolved}")
    app = create_validator_app(resolved)
    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
    print(f"WAAM Validator is running on {url}")
    app.run(host=host, port=port, debug=False, use_reloader=False)

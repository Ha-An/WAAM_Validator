"""Single-job WAAM Validator 2.0 local web interface."""

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

from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html, no_update
from dash.exceptions import PreventUpdate
from flask import abort, send_file

from .app import (
    _collision_gauge,
    _collision_timeline,
    _empty_figure,
    _inspection_content,
    _issues_content,
    _metric_card,
    _robot_figure,
    _robot_table,
    _shape_figure,
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
                        [html.H1("WAAM Validator"), html.P("단일 WAAM 작업 검증")],
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
                                            html.Button("같은 입력 다시 실행", id="rerun-button"),
                                            html.Button(
                                                "다른 입력 선택",
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


def _gallery_content(token: str, run: RunRecord) -> list[html.Figure]:
    labels = {
        "overview_xy.png": "Robot TCP XY 경로",
        "gantt.png": "로봇 작업 일정 (Gantt)",
        "shape_metrics_by_layer.png": "Layer별 형상 일치도 및 오차",
        "worst_layer_comparison.png": "최악 Layer 비교",
    }
    return [
        html.Figure(
            [
                html.Img(src=_artifact_url(token, run, filename), alt=label),
                html.Figcaption(label),
            ]
        )
        for filename, label in labels.items()
        if (run.directory / filename).is_file()
    ]


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
        minimum = row.get("min_tcp_distance_mm")
        formatted.append(
            {
                "event_id": int(row.get("event_id", 0)),
                "type": str(row.get("type", "")),
                "robot_a": f"R{int(row.get('robot_a', 0))}",
                "robot_b": f"R{int(row.get('robot_b', 0))}",
                "start_s": f"{float(row.get('start_s', 0.0)):,.3f}",
                "end_s": f"{float(row.get('end_s', 0.0)):,.3f}",
                "duration_s": f"{float(row.get('duration_s', 0.0)):,.3f}",
                "min_tcp_distance_mm": ("—" if minimum is None else f"{float(minimum):,.2f}"),
            }
        )
    return formatted


def _result_check_card(label: str, passed: object, detail: str) -> html.Div:
    verdict = "PASS" if passed is True else "FAIL" if passed is False else "확인 불가"
    tone = "pass" if passed is True else "fail" if passed is False else "unknown"
    return html.Div(
        [html.Span(label), html.Strong(verdict), html.Small(detail)],
        className=f"result-check-card check-{tone}",
    )


def _shape_summary_cards(shape: JsonDict) -> list[html.Div]:
    return [
        _metric_card(
            "Target 기준 체적",
            f"{float(shape.get('target_volume_mm3', 0.0)):,.1f} mm³",
            "Target slice 누계",
        ),
        _metric_card(
            "Deposition 체적",
            f"{float(shape.get('deposited_volume_mm3', 0.0)):,.1f} mm³",
            "적층 layer 누계",
        ),
        _metric_card("Underfill", f"{float(shape.get('underfill_ratio', 0.0)):.2%}"),
        _metric_card("Overfill", f"{float(shape.get('overfill_ratio', 0.0)):.2%}"),
        _metric_card(
            "평가 Layer",
            f"{int(shape.get('evaluated_layer_count', 0)):,}개",
        ),
        _metric_card(
            "실패 Layer 비율",
            f"{float(shape.get('failed_layer_ratio', 0.0)):.2%}",
        ),
    ]


def _result_view(token: str, job: JobRecord, run: RunRecord) -> html.Div:
    payload = run.payload
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
    minimum_tcp = float(collision.get("minimum_tcp_distance_mm", 0.0))
    required_tcp = float(collision.get("minimum_required_distance_mm", 0.0))
    minimum_pair = collision.get("minimum_tcp_pair", [])
    pair_label = (
        f"R{minimum_pair[0]}–R{minimum_pair[1]}"
        if isinstance(minimum_pair, list) and len(minimum_pair) == 2
        else "Robot pair 확인 불가"
    )
    collision_count = int(collision.get("collision_event_count", 0))
    failed_layer_count = int(shape.get("failed_layer_count", 0))
    evaluated_layer_count = int(shape.get("evaluated_layer_count", 0))
    makespan_s = float(schedule.get("makespan_s", 0.0))
    cards = [
        _metric_card("최종 판정", status, run.completed_label),
        _metric_card(
            "Makespan",
            f"{makespan_s:,.1f} s",
            f"{makespan_s / 60.0:,.2f} min · {makespan_s / 3600.0:,.2f} h",
        ),
        _metric_card("Reach 판정", "PASS" if reach.get("passed") else "FAIL"),
        _metric_card("충돌 이벤트", f"{collision_count:,}건"),
        _metric_card(
            "최소 TCP 간 거리",
            f"{minimum_tcp:,.2f} mm",
            f"요구 {required_tcp:,.2f} mm · {pair_label}",
        ),
        _metric_card("전체 Coverage", f"{float(shape.get('coverage', 0.0)):.2%}"),
        _metric_card("전체 IoU", f"{float(shape.get('iou', 0.0)):.2%}"),
        _metric_card("실패 Layer 수", f"{failed_layer_count:,}개"),
    ]
    reach_rows = reach.get("robots", []) if isinstance(reach.get("robots"), list) else []
    reach_violation_count = sum(
        int(row.get("violation_point_count", 0)) for row in reach_rows if isinstance(row, dict)
    )
    reach_table_rows = _reach_display_rows([row for row in reach_rows if isinstance(row, dict)])
    artifact_links = []
    for filename in sorted(ALLOWED_ARTIFACTS):
        path = run.directory / filename
        if path.is_file() and filename not in {"dashboard_status.json", "replay_status.json"}:
            artifact_links.append(
                html.A(
                    [html.Strong(filename), html.Span(_format_size(path.stat().st_size))],
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
                        label="종합",
                        value="overview",
                        children=html.Div(
                            [
                                html.Section(
                                    [
                                        html.H3("검사 영역별 판정"),
                                        html.Div(
                                            [
                                                _result_check_card(
                                                    "로봇 Reach",
                                                    reach.get("passed"),
                                                    f"초과 절점 {reach_violation_count:,}개",
                                                ),
                                                _result_check_card(
                                                    "로봇 간 충돌",
                                                    collision.get("passed"),
                                                    f"이벤트 {collision_count:,}건",
                                                ),
                                                _result_check_card(
                                                    "적층 형상",
                                                    shape.get("passed"),
                                                    (
                                                        f"실패 Layer "
                                                        f"{failed_layer_count:,}/"
                                                        f"{evaluated_layer_count:,}"
                                                    ),
                                                ),
                                            ],
                                            className="result-check-grid",
                                        ),
                                    ],
                                    className="panel panel-span result-check-panel",
                                ),
                                html.Section(
                                    [
                                        html.H3("최종 판정 근거"),
                                        html.Ul(
                                            [
                                                html.Li(str(reason))
                                                for reason in payload.get("failure_reasons", [])
                                            ]
                                        )
                                        if payload.get("failure_reasons")
                                        else html.Div(
                                            "Validator가 기록한 실패 사유가 없습니다.",
                                            className="empty-state success-border",
                                        ),
                                    ],
                                    className="panel",
                                ),
                                html.Section(
                                    [
                                        html.H3("적용 임계값"),
                                        *_threshold_content(load_thresholds(job.path)),
                                    ],
                                    className="panel",
                                ),
                                html.Section(
                                    [html.H3("경고 및 오류"), _issues_content(issue_rows, payload)],
                                    className="panel panel-span",
                                ),
                            ],
                            className="overview-grid",
                        ),
                    ),
                    dcc.Tab(
                        label="로봇 작업 및 Reach",
                        value="robots",
                        children=html.Div(
                            [
                                html.Section(
                                    [
                                        html.H3("로봇별 D/T/W 누적 시간"),
                                        html.P(
                                            "원본 trajectory interval에서 계산한 절대 시간입니다.",
                                            className="muted-copy",
                                        ),
                                        dcc.Graph(
                                            figure=_robot_figure(robot_rows), config=_GRAPH_CONFIG
                                        ),
                                    ],
                                    className="panel",
                                ),
                                html.Section(
                                    [html.H3("로봇별 경로 및 속도"), _robot_table(robot_rows)],
                                    className="panel",
                                ),
                                html.Section(
                                    [
                                        html.H3("로봇별 Reach 판정"),
                                        html.P(
                                            "최대 Base–TCP 거리를 config의 reach_radius_mm와 "
                                            "비교합니다. 사용률이 100%를 넘거나 최소 여유가 "
                                            "음수이면 FAIL입니다.",
                                            className="muted-copy",
                                        ),
                                        DataTable(
                                            columns=[
                                                {"name": "Robot", "id": "robot_id"},
                                                {"name": "판정", "id": "passed"},
                                                {"name": "Reach [mm]", "id": "reach_radius_mm"},
                                                {
                                                    "name": "최대 Base–TCP [mm]",
                                                    "id": "maximum_reach_mm",
                                                },
                                                {
                                                    "name": "최소 여유 [mm]",
                                                    "id": "minimum_margin_mm",
                                                },
                                                {"name": "사용률", "id": "utilization_ratio"},
                                                {
                                                    "name": "초과 절점",
                                                    "id": "violation_point_count",
                                                },
                                            ],
                                            data=reach_table_rows,
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
                        label="충돌",
                        value="collision",
                        children=html.Div(
                            [
                                html.Section(
                                    [
                                        html.H3("TCP 간 최소 거리"),
                                        html.P(
                                            "전체 simulation sample 중 가장 가까운 Robot pair의 "
                                            "TCP 거리와 요구 거리를 비교합니다.",
                                            className="muted-copy",
                                        ),
                                        dcc.Graph(
                                            figure=_collision_gauge(payload), config=_GRAPH_CONFIG
                                        ),
                                    ],
                                    className="panel",
                                ),
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
                                        html.H3("충돌 이벤트"),
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
                                                {
                                                    "name": "최소 TCP [mm]",
                                                    "id": "min_tcp_distance_mm",
                                                },
                                            ],
                                            data=_collision_display_rows(collision_rows[:20]),
                                            **_table_options(20, (len(collision_rows) + 19) // 20),
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
                        children=html.Div(
                            [
                                html.Section(
                                    [
                                        html.H3("전체 형상 판정 요약"),
                                        html.Div(
                                            _shape_summary_cards(shape),
                                            className="metric-grid shape-summary-grid",
                                        ),
                                    ],
                                    className="panel panel-span shape-summary-panel",
                                ),
                                html.Section(
                                    [
                                        html.H3("Layer별 형상 일치도 및 오차"),
                                        html.P(
                                            "Layer별 PASS/FAIL은 Layer IoU 기준으로 판정합니다. "
                                            "전체 Coverage·Overfill·IoU 임계값은 종합 판정에만 "
                                            "적용됩니다.",
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
                                        html.H3("Layer별 상세 지표"),
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
                        label="산출물",
                        value="artifacts",
                        children=html.Div(
                            [
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
                                html.Section(
                                    [
                                        html.H3("검증 산출물"),
                                        html.Div(artifact_links, className="artifact-list"),
                                    ],
                                    className="panel",
                                ),
                                html.Div(
                                    _gallery_content(token, run),
                                    className="image-gallery panel-span",
                                ),
                            ],
                            className="artifact-grid",
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
            active = {**inspection.to_dict(), "context_id": token}
            recent_data: JsonDict = {}
            recent = load_latest_run(paths.job_dir)
            if recent is not None:
                matches, _ = verify_validation_inputs(paths.job_dir, recent.directory)
                if matches and recent.payload.get("schema_version") == "1.1":
                    recent_data = {
                        "context_id": token,
                        "job_dir": str(paths.job_dir),
                        "output_dir": str(recent.directory),
                        "run_name": recent.run_name,
                        "status": recent.status,
                    }
            return (
                active,
                recent_data,
                _inspection_content(active),
                preview.gantt_figure,
                preview.scene,
                preview.time_figure,
                preview.motion_figure,
                preview.reach_figure,
                _preview_note(preview),
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
    ) -> tuple[bool, str, bool]:
        if (runtime or {}).get("running"):
            return True, "Validation 실행 중", True
        if not active or not active.get("can_run"):
            return True, "입력 확인을 완료해야 Validation을 실행할 수 있습니다.", True
        if value is None:
            value = remembered_value
        try:
            current_path = str(Path(value or "").expanduser().resolve())
        except OSError:
            current_path = ""
        inspected_path = str((active.get("paths") or {}).get("job_dir", ""))
        if current_path != inspected_path:
            return True, "폴더가 변경되었습니다. 입력 확인을 다시 실행하세요.", True
        status = str(active.get("status", "READY"))
        return False, f"Validation 준비 완료 · 입력 상태 {status}", not bool(recent)

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
        Input("rerun-button", "n_clicks"),
        Input("validation-poller", "n_intervals"),
        State("active-input-store", "data"),
        prevent_initial_call=True,
    )
    def manage_validation(
        validation_clicks: int | None,
        rerun_clicks: int | None,
        _ticks: int,
        active: JsonDict | None,
    ) -> tuple[Any, ...]:
        triggered = ctx.triggered_id
        if triggered in {"validation-button", "rerun-button"}:
            clicked = validation_clicks if triggered == "validation-button" else rerun_clicks
            if clicked:
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
            view = {"view": "result", "nonce": time.time_ns()}
        stage = str(snapshot.get("stage", "starting"))
        return (
            snapshot,
            not running,
            view,
            run_data,
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
                if triggered in {"validation-button", "rerun-button"}
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

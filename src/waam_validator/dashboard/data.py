"""Read-only discovery and result loading for the local dashboard."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import polars as pl

from ..config.loader import load_config
from ..errors import WaamValidatorError

JsonDict = dict[str, Any]

REQUIRED_INPUT_FILES: Final = ("config.yaml", "trajectory.csv", "target.stl")
ALLOWED_ARTIFACTS: Final = frozenset(
    {
        "summary.json",
        "error.json",
        "robot_metrics.csv",
        "collision_events.csv",
        "layer_metrics.csv",
        "warnings.csv",
        "validation_report.md",
        "deposited.stl",
        "run.log",
        "overview_xy.png",
        "gantt.png",
        "shape_metrics_by_layer.png",
        "worst_layer_comparison.png",
        "replay.html",
        "replay_manifest.json",
        "dashboard_status.json",
        "replay_status.json",
        "validation_inputs.json",
    }
)
_STAMP_PATTERN: Final = re.compile(r"^(?P<stamp>\d{4}-\d{2}-\d{2}_\d{6})(?:_(?P<suffix>\d{3}))?$")
CSV_COLUMNS: Final = {
    "robot_metrics.csv": (
        "robot_id",
        "completion_s",
        "deposition_time_s",
        "travel_time_s",
        "wait_time_s",
        "deposition_length_mm",
        "travel_length_mm",
        "mean_deposition_speed_mm_s",
        "mean_travel_speed_mm_s",
        "reach_radius_mm",
        "maximum_reach_mm",
        "reach_margin_mm",
        "reach_utilization_ratio",
        "reach_violation_point_count",
    ),
    "collision_events.csv": (
        "event_id",
        "type",
        "robot_a",
        "robot_b",
        "start_s",
        "end_s",
        "duration_s",
        "min_tcp_distance_mm",
    ),
    "layer_metrics.csv": (
        "layer_index",
        "z_slice_mm",
        "coverage",
        "underfill_ratio",
        "overfill_ratio",
        "iou",
        "passed",
    ),
    "warnings.csv": ("severity", "code", "message", "robot_id", "start_s", "end_s"),
}


class DashboardDataError(ValueError):
    """Raised when a dashboard path or result artifact is unsafe or invalid."""


@dataclass(slots=True, frozen=True)
class JobRecord:
    """One validation-ready job directory."""

    name: str
    path: Path


@dataclass(slots=True, frozen=True)
class RunRecord:
    """The newest completed result for a job."""

    directory: Path
    status: str
    payload: JsonDict
    completed_label: str
    load_error: str | None = None

    @property
    def run_name(self) -> str:
        return self.directory.name


def is_job_directory(path: Path) -> bool:
    """Return whether a directory contains the fixed validator inputs."""
    if not path.is_dir():
        return False
    resolved = path.resolve()
    return all(
        (resolved / name).is_file() and (resolved / name).resolve().parent == resolved
        for name in REQUIRED_INPUT_FILES
    )


def discover_jobs(jobs_root: Path) -> list[JobRecord]:
    """Discover the root itself or validation-ready direct child directories."""
    root = jobs_root.expanduser().resolve()
    if not root.is_dir():
        raise DashboardDataError(f"작업 루트가 존재하지 않습니다: {root}")
    if is_job_directory(root):
        return [JobRecord(root.name, root)]
    jobs = []
    for child in root.iterdir():
        resolved_child = child.resolve()
        if resolved_child.parent == root and is_job_directory(resolved_child):
            jobs.append(JobRecord(child.name, resolved_child))
    return sorted(jobs, key=lambda item: item.name.casefold())


def _run_sort_key(path: Path) -> tuple[int, float, int, str]:
    match = _STAMP_PATTERN.fullmatch(path.name)
    if match is not None:
        try:
            parsed = datetime.strptime(match.group("stamp"), "%Y-%m-%d_%H%M%S")
        except ValueError:
            pass
        else:
            suffix = int(match.group("suffix") or 0)
            return (1, parsed.timestamp(), suffix, path.name)
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0.0
    return (0, modified, 0, path.name)


def completed_run_directories(job_dir: Path) -> list[Path]:
    """Return terminal output directories, excluding partial runs."""
    output_root = job_dir / "output"
    if not output_root.is_dir():
        return []
    completed = [
        child
        for child in output_root.iterdir()
        if child.is_dir()
        and ((child / "summary.json").is_file() or (child / "error.json").is_file())
    ]
    return sorted(completed, key=_run_sort_key, reverse=True)


def latest_completed_run(job_dir: Path) -> Path | None:
    """Return the newest terminal run for a job."""
    runs = completed_run_directories(job_dir)
    return runs[0] if runs else None


def _read_json(path: Path) -> JsonDict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DashboardDataError(f"{path.name}을 읽을 수 없습니다: {exc}") from exc
    if not isinstance(value, dict):
        raise DashboardDataError(f"{path.name}의 최상위 값은 JSON object여야 합니다.")
    return value


def _completed_label(run_dir: Path) -> str:
    match = _STAMP_PATTERN.fullmatch(run_dir.name)
    if match is not None:
        try:
            parsed = datetime.strptime(match.group("stamp"), "%Y-%m-%d_%H%M%S")
        except ValueError:
            pass
        else:
            suffix = match.group("suffix")
            base = parsed.strftime("%Y-%m-%d %H:%M:%S")
            return f"{base} · {suffix}" if suffix else base
    try:
        return datetime.fromtimestamp(run_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return run_dir.name


def load_latest_run(job_dir: Path) -> RunRecord | None:
    """Load the latest summary or fatal error without changing its verdict."""
    run_dir = latest_completed_run(job_dir)
    if run_dir is None:
        return None
    source = run_dir / ("summary.json" if (run_dir / "summary.json").is_file() else "error.json")
    try:
        payload = _read_json(source)
        status = str(payload.get("status", "ERROR")).upper()
        if status not in {"PASS", "FAIL", "ERROR"}:
            raise DashboardDataError(f"알 수 없는 status 값입니다: {status}")
        return RunRecord(run_dir, status, payload, _completed_label(run_dir))
    except DashboardDataError as exc:
        payload = {
            "schema_version": "1.1",
            "status": "ERROR",
            "code": "DASHBOARD_DATA_ERROR",
            "message": str(exc),
        }
        return RunRecord(run_dir, "ERROR", payload, _completed_label(run_dir), str(exc))


def load_run_directory(run_dir: Path) -> RunRecord:
    """Load one exact completed output directory without latest-run rediscovery."""
    resolved = run_dir.expanduser().resolve()
    source = resolved / ("summary.json" if (resolved / "summary.json").is_file() else "error.json")
    if not source.is_file():
        raise DashboardDataError(f"완료된 결과가 아닙니다: {resolved}")
    payload = _read_json(source)
    status = str(payload.get("status", "ERROR")).upper()
    if status not in {"PASS", "FAIL", "ERROR"}:
        raise DashboardDataError(f"알 수 없는 status 값입니다: {status}")
    return RunRecord(resolved, status, payload, _completed_label(resolved))


def load_thresholds(job_dir: Path) -> JsonDict:
    """Load display thresholds from the same validated configuration model."""
    try:
        config = load_config(job_dir / "config.yaml")
    except WaamValidatorError as exc:
        return {"error": f"{exc.code} - {exc.message}"}
    shape = config.shape_validation
    return {
        "minimum_overall_coverage": shape.minimum_overall_coverage,
        "maximum_overall_overfill_ratio": shape.maximum_overall_overfill_ratio,
        "minimum_overall_iou": shape.minimum_overall_iou,
        "minimum_layer_iou": shape.minimum_layer_iou,
        "maximum_failed_layer_ratio": shape.maximum_failed_layer_ratio,
    }


def read_csv_records(run_dir: Path, filename: str) -> list[JsonDict]:
    """Read a small result CSV through Polars; missing files are valid empty states."""
    if filename not in ALLOWED_ARTIFACTS or not filename.endswith(".csv"):
        raise DashboardDataError(f"허용되지 않은 CSV입니다: {filename}")
    path = run_dir / filename
    if not path.is_file():
        return []
    try:
        return pl.read_csv(
            path,
            columns=list(CSV_COLUMNS.get(filename, ())),
            infer_schema_length=1000,
        ).to_dicts()
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise DashboardDataError(f"{filename}을 읽을 수 없습니다: {exc}") from exc


def read_csv_page(
    run_dir: Path,
    filename: str,
    *,
    page: int,
    page_size: int,
    sort_by: list[dict[str, str]] | None = None,
    equals: dict[str, object] | None = None,
) -> tuple[list[JsonDict], int]:
    """Read, filter, sort and slice a result CSV on the server."""
    if filename not in ALLOWED_ARTIFACTS or not filename.endswith(".csv"):
        raise DashboardDataError(f"허용되지 않은 CSV입니다: {filename}")
    path = run_dir / filename
    if not path.is_file():
        return [], 1
    try:
        frame = pl.read_csv(
            path,
            columns=list(CSV_COLUMNS.get(filename, ())),
            infer_schema_length=1000,
        )
        for column, value in (equals or {}).items():
            if value not in (None, "", "ALL") and column in frame.columns:
                frame = frame.filter(pl.col(column) == value)
        for item in reversed(sort_by or []):
            column = item.get("column_id", "")
            if column in frame.columns:
                frame = frame.sort(column, descending=item.get("direction") == "desc")
        size = max(1, page_size)
        count = frame.height
        page_count = max(1, math.ceil(count / size))
        safe_page = min(max(0, page), page_count - 1)
        return frame.slice(safe_page * size, size).to_dicts(), page_count
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise DashboardDataError(f"{filename}을 읽을 수 없습니다: {exc}") from exc


def resolve_job(jobs_root: Path, job_name: str) -> JobRecord:
    """Resolve a browser-supplied job name through the discovered allowlist."""
    for job in discover_jobs(jobs_root):
        if job.name == job_name:
            return job
    raise DashboardDataError(f"알 수 없는 작업입니다: {job_name}")


def resolve_artifact(
    jobs_root: Path,
    job_name: str,
    run_name: str,
    filename: str,
) -> Path:
    """Resolve an allowlisted artifact and reject path traversal."""
    if filename not in ALLOWED_ARTIFACTS:
        raise DashboardDataError(f"허용되지 않은 산출물입니다: {filename}")
    job = resolve_job(jobs_root, job_name)
    output_root = (job.path / "output").resolve()
    candidate = (output_root / run_name / filename).resolve()
    if not candidate.is_relative_to(output_root) or candidate.parent.parent != output_root:
        raise DashboardDataError("허용된 결과 폴더 밖의 경로입니다.")
    if not candidate.is_file():
        raise DashboardDataError(f"산출물이 존재하지 않습니다: {filename}")
    return candidate


def resolve_single_job_artifact(job_dir: Path, run_name: str, filename: str) -> Path:
    """Resolve one allowlisted artifact below a canonical single-job output root."""
    if filename not in ALLOWED_ARTIFACTS:
        raise DashboardDataError(f"허용되지 않은 산출물입니다: {filename}")
    output_root = (job_dir.expanduser().resolve() / "output").resolve()
    candidate = (output_root / run_name / filename).resolve()
    if not candidate.is_relative_to(output_root) or candidate.parent.parent != output_root:
        raise DashboardDataError("허용된 결과 폴더 밖의 경로입니다.")
    if not candidate.is_file():
        raise DashboardDataError(f"산출물이 존재하지 않습니다: {filename}")
    return candidate

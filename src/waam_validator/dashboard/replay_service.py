"""Replay presets, conservative estimates, and input provenance helpers."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from .data import JobRecord, RunRecord

REPLAY_PRESET_FRAMES: Final = {"fast": 300, "standard": 600, "detail": 1_200}
MAX_REPLAY_FRAMES: Final = 2_000
VALIDATION_INPUT_MANIFEST: Final = "validation_inputs.json"
REPLAY_MANIFEST: Final = "replay_manifest.json"
REPLAY_STATUS: Final = "replay_status.json"


@dataclass(slots=True, frozen=True)
class ReplayEstimate:
    """A deliberately ranged estimate suitable for display before generation."""

    interval_s: float
    frame_count: int
    time_low_s: float
    time_high_s: float
    size_low_bytes: int
    size_high_bytes: int
    allowed: bool
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def recommended_interval_s(makespan_s: float, target_frames: int = 600) -> float:
    """Round upward to a human-friendly interval for the requested frame budget."""
    if not math.isfinite(makespan_s) or makespan_s <= 0:
        return 1.0
    raw = makespan_s / max(1, target_frames)
    if raw >= 60.0:
        return float(max(60, math.ceil(raw / 60.0) * 60))
    if raw >= 10.0:
        return float(math.ceil(raw / 10.0) * 10)
    return float(max(1, math.ceil(raw)))


def preset_interval_s(makespan_s: float, preset: str) -> float:
    """Return an interval for fast, standard, or detail replay quality."""
    return recommended_interval_s(makespan_s, REPLAY_PRESET_FRAMES.get(preset, 600))


def estimate_replay(job: JobRecord, run: RunRecord, interval_s: float) -> ReplayEstimate:
    """Estimate output cost from frame count and source file sizes."""
    schedule = run.payload.get("schedule")
    makespan_raw = schedule.get("makespan_s") if isinstance(schedule, dict) else None
    makespan = float(makespan_raw) if isinstance(makespan_raw, int | float) else 0.0
    collision = run.payload.get("collision")
    event_raw = collision.get("collision_event_count") if isinstance(collision, dict) else 0
    event_count = int(event_raw) if isinstance(event_raw, int | float) else 0
    if not math.isfinite(interval_s) or interval_s <= 0 or makespan <= 0:
        return ReplayEstimate(interval_s, 0, 0.0, 0.0, 0, 0, False, "간격을 확인하세요.")

    regular_frames = math.ceil(makespan / interval_s) + 1
    critical_frames = event_count * 2
    frame_count = regular_frames + critical_frames + math.ceil(regular_frames * 0.1)
    frame_count = min(MAX_REPLAY_FRAMES, frame_count)
    allowed = regular_frames <= MAX_REPLAY_FRAMES
    warning = ""
    if not allowed:
        minimum = recommended_interval_s(makespan, MAX_REPLAY_FRAMES)
        warning = (
            f"정규 프레임이 안전 한도 {MAX_REPLAY_FRAMES:,}개를 초과합니다. "
            f"간격을 최소 {minimum:g}초로 늘리세요."
        )
    elif frame_count > 1_200:
        warning = "상세 설정입니다. 생성 시간과 브라우저 메모리 사용량이 증가합니다."

    trajectory_mb = _size_mb(job.path / "trajectory.csv")
    target_mb = _size_mb(job.path / "target.stl")
    central_size_mb = 5.0 + trajectory_mb * 0.55 + target_mb * 1.5 + frame_count * 0.0006
    central_time_s = 1.5 + trajectory_mb * 0.12 + target_mb * 0.1 + frame_count * 0.0007
    prior = read_replay_manifest(run.directory)
    if prior is not None:
        prior_frames = _positive_number(prior.get("frame_count"))
        prior_seconds = _positive_number(prior.get("duration_s"))
        prior_bytes = _positive_number(prior.get("size_bytes"))
        if prior_frames and prior_seconds and prior_bytes:
            ratio = frame_count / prior_frames
            central_time_s = (prior_seconds + 1.5) * (0.7 + 0.3 * ratio)
            central_size_mb = prior_bytes / (1024 * 1024) * (0.8 + 0.2 * ratio)
    return ReplayEstimate(
        interval_s=interval_s,
        frame_count=frame_count,
        time_low_s=max(1.0, central_time_s * 0.65),
        time_high_s=max(2.0, central_time_s * 1.7),
        size_low_bytes=max(1, int(central_size_mb * 0.75 * 1024 * 1024)),
        size_high_bytes=max(1, int(central_size_mb * 1.35 * 1024 * 1024)),
        allowed=allowed,
        warning=warning,
    )


def input_signature(job_dir: Path) -> dict[str, dict[str, int]]:
    """Capture a cheap identity check for the three immutable validation inputs."""
    result: dict[str, dict[str, int]] = {}
    for filename in ("config.yaml", "trajectory.csv", "target.stl"):
        stat = (job_dir / filename).stat()
        result[filename] = {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return result


def write_validation_input_manifest(job_dir: Path, run_dir: Path) -> None:
    write_json_atomic(
        run_dir / VALIDATION_INPUT_MANIFEST,
        {"schema_version": "1.1", "inputs": input_signature(job_dir)},
    )


def verify_validation_inputs(job_dir: Path, run_dir: Path) -> tuple[bool, str]:
    """Reject replay generation when dashboard-validated inputs have since changed."""
    path = run_dir / VALIDATION_INPUT_MANIFEST
    if not path.is_file():
        return False, "기존 결과에는 입력 지문이 없어 현재 입력과 일치하는지 확인할 수 없습니다."
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        recorded = payload.get("inputs") if isinstance(payload, dict) else None
        current = input_signature(job_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"입력 지문을 확인할 수 없습니다: {exc}"
    if payload.get("schema_version") != "1.1" or recorded != current:
        return False, "검증 이후 입력 파일이 변경되었습니다. Validation을 다시 실행하세요."
    return True, ""


def read_replay_manifest(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / REPLAY_MANIFEST
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02 * (attempt + 1))


def _size_mb(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def _positive_number(value: object) -> float | None:
    if isinstance(value, int | float) and math.isfinite(float(value)) and float(value) > 0:
        return float(value)
    return None

from __future__ import annotations

import json
from pathlib import Path

from waam_validator.dashboard.data import JobRecord, RunRecord
from waam_validator.dashboard.replay_service import (
    estimate_replay,
    preset_interval_s,
    verify_validation_inputs,
    write_validation_input_manifest,
)


def _records(tmp_path: Path, makespan_s: float = 99_891.0) -> tuple[JobRecord, RunRecord]:
    job_path = tmp_path / "motor"
    job_path.mkdir()
    for filename, content in (
        ("config.yaml", "config"),
        ("trajectory.csv", "trajectory" * 100),
        ("target.stl", "mesh" * 100),
    ):
        (job_path / filename).write_text(content, encoding="utf-8")
    run_path = job_path / "output" / "2026-01-01_000000"
    run_path.mkdir(parents=True)
    payload = {
        "status": "PASS",
        "schedule": {"makespan_s": makespan_s},
        "collision": {"collision_event_count": 0},
    }
    (run_path / "summary.json").write_text(json.dumps(payload), encoding="utf-8")
    return JobRecord("motor", job_path), RunRecord(run_path, "PASS", payload, "now")


def test_standard_preset_targets_about_six_hundred_frames(tmp_path: Path) -> None:
    job, run = _records(tmp_path)
    interval = preset_interval_s(99_891.0, "standard")
    estimate = estimate_replay(job, run, interval)

    assert interval == 180.0
    assert 580 <= estimate.frame_count <= 650
    assert estimate.allowed is True
    assert estimate.size_high_bytes > estimate.size_low_bytes > 0


def test_one_second_interval_is_rejected_for_long_job(tmp_path: Path) -> None:
    job, run = _records(tmp_path)
    estimate = estimate_replay(job, run, 1.0)

    assert estimate.allowed is False
    assert "2,000" in estimate.warning


def test_validation_input_manifest_detects_later_change(tmp_path: Path) -> None:
    job, run = _records(tmp_path)
    write_validation_input_manifest(job.path, run.directory)
    assert verify_validation_inputs(job.path, run.directory)[0] is True

    (job.path / "trajectory.csv").write_text("changed", encoding="utf-8")
    unchanged, message = verify_validation_inputs(job.path, run.directory)
    assert unchanged is False
    assert "다시 실행" in message

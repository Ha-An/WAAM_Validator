from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from waam_validator.dashboard.data import (
    DashboardDataError,
    latest_completed_run,
    load_latest_matching_run,
    load_latest_run,
    read_csv_window,
    resolve_single_job_artifact,
)


def _make_job(root: Path, name: str = "part") -> Path:
    job = root / name
    job.mkdir()
    for filename in ("config.yaml", "trajectory.csv", "target.stl"):
        (job / filename).write_text("fixture", encoding="utf-8")
    return job


def _write_summary(run_dir: Path, status: str = "PASS") -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({"schema_version": "3.0", "status": status}),
        encoding="utf-8",
    )


def test_latest_completed_run_prefers_timestamp_and_suffix(tmp_path: Path) -> None:
    job = _make_job(tmp_path)
    _write_summary(job / "output" / "2026-01-01_100000")
    expected = job / "output" / "2026-01-01_100000_002"
    _write_summary(expected)
    partial = job / "output" / "2027-01-01_000000"
    partial.mkdir()

    assert latest_completed_run(job) == expected
    assert load_latest_run(job).completed_label.endswith("· 002")  # type: ignore[union-attr]


def test_nonstandard_run_falls_back_to_modified_time(tmp_path: Path) -> None:
    job = _make_job(tmp_path)
    older = job / "output" / "custom-old"
    newer = job / "output" / "custom-new"
    _write_summary(older)
    _write_summary(newer)
    os.utime(older, (10, 10))
    os.utime(newer, (20, 20))

    assert latest_completed_run(job) == newer


def test_corrupt_summary_becomes_dashboard_error(tmp_path: Path) -> None:
    job = _make_job(tmp_path)
    run_dir = job / "output" / "2026-01-01_100000"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text("not-json", encoding="utf-8")

    result = load_latest_run(job)
    assert result is not None
    assert result.status == "ERROR"
    assert result.payload["code"] == "UI_DATA_ERROR"


def test_latest_matching_run_skips_newer_incompatible_result(tmp_path: Path) -> None:
    job = _make_job(tmp_path)
    compatible = job / "output" / "2026-01-01_100000"
    _write_summary(compatible)
    signature = {
        filename: {
            "size_bytes": (job / filename).stat().st_size,
            "mtime_ns": (job / filename).stat().st_mtime_ns,
        }
        for filename in ("config.yaml", "trajectory.csv", "target.stl")
    }
    (compatible / "validation_inputs.json").write_text(
        json.dumps({"schema_version": "3.0", "inputs": signature}),
        encoding="utf-8",
    )
    incompatible = job / "output" / "2026-01-01_110000"
    _write_summary(incompatible)
    (incompatible / "validation_inputs.json").write_text(
        json.dumps({"schema_version": "2.0", "inputs": signature}),
        encoding="utf-8",
    )

    result = load_latest_matching_run(job)

    assert result is not None
    assert result.directory == compatible


def test_csv_window_sorts_and_pages(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "collision_events.csv").write_text(
        "event_id,type,robot_a,robot_b,start_s,end_s,duration_s,minimum_distance_mm,"
        "required_distance_mm,minimum_safety_margin_mm,"
        "minimum_capsule_surface_clearance_mm,minimum_distance_time_s,"
        "closest_a_x_mm,closest_a_y_mm,closest_b_x_mm,closest_b_y_mm\n"
        "1,ARM_ENVELOPE,1,2,5,6,1,100,250,-150,-100,5.5,0,0,100,0\n"
        "2,TCP_RADIUS,1,2,2,3,1,90,200,-110,,2.5,0,0,90,0\n"
        "3,ARM_ENVELOPE,1,3,8,9,1,80,250,-170,-120,8.5,0,0,80,0\n",
        encoding="utf-8",
    )

    window, count = read_csv_window(
        run_dir,
        "collision_events.csv",
        start=1,
        end=3,
        sort_by=[{"column_id": "start_s", "direction": "asc"}],
    )
    assert count == 3
    assert [row["event_id"] for row in window] == [1, 3]


def test_artifact_resolution_is_allowlisted_and_contained(tmp_path: Path) -> None:
    job = _make_job(tmp_path)
    run_dir = job / "output" / "2026-01-01_100000"
    _write_summary(run_dir)

    assert resolve_single_job_artifact(job, run_dir.name, "summary.json").is_file()
    with pytest.raises(DashboardDataError):
        resolve_single_job_artifact(job, run_dir.name, "../config.yaml")
    with pytest.raises(DashboardDataError):
        resolve_single_job_artifact(job, "../outside", "summary.json")


def test_allocate_timestamp_fixture_is_stable() -> None:
    assert datetime(2026, 1, 1, 12, 0, 0).strftime("%Y-%m-%d_%H%M%S") == ("2026-01-01_120000")

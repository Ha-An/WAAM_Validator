from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import trimesh

from waam_validator.dashboard.data import DashboardDataError
from waam_validator.dashboard.input_inspector import (
    inspect_dashboard_inputs,
    inspection_is_current,
    resolve_dashboard_inputs,
)


def _copy_job(destination: Path, fixture: str = "collision_free") -> Path:
    destination.mkdir()
    source = Path("tests/fixtures") / fixture
    for filename in ("config.yaml", "trajectory.csv", "target.stl"):
        shutil.copy2(source / filename, destination / filename)
    return destination


def test_resolve_inputs_defaults_to_standard_files_and_inspects_details(tmp_path: Path) -> None:
    job = _copy_job(tmp_path / "new_model")

    paths = resolve_dashboard_inputs(tmp_path, job)
    inspection = inspect_dashboard_inputs(paths)

    assert inspection.status in {"READY", "WARNING"}
    assert inspection.can_run is True
    assert inspection.trajectory["row_count"] == 8
    assert len(inspection.trajectory["robots"]) == 3
    assert inspection.target["face_count"] > 0
    assert inspection.target["watertight"] is True
    assert "schema_version" not in inspection.config
    assert inspection_is_current(inspection.to_dict(), paths) is True


def test_resolve_inputs_rejects_wrong_name_parent_and_outside_root(tmp_path: Path) -> None:
    job = _copy_job(tmp_path / "job")
    outside = _copy_job(tmp_path.parent / f"{tmp_path.name}-outside")
    try:
        with pytest.raises(DashboardDataError, match="파일명"):
            resolve_dashboard_inputs(
                tmp_path,
                job,
                job / "renamed.yaml",
                job / "trajectory.csv",
                job / "target.stl",
            )
        with pytest.raises(DashboardDataError, match="함께"):
            resolve_dashboard_inputs(
                tmp_path,
                job,
                job / "config.yaml",
                outside / "trajectory.csv",
                job / "target.stl",
            )
        with pytest.raises(DashboardDataError, match="JOBS_ROOT"):
            resolve_dashboard_inputs(tmp_path, outside)
    finally:
        shutil.rmtree(outside)


def test_resolve_inputs_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    outside = _copy_job(tmp_path.parent / f"{tmp_path.name}-outside")
    link = tmp_path / "linked"
    try:
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlinks are not available")
        with pytest.raises(DashboardDataError, match="JOBS_ROOT"):
            resolve_dashboard_inputs(tmp_path, link)
    finally:
        if link.is_symlink():
            link.unlink()
        shutil.rmtree(outside)


def test_inspection_distinguishes_expected_fail_and_blocking_error(tmp_path: Path) -> None:
    expected_fail = _copy_job(tmp_path / "expected_fail")
    trajectory_text = (expected_fail / "trajectory.csv").read_text(encoding="utf-8")
    (expected_fail / "trajectory.csv").write_text(
        trajectory_text.replace(
            "2,18.0,1000.0,-600.0,100.0,W",
            "2,18.0,1001.0,-600.0,100.0,W",
        ),
        encoding="utf-8",
    )
    paths = resolve_dashboard_inputs(tmp_path, expected_fail)
    inspection = inspect_dashboard_inputs(paths)
    assert inspection.status == "EXPECTED_FAIL"
    assert inspection.can_run is True
    assert inspection.expected_failures[0]["code"] == "WAIT_POSITION_CHANGED"

    blocked = _copy_job(tmp_path / "blocked")
    trajectory = blocked / "trajectory.csv"
    trajectory.write_text("robot_id,time_s,x_mm,y_mm,z_mm,mode\n", encoding="utf-8")
    blocked_inspection = inspect_dashboard_inputs(resolve_dashboard_inputs(tmp_path, blocked))
    assert blocked_inspection.status == "BLOCKED"
    assert blocked_inspection.can_run is False
    assert blocked_inspection.blocking_errors[0]["code"] == "MISSING_ROBOT"


def test_inspection_signature_changes_when_input_changes(tmp_path: Path) -> None:
    job = _copy_job(tmp_path / "job")
    paths = resolve_dashboard_inputs(tmp_path, job)
    inspection = inspect_dashboard_inputs(paths).to_dict()

    # Dash sends Store data through JavaScript, whose numeric precision cannot
    # preserve nanosecond timestamps. The signature must therefore keep them as
    # strings so a browser round trip does not make a fresh inspection stale.
    assert all(isinstance(item["mtime_ns"], str) for item in inspection["signature"].values())
    assert inspection_is_current(inspection, paths) is True

    stat = paths.trajectory.stat()
    os.utime(paths.trajectory, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert inspection_is_current(inspection, paths) is False


def test_inspection_blocks_non_watertight_and_coordinate_mismatch(tmp_path: Path) -> None:
    non_watertight = _copy_job(tmp_path / "non_watertight")
    open_mesh = trimesh.creation.box(extents=(10.0, 10.0, 2.0))
    open_mesh.apply_translation((0.0, 0.0, 1.0))
    open_mesh.update_faces(list(range(len(open_mesh.faces) - 1)))
    open_mesh.export(non_watertight / "target.stl")
    inspection = inspect_dashboard_inputs(resolve_dashboard_inputs(tmp_path, non_watertight))
    assert inspection.status == "BLOCKED"
    assert any(item["code"] == "TARGET_NOT_WATERTIGHT" for item in inspection.blocking_errors)

    mismatch = _copy_job(tmp_path / "mismatch")
    far_mesh = trimesh.creation.box(extents=(10.0, 10.0, 2.0))
    far_mesh.apply_translation((10_000.0, 10_000.0, 1.0))
    far_mesh.export(mismatch / "target.stl")
    inspection = inspect_dashboard_inputs(resolve_dashboard_inputs(tmp_path, mismatch))
    assert inspection.status == "BLOCKED"
    assert any(
        item["code"] == "TARGET_TRAJECTORY_FRAME_MISMATCH" for item in inspection.blocking_errors
    )

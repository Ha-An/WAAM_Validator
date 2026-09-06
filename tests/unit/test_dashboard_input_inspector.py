from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import trimesh

from waam_validator.dashboard.data import DashboardDataError
from waam_validator.dashboard.input_inspector import (
    inspect_dashboard_input_bundle,
    inspection_is_current,
    resolve_input_directory,
)


def _copy_job(destination: Path, fixture: str = "collision_free") -> Path:
    destination.mkdir()
    source = Path("tests/fixtures") / fixture
    for filename in ("config.yaml", "trajectory.csv", "target.stl"):
        shutil.copy2(source / filename, destination / filename)
    return destination


def test_resolve_inputs_defaults_to_standard_files_and_inspects_details(tmp_path: Path) -> None:
    job = _copy_job(tmp_path / "new_model")

    paths = resolve_input_directory(job)
    inspection, _, _, _ = inspect_dashboard_input_bundle(paths)

    assert inspection.status in {"READY", "WARNING"}
    assert inspection.can_run is True
    assert inspection.trajectory["row_count"] == 8
    assert len(inspection.trajectory["robots"]) == 3
    assert inspection.target["face_count"] > 0
    assert inspection.target["watertight"] is True
    assert "schema_version" not in inspection.config
    assert inspection_is_current(inspection.to_dict(), paths) is True


def test_resolve_inputs_requires_one_folder_and_three_standard_files(tmp_path: Path) -> None:
    job = _copy_job(tmp_path / "job")
    (job / "config.yaml").rename(job / "renamed.yaml")
    with pytest.raises(DashboardDataError, match="config.yaml"):
        resolve_input_directory(job)
    with pytest.raises(DashboardDataError, match="존재하지 않습니다"):
        resolve_input_directory(tmp_path / "missing")


def test_resolve_inputs_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    job = _copy_job(tmp_path / "job")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-config.yaml"
    outside.write_text("outside", encoding="utf-8")
    try:
        (job / "config.yaml").unlink()
        try:
            (job / "config.yaml").symlink_to(outside)
        except OSError:
            pytest.skip("file symlinks are not available")
        with pytest.raises(DashboardDataError, match="밖"):
            resolve_input_directory(job)
    finally:
        if (job / "config.yaml").is_symlink():
            (job / "config.yaml").unlink()
        outside.unlink(missing_ok=True)


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
    paths = resolve_input_directory(expected_fail)
    inspection, _, _, _ = inspect_dashboard_input_bundle(paths)
    assert inspection.status == "EXPECTED_FAIL"
    assert inspection.can_run is True
    assert inspection.expected_failures[0]["code"] == "WAIT_POSITION_CHANGED"

    blocked = _copy_job(tmp_path / "blocked")
    trajectory = blocked / "trajectory.csv"
    trajectory.write_text("robot_id,time_s,x_mm,y_mm,z_mm,mode\n", encoding="utf-8")
    blocked_inspection, _, _, _ = inspect_dashboard_input_bundle(resolve_input_directory(blocked))
    assert blocked_inspection.status == "BLOCKED"
    assert blocked_inspection.can_run is False
    assert blocked_inspection.blocking_errors[0]["code"] == "MISSING_ROBOT"


def test_inspection_signature_changes_when_input_changes(tmp_path: Path) -> None:
    job = _copy_job(tmp_path / "job")
    paths = resolve_input_directory(job)
    inspection = inspect_dashboard_input_bundle(paths)[0].to_dict()

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
    inspection = inspect_dashboard_input_bundle(resolve_input_directory(non_watertight))[0]
    assert inspection.status == "BLOCKED"
    assert any(item["code"] == "TARGET_NOT_WATERTIGHT" for item in inspection.blocking_errors)

    mismatch = _copy_job(tmp_path / "mismatch")
    far_mesh = trimesh.creation.box(extents=(10.0, 10.0, 2.0))
    far_mesh.apply_translation((10_000.0, 10_000.0, 1.0))
    far_mesh.export(mismatch / "target.stl")
    inspection = inspect_dashboard_input_bundle(resolve_input_directory(mismatch))[0]
    assert inspection.status == "BLOCKED"
    assert any(
        item["code"] == "TARGET_TRAJECTORY_FRAME_MISMATCH" for item in inspection.blocking_errors
    )

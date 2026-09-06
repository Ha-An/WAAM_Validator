from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import yaml

from waam_validator.config.loader import load_config
from waam_validator.errors import InputValidationError
from waam_validator.models import RobotTrajectory, TrajectorySet
from waam_validator.pipeline import run_validation
from waam_validator.trajectory.reach import compute_reach_metrics


def _one_point_trajectories(
    config_path: Path, offsets: tuple[float, float, float]
) -> tuple[object, TrajectorySet]:
    config = load_config(config_path)
    robots = []
    for robot, offset in zip(config.robots, offsets, strict=True):
        base = np.asarray(robot.base_xyz_mm, dtype=np.float32)
        robots.append(
            RobotTrajectory(
                robot_id=robot.id,
                time_s=np.asarray([0.0], dtype=np.float64),
                xyz_mm=np.asarray([base + np.asarray([offset, 0.0, 0.0])], dtype=np.float32),
                mode=np.asarray([2], dtype=np.uint8),
            )
        )
    return config, TrajectorySet(tuple(robots), 3)  # type: ignore[arg-type]


def test_config_requires_workspace_positive_reach_and_no_version_field(
    fixture_root: Path, tmp_path: Path
) -> None:
    source = fixture_root / "collision_free" / "config.yaml"
    original = yaml.safe_load(source.read_text(encoding="utf-8"))
    cases = []
    versioned_config = dict(original)
    versioned_config["schema_version"] = "1.1"
    cases.append(versioned_config)
    no_workspace = dict(original)
    no_workspace.pop("workspace")
    cases.append(no_workspace)
    negative_reach = yaml.safe_load(source.read_text(encoding="utf-8"))
    negative_reach["robots"][0]["reach_radius_mm"] = -1.0
    cases.append(negative_reach)
    missing_arm_radius = yaml.safe_load(source.read_text(encoding="utf-8"))
    missing_arm_radius["robots"][0].pop("arm_envelope_radius_mm")
    cases.append(missing_arm_radius)
    zero_arm_radius = yaml.safe_load(source.read_text(encoding="utf-8"))
    zero_arm_radius["robots"][0]["arm_envelope_radius_mm"] = 0.0
    cases.append(zero_arm_radius)
    negative_clearance = yaml.safe_load(source.read_text(encoding="utf-8"))
    negative_clearance["collision"]["arm_clearance_mm"] = -1.0
    cases.append(negative_clearance)
    legacy_collision = yaml.safe_load(source.read_text(encoding="utf-8"))
    legacy_collision["collision"]["check_arm_crossing"] = legacy_collision["collision"].pop(
        "check_arm_envelope"
    )
    cases.append(legacy_collision)

    for index, data in enumerate(cases):
        path = tmp_path / f"invalid-{index}.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        with pytest.raises(InputValidationError) as caught:
            load_config(path)
        assert caught.value.code == "INVALID_CONFIG_SCHEMA"
    assert "schema_version is not a Config field" in str(
        pytest.raises(InputValidationError, load_config, tmp_path / "invalid-0.yaml").value
    )


def test_reach_boundary_and_geometry_epsilon(fixture_root: Path) -> None:
    path = fixture_root / "collision_free" / "config.yaml"
    config, trajectories = _one_point_trajectories(path, (2500.0, 2500.0, 2500.0))
    metrics = compute_reach_metrics(trajectories, config)  # type: ignore[arg-type]
    assert metrics.passed
    assert all(item.violation_point_count == 0 for item in metrics.robots)

    _, trajectories = _one_point_trajectories(path, (2500.0, 2500.002, 2499.0))
    metrics = compute_reach_metrics(trajectories, config)  # type: ignore[arg-type]
    assert metrics.passed is False
    assert metrics.robots[1].violation_point_count == 1
    assert metrics.robots[1].minimum_margin_mm == pytest.approx(-0.001953125)


def test_reach_violation_is_normal_fail_and_is_written(fixture_root: Path, tmp_path: Path) -> None:
    job = tmp_path / "reach-fail"
    shutil.copytree(fixture_root / "collision_free", job)
    config_data = yaml.safe_load((job / "config.yaml").read_text(encoding="utf-8"))
    config_data["robots"][0]["reach_radius_mm"] = 100.0
    (job / "config.yaml").write_text(yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8")

    result = run_validation(job, tmp_path / "result")

    assert result.status == "FAIL"
    assert result.reach.passed is False
    assert any(reason.startswith("ROBOT_REACH: R1") for reason in result.failure_reasons)
    summary = json.loads((result.output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == "3.0"
    assert summary["validator_version"] == "1.0.0"
    assert summary["reach"]["passed"] is False
    robot_csv = (result.output_dir / "robot_metrics.csv").read_text(encoding="utf-8")
    assert "reach_violation_point_count" in robot_csv
    report = (result.output_dir / "validation_report.md").read_text(encoding="utf-8")
    assert "Robot Reach" in report
    assert "Validation Violations" in report
    assert "fatal status `ERROR`" in report

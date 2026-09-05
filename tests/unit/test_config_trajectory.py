from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from waam_validator.config.loader import load_config
from waam_validator.errors import InputValidationError
from waam_validator.trajectory.interpolation import interpolate_robot_state
from waam_validator.trajectory.loader import load_trajectory_csv
from waam_validator.trajectory.sampling import iter_simulation_samples
from waam_validator.trajectory.validator import validate_trajectory_set


def test_config_and_trajectory_compact_types(fixture_root: Path) -> None:
    job = fixture_root / "collision_free"
    config = load_config(job / "config.yaml")
    trajectories = load_trajectory_csv(job / "trajectory.csv", config)
    messages = validate_trajectory_set(trajectories, config)
    assert not messages.errors
    assert trajectories.row_count == 8
    assert trajectories.robots[0].time_s.dtype == np.float64
    assert trajectories.robots[0].xyz_mm.dtype == np.float32
    assert trajectories.robots[0].mode.dtype == np.uint8


def test_circular_workspace_is_loaded_and_must_lie_inside_robot_triangle(
    fixture_root: Path, tmp_path: Path
) -> None:
    source = fixture_root / "collision_free" / "config.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    data["workspace"] = {
        "shape": "circle_xy",
        "center_xy_mm": [0.0, 0.0],
        "radius_mm": 250.0,
    }
    valid_path = tmp_path / "workspace-valid.yaml"
    valid_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    config = load_config(valid_path)
    assert config.workspace is not None
    assert config.workspace.center_xy_mm == (0.0, 0.0)
    assert config.workspace.radius_mm == 250.0

    data["workspace"]["radius_mm"] = 3000.0
    invalid_path = tmp_path / "workspace-invalid.yaml"
    invalid_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(InputValidationError, match="inside the robot-base XY triangle"):
        load_config(invalid_path)


def test_deposition_bead_must_stay_inside_circular_workspace(
    fixture_root: Path, tmp_path: Path
) -> None:
    job = fixture_root / "collision_free"
    data = yaml.safe_load((job / "config.yaml").read_text(encoding="utf-8"))
    data["workspace"] = {
        "shape": "circle_xy",
        "center_xy_mm": [0.0, 0.0],
        "radius_mm": 250.0,
    }
    config_path = tmp_path / "workspace.yaml"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    csv_text = (job / "trajectory.csv").read_text(encoding="utf-8")
    csv_text = csv_text.replace("-40.0,0.0,2.0,D", "-249.0,0.0,2.0,D")
    trajectory_path = tmp_path / "trajectory.csv"
    trajectory_path.write_text(csv_text, encoding="utf-8")
    trajectories = load_trajectory_csv(trajectory_path, config)

    with pytest.raises(InputValidationError) as caught:
        validate_trajectory_set(trajectories, config)
    assert caught.value.code == "DEPOSITION_OUTSIDE_WORKSPACE"


def test_invalid_extra_csv_column_fails(fixture_root: Path, tmp_path: Path) -> None:
    config = load_config(fixture_root / "collision_free" / "config.yaml")
    path = tmp_path / "trajectory.csv"
    path.write_text(
        "robot_id,time_s,x_mm,y_mm,z_mm,mode,segment_id\n",
        encoding="utf-8",
    )
    with pytest.raises(InputValidationError, match="exactly") as caught:
        load_trajectory_csv(path, config)
    assert caught.value.code == "INVALID_CSV_HEADER"


@pytest.mark.parametrize(
    "case",
    ["two_robots", "duplicate_id", "invalid_id", "negative_radius", "zero_bead", "schema"],
)
def test_invalid_config_cases(fixture_root: Path, tmp_path: Path, case: str) -> None:
    source = fixture_root / "collision_free" / "config.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if case == "two_robots":
        data["robots"] = data["robots"][:2]
    elif case == "duplicate_id":
        data["robots"][1]["id"] = 1
    elif case == "invalid_id":
        data["robots"][2]["id"] = 4
    elif case == "negative_radius":
        data["robots"][0]["tcp_radius_mm"] = -1.0
    elif case == "zero_bead":
        data["process"]["bead_width_mm"] = 0.0
    else:
        data["schema_version"] = "2.0"
    path = tmp_path / f"{case}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(InputValidationError) as caught:
        load_config(path)
    assert caught.value.code == "INVALID_CONFIG_SCHEMA"


@pytest.mark.parametrize(
    "case",
    ["robot_text", "long_mode", "lower_mode", "duplicate_time", "first_not_zero", "last_not_w"],
)
def test_invalid_trajectory_cases(fixture_root: Path, tmp_path: Path, case: str) -> None:
    job = fixture_root / "collision_free"
    config = load_config(job / "config.yaml")
    lines = (job / "trajectory.csv").read_text(encoding="utf-8").splitlines()
    rows = [line.split(",") for line in lines]
    if case == "robot_text":
        rows[1][0] = "R1"
    elif case == "long_mode":
        rows[1][5] = "TRAVEL"
    elif case == "lower_mode":
        rows[1][5] = "t"
    elif case == "duplicate_time":
        rows[2][1] = rows[1][1]
    elif case == "first_not_zero":
        rows[1][1] = "0.1"
    else:
        rows[4][5] = "T"
    path = tmp_path / f"{case}.csv"
    path.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(InputValidationError):
        load_trajectory_csv(path, config)


def test_interpolation_uses_left_row_mode(fixture_root: Path) -> None:
    job = fixture_root / "collision_free"
    config = load_config(job / "config.yaml")
    robot = load_trajectory_csv(job / "trajectory.csv", config).robots[0]
    xyz, mode = interpolate_robot_state(robot, 9.0)
    assert xyz == pytest.approx([0.0, 0.0, 2.0])
    assert mode == 1
    final_xyz, final_mode = interpolate_robot_state(robot, 100.0)
    assert final_xyz == pytest.approx(robot.xyz_mm[-1])
    assert final_mode == 2


def test_adaptive_samples_obey_time_and_distance_limits(fixture_root: Path) -> None:
    job = fixture_root / "collision_free"
    config = load_config(job / "config.yaml")
    trajectories = load_trajectory_csv(job / "trajectory.csv", config)
    samples = list(iter_simulation_samples(trajectories, config))
    times = np.asarray([item.time_s for item in samples])
    positions = np.asarray([item.xyz_by_robot for item in samples], dtype=np.float64)
    assert np.diff(times).max() <= config.simulation.max_time_step_s + 1e-12
    displacements = np.linalg.norm(np.diff(positions, axis=0), axis=2)
    assert displacements.max() <= config.simulation.max_tcp_step_mm + 1e-4
    assert len(times) == len(np.unique(times))

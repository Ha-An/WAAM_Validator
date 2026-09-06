from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import trimesh
import yaml
from typer.testing import CliRunner

from waam_validator.cli import app
from waam_validator.errors import InputValidationError
from waam_validator.pipeline import run_validation
from waam_validator.progress import ValidationProgress


def test_full_headless_pipeline_pass(fixture_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "result"
    result = run_validation(fixture_root / "collision_free", output, headless=True)
    assert result.status == "PASS"
    for filename in (
        "summary.json",
        "collision_events.csv",
        "robot_metrics.csv",
        "layer_metrics.csv",
        "validation_report.md",
        "warnings.csv",
        "run.log",
        "deposited.stl",
    ):
        assert (output / filename).is_file()
    assert not (output / "overview_xy.png").exists()
    deposited = trimesh.load(output / "deposited.stl")
    assert not deposited.is_empty


def test_pipeline_reports_monotonic_measured_progress(fixture_root: Path, tmp_path: Path) -> None:
    events: list[ValidationProgress] = []
    result = run_validation(
        fixture_root / "collision_free",
        tmp_path / "progress",
        headless=True,
        progress_callback=events.append,
    )

    assert result.status == "PASS"
    assert events[-1].overall_progress == 1.0
    assert [event.overall_progress for event in events] == sorted(
        event.overall_progress for event in events
    )
    stages = {event.stage for event in events}
    assert {
        "loading_inputs",
        "collision",
        "deposition",
        "target_slicing",
        "shape_metrics",
        "artifacts",
        "completed",
    } <= stages
    assert any(event.unit == "simulation_s" for event in events)
    assert any(event.unit == "intervals" for event in events)
    assert any(event.unit == "layers" for event in events)


def test_shape_fail_and_failure_reasons(fixture_root: Path, tmp_path: Path) -> None:
    result = run_validation(fixture_root / "shape_underfill", tmp_path / "underfill", headless=True)
    assert result.status == "FAIL"
    assert any("coverage" in reason.lower() for reason in result.failure_reasons)


@pytest.mark.parametrize(
    ("fixture_name", "status", "arm_events", "tcp_events"),
    [
        ("arm_cross", "FAIL", 1, 0),
        ("tcp_radius", "FAIL", 0, 1),
        ("time_separated_crossing", "PASS", 0, 0),
        ("shape_overfill", "FAIL", 0, 0),
    ],
)
def test_known_answer_jobs(
    fixture_root: Path,
    tmp_path: Path,
    fixture_name: str,
    status: str,
    arm_events: int,
    tcp_events: int,
) -> None:
    result = run_validation(
        fixture_root / fixture_name,
        tmp_path / fixture_name,
        headless=True,
    )
    assert result.status == status
    assert result.collision.arm_cross_event_count == arm_events
    assert result.collision.tcp_radius_event_count == tcp_events


def test_cli_json_stdout_is_one_object(fixture_root: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            str(fixture_root / "collision_free"),
            "--output",
            str(tmp_path / "json-result"),
            "--headless",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "PASS"
    assert "WAAM PATH" not in result.stdout


def test_cli_check_and_input_error(fixture_root: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    valid = runner.invoke(app, ["check", str(fixture_root / "collision_free")])
    assert valid.exit_code == 0
    assert "WAAM INPUT CHECK : PASS" in valid.stdout
    invalid = runner.invoke(app, ["check", str(tmp_path / "missing")])
    assert invalid.exit_code == 2
    assert "MISSING_CONFIG" in invalid.stdout


def test_cli_check_accepts_runnable_reach_fail_input(fixture_root: Path, tmp_path: Path) -> None:
    job = tmp_path / "reach-fail"
    shutil.copytree(fixture_root / "collision_free", job)
    config = yaml.safe_load((job / "config.yaml").read_text(encoding="utf-8"))
    config["robots"][0]["reach_radius_mm"] = 100.0
    (job / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(app, ["check", str(job)])

    assert result.exit_code == 0
    assert "WAAM INPUT CHECK : PASS" in result.stdout


def test_cli_validation_fail_returns_one(fixture_root: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            str(fixture_root / "shape_overfill"),
            "--output",
            str(tmp_path / "failed-result"),
            "--headless",
        ],
    )
    assert result.exit_code == 1
    assert "Overall Result : FAIL" in result.stdout
    assert "[Failure Reasons]" in result.stdout


def test_visual_outputs_and_replay(fixture_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "visual"
    result = run_validation(Path("examples/sample_job"), output, generate_replay=True)
    assert result.status == "PASS"
    for filename in (
        "overview_xy.png",
        "gantt.png",
        "shape_metrics_by_layer.png",
        "worst_layer_comparison.png",
        "replay.html",
    ):
        assert (output / filename).stat().st_size > 0


def test_repeated_runs_have_identical_numerical_results(fixture_root: Path, tmp_path: Path) -> None:
    first = run_validation(
        fixture_root / "collision_free", tmp_path / "repeat-1", headless=True
    ).summary_dict()
    second = run_validation(
        fixture_root / "collision_free", tmp_path / "repeat-2", headless=True
    ).summary_dict()
    for key in ("schedule", "collision", "shape", "failure_reasons", "warnings", "errors"):
        assert first[key] == second[key]


def test_fatal_input_writes_error_json(fixture_root: Path, tmp_path: Path) -> None:
    job = tmp_path / "missing-target"
    job.mkdir()
    for filename in ("config.yaml", "trajectory.csv"):
        (job / filename).write_bytes((fixture_root / "collision_free" / filename).read_bytes())
    with pytest.raises(InputValidationError) as caught:
        run_validation(job, tmp_path / "fatal-output", headless=True)
    assert caught.value.code == "MISSING_TARGET"
    error_path = tmp_path / "fatal-output" / "error.json"
    payload = json.loads(error_path.read_text(encoding="utf-8"))
    assert payload["status"] == "ERROR"
    assert payload["code"] == "MISSING_TARGET"
    assert payload["validator_version"] == "1.0"

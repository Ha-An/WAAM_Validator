from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from waam_validator import pipeline
from waam_validator.cli import app
from waam_validator.collision.simulator import run_collision_analysis
from waam_validator.config.loader import load_config
from waam_validator.errors import InputValidationError
from waam_validator.pipeline import run_validation
from waam_validator.progress import ValidationProgress
from waam_validator.reporting.writers import CORE_RESULT_FILES
from waam_validator.trajectory.loader import load_trajectory_csv


def test_full_pipeline_writes_only_core_result_bundle(fixture_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "result"
    result = run_validation(fixture_root / "collision_free", output)
    assert result.status == "PASS"
    assert {path.name for path in output.iterdir()} == set(CORE_RESULT_FILES)


def test_report_does_not_expose_hidden_worker_state(fixture_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "worker-result"

    def worker_progress(_event: ValidationProgress) -> None:
        state_dir = output / ".waam_state"
        state_dir.mkdir(exist_ok=True)
        (state_dir / "validation-status.json").write_text("{}", encoding="utf-8")

    run_validation(
        fixture_root / "collision_free",
        output,
        progress_callback=worker_progress,
    )

    report = (output / "validation_report.md").read_text(encoding="utf-8")
    assert ".waam_state" not in report
    assert all(f"`{filename}`" in report for filename in CORE_RESULT_FILES)


def test_result_csvs_reconcile_with_summary(fixture_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "reconciled"
    result = run_validation(fixture_root / "collision_free", output)
    summary = result.summary_dict()
    with (output / "robot_metrics.csv").open(encoding="utf-8", newline="") as handle:
        robots = list(csv.DictReader(handle))
    for robot in robots:
        completion = float(robot["completion_s"])
        state_total = sum(
            float(robot[field]) for field in ("deposition_time_s", "travel_time_s", "wait_time_s")
        )
        assert state_total == pytest.approx(completion)
        assert completion + float(robot["inactive_after_completion_s"]) == pytest.approx(
            summary["schedule"]["makespan_s"]
        )
        for mode in ("deposition", "travel"):
            duration = float(robot[f"{mode}_time_s"])
            length = float(robot[f"{mode}_length_mm"])
            speed = robot[f"mean_{mode}_speed_mm_s"]
            if duration > 0:
                assert float(speed) == pytest.approx(length / duration)
            else:
                assert speed == ""

    with (output / "layer_metrics.csv").open(encoding="utf-8", newline="") as handle:
        layers = list(csv.DictReader(handle))
    height = load_config(fixture_root / "collision_free" / "config.yaml").process.layer_height_mm
    target_volume = sum(float(row["target_area_mm2"]) * height for row in layers)
    deposited_volume = sum(float(row["deposited_area_mm2"]) * height for row in layers)
    intersection_volume = sum(float(row["intersection_area_mm2"]) * height for row in layers)
    underfill_volume = sum(float(row["underfill_area_mm2"]) * height for row in layers)
    overfill_volume = sum(float(row["overfill_area_mm2"]) * height for row in layers)
    shape = summary["shape"]
    assert shape["target_volume_mm3"] == pytest.approx(target_volume)
    assert shape["deposited_volume_mm3"] == pytest.approx(deposited_volume)
    assert shape["intersection_volume_mm3"] == pytest.approx(intersection_volume)
    assert shape["underfill_volume_mm3"] == pytest.approx(underfill_volume)
    assert shape["overfill_volume_mm3"] == pytest.approx(overfill_volume)
    assert shape["coverage"] == pytest.approx(intersection_volume / target_volume)
    assert shape["iou"] == pytest.approx(
        intersection_volume / (target_volume + deposited_volume - intersection_volume)
    )
    assert shape["evaluated_layer_count"] == len(layers)
    assert shape["failed_layer_count"] == sum(row["passed"] == "false" for row in layers)


def test_pipeline_reports_monotonic_measured_progress(fixture_root: Path, tmp_path: Path) -> None:
    events: list[ValidationProgress] = []
    result = run_validation(
        fixture_root / "collision_free",
        tmp_path / "progress",
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
        "results",
        "completed",
    } <= stages
    assert any(event.unit == "simulation_s" for event in events)
    assert any(event.unit == "intervals" for event in events)
    assert any(event.unit == "layers" for event in events)


def test_pipeline_rejects_inputs_changed_during_run(
    fixture_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_signature = pipeline.input_signature
    calls = 0

    def changing_signature(job_dir: Path) -> dict[str, dict[str, int]]:
        nonlocal calls
        calls += 1
        signature = real_signature(job_dir)
        if calls > 1:
            signature["trajectory.csv"]["mtime_ns"] += 1
        return signature

    monkeypatch.setattr(pipeline, "input_signature", changing_signature)
    output = tmp_path / "changed-input"
    with pytest.raises(InputValidationError) as caught:
        run_validation(fixture_root / "collision_free", output)
    assert caught.value.code == "INPUT_CHANGED_DURING_VALIDATION"
    payload = json.loads((output / "error.json").read_text(encoding="utf-8"))
    assert payload["code"] == "INPUT_CHANGED_DURING_VALIDATION"


def test_shape_fail_and_failure_reasons(fixture_root: Path, tmp_path: Path) -> None:
    result = run_validation(fixture_root / "shape_underfill", tmp_path / "underfill")
    assert result.status == "FAIL"
    assert any("coverage" in reason.lower() for reason in result.failure_reasons)


def test_capsule_result_schema_csv_report_and_log(fixture_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "capsule-result"
    result = run_validation(fixture_root / "arm_envelope_near_miss", output)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    arm = summary["collision"]["arm_envelope"]

    assert summary["schema_version"] == "3.0"
    assert "warnings" not in summary
    assert "errors" not in summary
    assert all(item["severity"] in {"warning", "violation"} for item in summary["issues"])
    assert arm["enabled"] is True
    assert arm["passed"] is False
    assert arm["minimum_safety_margin_mm"] < 0.0
    assert len(arm["closest_points_xy_mm"]) == 2
    assert any("ARM_ENVELOPE" in reason for reason in result.failure_reasons)
    csv_header = (output / "collision_events.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "minimum_safety_margin_mm" in csv_header
    assert "closest_a_x_mm" in csv_header
    assert "ARM_ENVELOPE" in (output / "validation_report.md").read_text(encoding="utf-8")
    assert "Arm Envelope worst case" in (output / "run.log").read_text(encoding="utf-8")
    with (output / "collision_events.csv").open(encoding="utf-8", newline="") as handle:
        events = list(csv.DictReader(handle))
    arm_events = [event for event in events if event["type"] == "ARM_ENVELOPE"]
    tcp_events = [event for event in events if event["type"] == "TCP_RADIUS"]
    assert len(arm_events) == summary["collision"]["arm_envelope"]["event_count"]
    assert len(tcp_events) == summary["collision"]["tcp_radius"]["event_count"]
    assert min(float(event["minimum_safety_margin_mm"]) for event in arm_events) == pytest.approx(
        arm["minimum_safety_margin_mm"]
    )
    issue_keys = [
        (
            item["severity"],
            item["code"],
            item["robot_id"],
            item["start_s"],
            item["end_s"],
        )
        for item in summary["issues"]
    ]
    assert len(issue_keys) == len(set(issue_keys))


def test_disabled_arm_check_keeps_metric_without_event_or_fail(
    fixture_root: Path, tmp_path: Path
) -> None:
    result = run_validation(fixture_root / "tcp_radius", tmp_path / "tcp-only")
    arm = result.summary_dict()["collision"]["arm_envelope"]

    assert arm["enabled"] is False
    assert arm["passed"] is True
    assert arm["event_count"] == 0
    assert arm["minimum_safety_margin_mm"] < 0.0
    assert not any("ARM_ENVELOPE:" in reason for reason in result.failure_reasons)
    assert any(issue.code == "ARM_ENVELOPE_CHECK_DISABLED" for issue in result.warnings)


@pytest.mark.parametrize(
    ("fixture_name", "status", "arm_events", "tcp_events"),
    [
        ("arm_cross", "FAIL", 1, 0),
        ("arm_envelope_near_miss", "FAIL", 1, 0),
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
    result = run_validation(fixture_root / fixture_name, tmp_path / fixture_name)
    assert result.status == status
    assert result.collision.arm_envelope_event_count == arm_events
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


def test_repeated_runs_have_identical_numerical_results(fixture_root: Path, tmp_path: Path) -> None:
    first = run_validation(fixture_root / "collision_free", tmp_path / "repeat-1").summary_dict()
    second = run_validation(fixture_root / "collision_free", tmp_path / "repeat-2").summary_dict()
    for key in ("schedule", "collision", "shape", "failure_reasons", "issues"):
        assert first[key] == second[key]


@pytest.mark.parametrize("job_number", range(1, 11))
def test_benchmark_arm_envelopes_pass(job_number: int) -> None:
    job = Path("tests") / f"{job_number:02d}"
    config = load_config(job / "config.yaml")
    trajectories = load_trajectory_csv(job / "trajectory.csv", config)
    collision = run_collision_analysis(trajectories, config)

    assert collision.arm_envelope_event_count == 0
    assert collision.minimum_arm_safety_margin_mm > 0.0
    assert collision.arm_required_distance_at_worst_mm == pytest.approx(250.0)


def test_fatal_input_writes_error_json(fixture_root: Path, tmp_path: Path) -> None:
    job = tmp_path / "missing-target"
    job.mkdir()
    for filename in ("config.yaml", "trajectory.csv"):
        (job / filename).write_bytes((fixture_root / "collision_free" / filename).read_bytes())
    with pytest.raises(InputValidationError) as caught:
        run_validation(job, tmp_path / "fatal-output")
    assert caught.value.code == "MISSING_TARGET"
    error_path = tmp_path / "fatal-output" / "error.json"
    payload = json.loads(error_path.read_text(encoding="utf-8"))
    assert payload["status"] == "ERROR"
    assert payload["code"] == "MISSING_TARGET"
    assert payload["validator_version"] == "1.0.0"

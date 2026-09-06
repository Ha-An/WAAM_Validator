from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from waam_validator.dashboard.data import JobRecord, RunRecord
from waam_validator.dashboard.deposited_stl_worker import run_deposited_stl_worker
from waam_validator.dashboard.replay_service import (
    DEPOSITED_STL_MANIFEST,
    REPLAY_MANIFEST,
    VALIDATION_STATUS,
)
from waam_validator.dashboard.replay_worker import run_replay_worker
from waam_validator.dashboard.runner import (
    ReplayAlreadyRunningError,
    ReplayRunManager,
    ValidationAlreadyRunningError,
    ValidationRunManager,
    allocate_output_path,
    tail_log,
)
from waam_validator.dashboard.worker import run_worker


class FakeProcess:
    def __init__(self) -> None:
        self.return_code: int | None = None

    def poll(self) -> int | None:
        return self.return_code


def test_allocate_output_path_uses_non_destructive_suffix(tmp_path: Path) -> None:
    job = tmp_path / "job"
    existing = job / "output" / "2026-01-01_120000"
    existing.mkdir(parents=True)

    assert allocate_output_path(job, datetime(2026, 1, 1, 12, 0, 0)).name == (
        "2026-01-01_120000_001"
    )


def test_manager_allows_only_one_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeProcess()

    def fake_popen(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return fake

    monkeypatch.setattr("waam_validator.dashboard.runner.subprocess.Popen", fake_popen)
    job_path = tmp_path / "job"
    job_path.mkdir()
    manager = ValidationRunManager("python")
    output = manager.start(JobRecord("job", job_path))

    with pytest.raises(ValidationAlreadyRunningError):
        manager.start(JobRecord("job", job_path))
    snapshot = manager.snapshot()
    assert snapshot["running"] is True
    assert snapshot["job_name"] == "job"

    output.mkdir(parents=True)
    status_path = output / VALIDATION_STATUS
    status_path.parent.mkdir(parents=True)
    status_path.write_text(
        json.dumps({"state": "FINISHED", "stage": "completed", "verdict": "PASS"}),
        encoding="utf-8",
    )
    fake.return_code = 0
    snapshot = manager.snapshot()
    assert snapshot["running"] is False
    assert snapshot["verdict"] == "PASS"


def test_tail_log_is_bounded(tmp_path: Path) -> None:
    (tmp_path / "run.log").write_text("\n".join(str(i) for i in range(20)), encoding="utf-8")
    assert tail_log(tmp_path, 3) == "17\n18\n19"


def test_manager_maps_unreported_process_exit_to_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeProcess()
    fake.return_code = 4

    def fake_popen(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return fake

    monkeypatch.setattr("waam_validator.dashboard.runner.subprocess.Popen", fake_popen)
    job_path = tmp_path / "job"
    job_path.mkdir()
    manager = ValidationRunManager("python")
    manager.start(JobRecord("job", job_path))

    snapshot = manager.snapshot()
    assert snapshot["state"] == "FINISHED"
    assert snapshot["stage"] == "process_exit"
    assert snapshot["verdict"] == "ERROR"


def test_worker_writes_terminal_state_for_real_fixture(tmp_path: Path) -> None:
    output = tmp_path / "dashboard-worker"
    exit_code = run_worker(Path("examples/sample_job"), output)

    status = json.loads((output / VALIDATION_STATUS).read_text(encoding="utf-8"))
    assert exit_code == 0
    assert status["state"] == "FINISHED"
    assert status["verdict"] == "PASS"
    assert (output / "summary.json").is_file()
    assert not (output / "replay.html").exists()
    assert (output / "validation_inputs.json").is_file()


def test_worker_maps_input_failure_to_error(tmp_path: Path) -> None:
    output = tmp_path / "failed-worker"
    exit_code = run_worker(tmp_path / "missing-job", output)

    status = json.loads((output / VALIDATION_STATUS).read_text(encoding="utf-8"))
    assert exit_code == 2
    assert status["verdict"] == "ERROR"
    assert status["code"] == "MISSING_CONFIG"


def test_replay_worker_generates_manifest_after_validation(tmp_path: Path) -> None:
    output = tmp_path / "dashboard-worker"
    job = Path("examples/sample_job")
    assert run_worker(job, output) == 0

    assert run_replay_worker(job, output, 10.0) == 0
    manifest = json.loads((output / REPLAY_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "3.0"
    assert manifest["status"] == "READY"
    assert manifest["interval_s"] == 10.0
    assert manifest["frame_count"] > 1
    assert manifest["size_bytes"] == (output / "replay.html").stat().st_size


def test_deposited_stl_worker_generates_atomic_optional_artifact(tmp_path: Path) -> None:
    output = tmp_path / "stl-worker"
    job = Path("examples/sample_job")
    assert run_worker(job, output) == 0

    assert run_deposited_stl_worker(job, output) == 0
    manifest = json.loads((output / DEPOSITED_STL_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "3.0"
    assert manifest["status"] == "READY"
    assert manifest["size_bytes"] == (output / "deposited.stl").stat().st_size
    assert not (output / ".deposited.stl.tmp").exists()


def test_replay_manager_starts_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeProcess()
    command: list[str] = []

    def fake_popen(args: list[str], **_kwargs: Any) -> FakeProcess:
        command.extend(args)
        return fake

    monkeypatch.setattr("waam_validator.dashboard.runner.subprocess.Popen", fake_popen)
    job_path = tmp_path / "job"
    run_path = job_path / "output" / "2026-01-01_000000"
    run_path.mkdir(parents=True)
    job = JobRecord("job", job_path)
    run = RunRecord(run_path, "PASS", {}, "now")
    manager = ReplayRunManager("python")

    manager.start(job, run, 180.0)
    assert "waam_validator.dashboard.replay_worker" in command
    assert manager.snapshot()["running"] is True


def test_derived_artifacts_share_one_worker_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = FakeProcess()

    def first_popen(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return first

    monkeypatch.setattr("waam_validator.dashboard.runner.subprocess.Popen", first_popen)
    job_path = tmp_path / "job"
    run_path = job_path / "output" / "2026-01-01_000000"
    run_path.mkdir(parents=True)
    job = JobRecord("job", job_path)
    run = RunRecord(run_path, "PASS", {}, "now")
    manager = ReplayRunManager("python")

    manager.start(job, run, 180.0)
    with pytest.raises(ReplayAlreadyRunningError):
        manager.start_deposited_stl(job, run)

    first.return_code = 0
    second = FakeProcess()
    monkeypatch.setattr(
        "waam_validator.dashboard.runner.subprocess.Popen",
        lambda *_args, **_kwargs: second,
    )
    manager.start_deposited_stl(job, run)
    with pytest.raises(ReplayAlreadyRunningError):
        manager.start(job, run, 180.0)

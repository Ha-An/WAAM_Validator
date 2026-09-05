from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from waam_validator.cli import app as cli_app
from waam_validator.dashboard.app import create_dashboard_app
from waam_validator.dashboard.data import JobRecord
from waam_validator.dashboard.runner import ValidationRunManager


class StubValidationManager:
    def __init__(self) -> None:
        self.start_count = 0
        self.state: dict[str, Any] = {
            "state": "IDLE",
            "stage": "idle",
            "message": "실행 대기",
            "running": False,
            "elapsed_s": 0.0,
            "log": "",
        }

    def start(self, job: JobRecord) -> Path:
        self.start_count += 1
        self.state = {
            "state": "RUNNING",
            "stage": "loading_inputs",
            "message": "입력 로딩",
            "running": True,
            "elapsed_s": 0.0,
            "log": "loading",
            "job_name": job.name,
        }
        return job.path / "output" / "stub"

    def snapshot(self) -> dict[str, Any]:
        return dict(self.state)

    def finish(self) -> None:
        self.state.update(
            state="FINISHED",
            stage="completed",
            message="완료",
            running=False,
            verdict="PASS",
        )


class StubReplayManager:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"state": "IDLE", "running": False}

    def start(self, job: JobRecord, _run: Any, _interval_s: float) -> None:
        self.state = {
            "state": "RUNNING",
            "running": True,
            "job_name": job.name,
            "message": "생성 중",
            "elapsed_s": 0.0,
            "progress": 0.2,
        }

    def cancel(self) -> bool:
        if not self.state.get("running"):
            return False
        self.finish("CANCELLED")
        return True

    def snapshot(self) -> dict[str, Any]:
        return dict(self.state)

    def finish(self, verdict: str = "READY") -> None:
        self.state.update(
            state="FINISHED",
            running=False,
            verdict=verdict,
            message="완료",
            elapsed_s=1.0,
            progress=1.0,
        )


def _post_callback(
    dash_app: Any,
    client: Any,
    key_fragment: str,
    *,
    inputs: list[dict[str, Any]],
    state: list[dict[str, Any]],
    changed: str,
) -> Any:
    output_key = next(key for key in dash_app.callback_map if key_fragment in key)
    output_spec = dash_app.callback_map[output_key]["output"]
    outputs = (
        [{"id": item.component_id, "property": item.component_property} for item in output_spec]
        if isinstance(output_spec, list)
        else {
            "id": output_spec.component_id,
            "property": output_spec.component_property,
        }
    )
    return client.post(
        "/_dash-update-component",
        json={
            "output": output_key,
            "outputs": outputs,
            "changedPropIds": [changed],
            "inputs": inputs,
            "state": state,
        },
    )


def test_dashboard_http_shell_and_artifact_route(tmp_path: Path) -> None:
    job = tmp_path / "motor"
    job.mkdir()
    for filename in ("config.yaml", "trajectory.csv", "target.stl"):
        (job / filename).write_text("fixture", encoding="utf-8")
    run = job / "output" / "2026-08-26_000505"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(
        json.dumps({"schema_version": "1.0", "status": "PASS"}),
        encoding="utf-8",
    )
    dash_app = create_dashboard_app(tmp_path)
    client = dash_app.server.test_client()

    response = client.get("/")
    assert response.status_code == 200
    layout = client.get("/_dash-layout")
    assert layout.status_code == 200
    assert b"motor" in layout.data
    assert b"input-job-dir" in layout.data
    assert b'"id":"validation-poller"' in layout.data
    assert b'"id":"replay-poller"' in layout.data
    assert layout.data.count(b'"disabled":true') >= 2
    assert dash_app.config.update_title is None
    css = client.get("/assets/dashboard.css")
    assert css.status_code == 200

    callback = next(
        value["callback"].__wrapped__
        for key, value in dash_app.callback_map.items()
        if "result-title.children" in key
    )
    rendered = callback("motor", {}, {})
    assert rendered[2] == "PASS"
    assert len(rendered[4]) == 6

    output_key = next(key for key in dash_app.callback_map if "result-title.children" in key)
    output_spec = dash_app.callback_map[output_key]["output"]
    update = client.post(
        "/_dash-update-component",
        json={
            "output": output_key,
            "outputs": [
                {"id": item.component_id, "property": item.component_property}
                for item in output_spec
            ],
            "changedPropIds": ["job-select.value"],
            "inputs": [
                {"id": "job-select", "property": "value", "value": "motor"},
                {"id": "result-refresh", "property": "data", "value": {}},
                {"id": "replay-refresh", "property": "data", "value": {}},
            ],
            "state": [],
        },
    )
    assert update.status_code == 200
    assert b"PASS" in update.data

    summary = client.get("/artifacts/motor/2026-08-26_000505/summary.json")
    assert summary.status_code == 200
    assert client.get("/artifacts/motor/not-a-run/summary.json").status_code == 404


def test_dashboard_input_preparation_is_one_shot_and_selects_job(tmp_path: Path) -> None:
    job = tmp_path / "new_model"
    job.mkdir()
    for filename in ("config.yaml", "trajectory.csv", "target.stl"):
        shutil.copy2(Path("tests/fixtures/collision_free") / filename, job / filename)
    dash_app = create_dashboard_app(tmp_path)

    fill = next(
        value["callback"].__wrapped__
        for key, value in dash_app.callback_map.items()
        if "input-config-path.value" in key
    )
    config_path, trajectory_path, target_path = fill(str(job))
    inspect = next(
        value["callback"].__wrapped__
        for key, value in dash_app.callback_map.items()
        if "input-inspection-store.data" in key
    )
    inspection, _content, request = inspect(
        1,
        str(job),
        config_path,
        trajectory_path,
        target_path,
    )
    assert inspection["can_run"] is True

    readiness_state = next(
        value["callback"].__wrapped__
        for key, value in dash_app.callback_map.items()
        if "input-readiness-message.children" in key
    )
    readiness, tone = readiness_state(
        inspection,
        str(job),
        config_path,
        trajectory_path,
        target_path,
        {"path": str(tmp_path)},
    )
    assert "자동 선택되었습니다" in readiness
    assert tone == "is-ready"

    refresh = next(
        value["callback"].__wrapped__
        for key, value in dash_app.callback_map.items()
        if "job-select.options" in key
    )
    options, selected = refresh({"path": str(tmp_path)}, request, None)
    assert selected == "new_model"
    assert {item["value"] for item in options} == {"new_model"}


def test_dashboard_jobs_root_can_be_changed_from_screen(tmp_path: Path) -> None:
    initial_root = tmp_path / "initial"
    next_root = tmp_path / "next"
    initial_root.mkdir()
    job = next_root / "part"
    job.mkdir(parents=True)
    for filename in ("config.yaml", "trajectory.csv", "target.stl"):
        shutil.copy2(Path("tests/fixtures/collision_free") / filename, job / filename)
    dash_app = create_dashboard_app(initial_root)

    apply_root = next(
        value["callback"].__wrapped__
        for key, value in dash_app.callback_map.items()
        if "jobs-root-store.data" in key
    )
    root_data, value, message, tone, footer, cleared_job = apply_root(1, str(next_root))
    assert root_data["path"] == str(next_root.resolve())
    assert value == str(next_root.resolve())
    assert message == "JOBS_ROOT를 적용했습니다."
    assert tone == "is-ready"
    assert footer == str(next_root.resolve())
    assert cleared_job == ""

    refresh = next(
        item["callback"].__wrapped__
        for key, item in dash_app.callback_map.items()
        if "job-select.options" in key
    )
    options, selected = refresh(root_data, {}, None)
    assert options == [{"label": "part", "value": "part"}]
    assert selected == "part"


def test_validation_poller_runs_only_until_terminal_state(tmp_path: Path) -> None:
    job = tmp_path / "sample"
    job.mkdir()
    for filename in ("config.yaml", "trajectory.csv", "target.stl"):
        shutil.copy2(Path("tests/fixtures/collision_free") / filename, job / filename)
    manager = StubValidationManager()
    dash_app = create_dashboard_app(
        tmp_path,
        run_manager=manager,  # type: ignore[arg-type]
        replay_run_manager=StubReplayManager(),  # type: ignore[arg-type]
    )
    client = dash_app.server.test_client()

    started = _post_callback(
        dash_app,
        client,
        "validation-poller.disabled",
        inputs=[
            {"id": "run-button", "property": "n_clicks", "value": 1},
            {"id": "validation-poller", "property": "n_intervals", "value": 0},
        ],
        state=[
            {"id": "job-select", "property": "value", "value": "sample"},
            {"id": "input-inspection-store", "property": "data", "value": {}},
            {"id": "input-job-dir", "property": "value", "value": ""},
            {"id": "input-config-path", "property": "value", "value": ""},
            {"id": "input-trajectory-path", "property": "value", "value": ""},
            {"id": "input-target-path", "property": "value", "value": ""},
        ],
        changed="run-button.n_clicks",
    )
    assert started.status_code == 200
    assert started.get_json()["response"]["validation-poller"]["disabled"] is False

    manager.finish()
    finished = _post_callback(
        dash_app,
        client,
        "validation-poller.disabled",
        inputs=[
            {"id": "run-button", "property": "n_clicks", "value": 1},
            {"id": "validation-poller", "property": "n_intervals", "value": 1},
        ],
        state=[
            {"id": "job-select", "property": "value", "value": "sample"},
            {"id": "input-inspection-store", "property": "data", "value": {}},
            {"id": "input-job-dir", "property": "value", "value": ""},
            {"id": "input-config-path", "property": "value", "value": ""},
            {"id": "input-trajectory-path", "property": "value", "value": ""},
            {"id": "input-target-path", "property": "value", "value": ""},
        ],
        changed="validation-poller.n_intervals",
    )
    response = finished.get_json()["response"]
    assert response["validation-poller"]["disabled"] is True
    assert response["result-refresh"]["data"]["job_name"] == "sample"


def test_validation_rejects_inputs_changed_after_preflight(tmp_path: Path) -> None:
    job = tmp_path / "sample"
    job.mkdir()
    for filename in ("config.yaml", "trajectory.csv", "target.stl"):
        shutil.copy2(Path("tests/fixtures/collision_free") / filename, job / filename)
    manager = StubValidationManager()
    dash_app = create_dashboard_app(
        tmp_path,
        run_manager=manager,  # type: ignore[arg-type]
        replay_run_manager=StubReplayManager(),  # type: ignore[arg-type]
    )
    inspect = next(
        value["callback"].__wrapped__
        for key, value in dash_app.callback_map.items()
        if "input-inspection-store.data" in key
    )
    inspection, _content, _request = inspect(
        1,
        str(job),
        str(job / "config.yaml"),
        str(job / "trajectory.csv"),
        str(job / "target.stl"),
    )
    with (job / "trajectory.csv").open("a", encoding="utf-8") as handle:
        handle.write("\n")

    response = _post_callback(
        dash_app,
        dash_app.server.test_client(),
        "validation-poller.disabled",
        inputs=[
            {"id": "run-button", "property": "n_clicks", "value": 1},
            {"id": "validation-poller", "property": "n_intervals", "value": 0},
        ],
        state=[
            {"id": "job-select", "property": "value", "value": "sample"},
            {"id": "input-inspection-store", "property": "data", "value": inspection},
            {"id": "input-job-dir", "property": "value", "value": str(job)},
            {
                "id": "input-config-path",
                "property": "value",
                "value": str(job / "config.yaml"),
            },
            {
                "id": "input-trajectory-path",
                "property": "value",
                "value": str(job / "trajectory.csv"),
            },
            {
                "id": "input-target-path",
                "property": "value",
                "value": str(job / "target.stl"),
            },
        ],
        changed="run-button.n_clicks",
    )
    payload = response.get_json()["response"]
    assert payload["validation-poller"]["disabled"] is True
    assert "다시 실행" in str(payload["run-banner"]["children"])
    assert manager.start_count == 0


def test_replay_poller_runs_only_until_terminal_state(tmp_path: Path) -> None:
    job = tmp_path / "sample"
    job.mkdir()
    for filename in ("config.yaml", "trajectory.csv", "target.stl"):
        shutil.copy2(Path("tests/fixtures/collision_free") / filename, job / filename)
    run = job / "output" / "2026-09-05_000000"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "status": "PASS",
                "schedule": {"makespan_s": 18.0},
                "collision": {"collision_event_count": 0},
            }
        ),
        encoding="utf-8",
    )
    replay_manager = StubReplayManager()
    dash_app = create_dashboard_app(
        tmp_path,
        run_manager=StubValidationManager(),  # type: ignore[arg-type]
        replay_run_manager=replay_manager,  # type: ignore[arg-type]
    )
    client = dash_app.server.test_client()

    started = _post_callback(
        dash_app,
        client,
        "replay-poller.disabled",
        inputs=[
            {"id": "replay-generate-button", "property": "n_clicks", "value": 1},
            {"id": "replay-cancel-button", "property": "n_clicks", "value": 0},
            {"id": "replay-poller", "property": "n_intervals", "value": 0},
        ],
        state=[
            {"id": "job-select", "property": "value", "value": "sample"},
            {"id": "replay-interval", "property": "value", "value": 2.0},
        ],
        changed="replay-generate-button.n_clicks",
    )
    assert started.status_code == 200
    assert started.get_json()["response"]["replay-poller"]["disabled"] is False

    replay_manager.finish()
    finished = _post_callback(
        dash_app,
        client,
        "replay-poller.disabled",
        inputs=[
            {"id": "replay-generate-button", "property": "n_clicks", "value": 1},
            {"id": "replay-cancel-button", "property": "n_clicks", "value": 0},
            {"id": "replay-poller", "property": "n_intervals", "value": 1},
        ],
        state=[
            {"id": "job-select", "property": "value", "value": "sample"},
            {"id": "replay-interval", "property": "value", "value": 2.0},
        ],
        changed="replay-poller.n_intervals",
    )
    response = finished.get_json()["response"]
    assert response["replay-poller"]["disabled"] is True
    assert response["replay-refresh"]["data"]["job_name"] == "sample"


def test_ui_cli_forwards_local_options(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_run_validator_ui(
        job_dir: Path,
        *,
        host: str,
        port: int,
        open_browser: bool,
    ) -> None:
        captured.update(
            job_dir=job_dir,
            host=host,
            port=port,
            open_browser=open_browser,
        )

    monkeypatch.setattr("waam_validator.cli.run_validator_ui", fake_run_validator_ui)
    result = CliRunner().invoke(
        cli_app,
        ["ui", "tests", "--port", "8061", "--no-browser"],
    )

    assert result.exit_code == 0
    assert captured["job_dir"] == Path("tests")
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8061
    assert captured["open_browser"] is False


def test_dashboard_manager_runs_validation_in_subprocess(tmp_path: Path) -> None:
    job = tmp_path / "sample"
    job.mkdir()
    for filename in ("config.yaml", "trajectory.csv", "target.stl"):
        shutil.copy2(Path("examples/sample_job") / filename, job / filename)
    manager = ValidationRunManager()

    output = manager.start(JobRecord("sample", job))
    assert manager.snapshot()["running"] is True
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        snapshot = manager.snapshot()
        if not snapshot["running"]:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("dashboard validation worker did not finish")

    assert snapshot["state"] == "FINISHED"
    assert snapshot["verdict"] == "PASS"
    assert (output / "summary.json").is_file()
    assert not (output / "replay.html").exists()
    assert (output / "validation_inputs.json").is_file()

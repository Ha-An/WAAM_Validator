from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from dash.exceptions import PreventUpdate

from waam_validator.dashboard.data import JobRecord
from waam_validator.dashboard.replay_service import write_validation_input_manifest
from waam_validator.dashboard.single_app import create_validator_app


class StubManager:
    def __init__(self) -> None:
        self.started: Path | None = None
        self.state: dict[str, Any] = {
            "state": "IDLE",
            "stage": "idle",
            "message": "대기",
            "running": False,
            "elapsed_s": 0.0,
        }

    def start(self, job: JobRecord) -> Path:
        self.started = job.path
        output = job.path / "output" / "stub-running"
        self.state = {
            "state": "RUNNING",
            "stage": "collision",
            "message": "충돌 검사",
            "running": True,
            "overall_progress": 0.25,
            "stage_progress": 0.5,
            "completed_units": 5.0,
            "total_units": 10.0,
            "unit": "simulation_s",
            "elapsed_s": 2.0,
            "job_name": job.name,
            "output_directory": str(output),
            "log": "collision",
        }
        return output

    def snapshot(self) -> dict[str, Any]:
        return dict(self.state)

    def finish(self, output: Path) -> None:
        self.state.update(
            state="FINISHED",
            stage="completed",
            message="완료",
            running=False,
            overall_progress=1.0,
            stage_progress=1.0,
            verdict="PASS",
            output_directory=str(output),
        )


class IdleReplayManager:
    def snapshot(self) -> dict[str, Any]:
        return {"state": "IDLE", "running": False, "progress": 0.0}


def _callback(app: Any, fragment: str) -> Any:
    return next(
        item["callback"].__wrapped__ for key, item in app.callback_map.items() if fragment in key
    )


def _post_callback(
    app: Any,
    client: Any,
    fragment: str,
    *,
    inputs: list[dict[str, Any]],
    state: list[dict[str, Any]],
    changed: str,
) -> Any:
    output_key = next(key for key in app.callback_map if fragment in key)
    output_spec = app.callback_map[output_key]["output"]
    outputs = [
        {"id": item.component_id, "property": item.component_property} for item in output_spec
    ]
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


def test_single_job_ui_inspection_enables_same_context_run(
    fixture_root: Path, tmp_path: Path
) -> None:
    job = tmp_path / "motor"
    shutil.copytree(fixture_root / "collision_free", job)
    manager = StubManager()
    app = create_validator_app(  # type: ignore[arg-type]
        job, run_manager=manager, replay_run_manager=IdleReplayManager()
    )
    client = app.server.test_client()

    layout = client.get("/_dash-layout")
    assert layout.status_code == 200
    assert b'"id":"job-dir-input"' in layout.data
    assert b'"debounce":false' in layout.data
    assert b'"id":"job-select"' not in layout.data
    assert b'"id":"input-gantt"' in layout.data
    assert layout.data.count(b'"disabled":true') >= 3
    assert app.config.update_title is None
    css = client.get("/assets/validator.css")
    assert css.status_code == 200
    assert b".input-footer-actions" in css.data
    assert b"position: static" in css.data
    assert b".input-chart-grid .dash-graph" in css.data
    assert b"height: 320px !important" in css.data

    inspect = _callback(app, "active-input-store.data")
    with pytest.raises(PreventUpdate):
        inspect(None, None, str(job))
    active, recent, *_ = inspect(1, None, str(job))
    assert active["can_run"] is True
    assert active["context_id"]
    assert recent == {}

    button_state = _callback(app, "validation-button.disabled")
    disabled, message, recent_disabled = button_state(active, recent, None, str(job), {})
    assert disabled is False
    assert "준비 완료" in message
    assert recent_disabled is True

    response = _post_callback(
        app,
        client,
        "runtime-store.data",
        inputs=[
            {"id": "validation-button", "property": "n_clicks", "value": 1},
            {"id": "rerun-button", "property": "n_clicks", "value": None},
            {"id": "validation-poller", "property": "n_intervals", "value": 0},
        ],
        state=[{"id": "active-input-store", "property": "data", "value": active}],
        changed="validation-button.n_clicks",
    )
    assert response.status_code == 200
    body = response.get_json()["response"]
    assert manager.started == job.resolve()
    assert body["validation-poller"]["disabled"] is False
    assert body["view-store"]["data"]["view"] == "running"
    assert body["overall-progress"]["value"] == 25.0
    assert "시작했습니다" in body["validation-start-message"]["children"]

    output = job / "output" / "stub-running"
    output.mkdir(parents=True)
    (output / "summary.json").write_text(
        json.dumps({"schema_version": "1.1", "status": "PASS"}), encoding="utf-8"
    )
    manager.finish(output)
    finished = _post_callback(
        app,
        client,
        "runtime-store.data",
        inputs=[
            {"id": "validation-button", "property": "n_clicks", "value": 1},
            {"id": "rerun-button", "property": "n_clicks", "value": None},
            {"id": "validation-poller", "property": "n_intervals", "value": 1},
        ],
        state=[{"id": "active-input-store", "property": "data", "value": active}],
        changed="validation-poller.n_intervals",
    ).get_json()["response"]
    assert finished["validation-poller"]["disabled"] is True
    assert finished["view-store"]["data"]["view"] == "result"
    assert finished["current-run-store"]["data"]["output_dir"] == str(output.resolve())
    assert finished["overall-progress"]["value"] == 100.0


def test_recent_result_requires_matching_11_signature(fixture_root: Path, tmp_path: Path) -> None:
    job = tmp_path / "motor"
    shutil.copytree(fixture_root / "collision_free", job)
    run = job / "output" / "2026-09-05_120000"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(
        json.dumps({"schema_version": "1.1", "status": "PASS"}), encoding="utf-8"
    )
    write_validation_input_manifest(job, run)

    app = create_validator_app(job)
    inspect = _callback(app, "active-input-store.data")
    active, recent, *_ = inspect(1, str(job), str(job))

    assert active["can_run"] is True
    assert recent["output_dir"] == str(run)
    render_result = _callback(app, "result-content.children")
    assert render_result(recent, {}) is not None
    token = active["context_id"]
    client = app.server.test_client()
    assert client.get(f"/artifacts/{token}/{run.name}/summary.json").status_code == 200
    assert client.get(f"/artifacts/{token}/{run.name}/../config.yaml").status_code == 404

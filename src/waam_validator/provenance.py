"""Input provenance shared by validation and derived-artifact workers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

RESULT_SCHEMA_VERSION: Final = "3.0"
VALIDATION_INPUT_MANIFEST: Final = "validation_inputs.json"
INPUT_FILENAMES: Final = ("config.yaml", "trajectory.csv", "target.stl")


InputSignature = dict[str, dict[str, int]]


def input_signature(job_dir: Path) -> InputSignature:
    """Capture the exact file size and nanosecond modification time."""
    result: InputSignature = {}
    for filename in INPUT_FILENAMES:
        stat = (job_dir / filename).stat()
        result[filename] = {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return result


def write_validation_input_manifest(
    job_dir: Path,
    run_dir: Path,
    signature: InputSignature | None = None,
) -> None:
    """Write one canonical manifest for a completed validation run."""
    (run_dir / VALIDATION_INPUT_MANIFEST).write_text(
        json.dumps(
            {
                "schema_version": RESULT_SCHEMA_VERSION,
                "inputs": signature if signature is not None else input_signature(job_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def verify_validation_inputs(job_dir: Path, run_dir: Path) -> tuple[bool, str]:
    """Verify that a schema 3.0 run still refers to the current three inputs."""
    path = run_dir / VALIDATION_INPUT_MANIFEST
    if not path.is_file():
        return False, "현재 결과 형식에는 입력 지문이 없습니다. Validation을 다시 실행하세요."
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        recorded = payload.get("inputs") if isinstance(payload, dict) else None
        current = input_signature(job_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"입력 지문을 확인할 수 없습니다: {exc}"
    if payload.get("schema_version") != RESULT_SCHEMA_VERSION or recorded != current:
        return False, "검증 이후 입력 파일이 변경되었습니다. Validation을 다시 실행하세요."
    return True, ""

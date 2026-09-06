"""YAML configuration loading."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from ..errors import InputValidationError
from .models import Config


def load_config(path: Path) -> Config:
    """Load YAML, validate the fixed schema, and return immutable config."""
    if not path.is_file():
        raise InputValidationError("MISSING_CONFIG", "config.yaml is required.")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise InputValidationError("INVALID_CONFIG_SCHEMA", str(exc)) from exc
    if not isinstance(data, dict):
        raise InputValidationError(
            "INVALID_CONFIG_SCHEMA", "config.yaml must contain one mapping object."
        )
    if "schema_version" in data:
        raise InputValidationError(
            "INVALID_CONFIG_SCHEMA",
            "schema_version is not a Config field. Remove it; WAAM Validator versions "
            "the application independently from config.yaml.",
        )
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise InputValidationError("INVALID_CONFIG_SCHEMA", str(exc)) from exc

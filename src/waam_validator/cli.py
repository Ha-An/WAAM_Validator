"""Typer command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from .errors import ComputationError, WaamValidatorError
from .pipeline import check_input, run_validation
from .reporting.writers import render_console_summary, render_error_block

app = typer.Typer(
    name="waam-validator",
    help="Validate three-robot WAAM trajectory jobs.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def run(
    job_dir: Annotated[
        Path,
        typer.Argument(help="Directory containing the three fixed input files."),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Use an exact new or empty output directory."),
    ] = None,
    headless: Annotated[
        bool,
        typer.Option("--headless", help="Skip PNG and HTML visualization."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print exactly one summary JSON object to stdout."),
    ] = False,
) -> None:
    """Run the complete validation pipeline."""
    try:
        result = run_validation(job_dir, output, headless=headless)
    except WaamValidatorError as exc:
        typer.echo(render_error_block(exc, job_dir.expanduser().resolve()), err=True)
        raise typer.Exit(exc.exit_code) from None
    except Exception as exc:  # Defensive CLI boundary.
        wrapped = ComputationError("INTERNAL_CALCULATION_ERROR", str(exc))
        typer.echo(render_error_block(wrapped, job_dir.expanduser().resolve()), err=True)
        raise typer.Exit(wrapped.exit_code) from None
    if json_output:
        typer.echo(json.dumps(result.summary_dict(), ensure_ascii=False, separators=(",", ":")))
    else:
        typer.echo(render_console_summary(result))
    raise typer.Exit(0 if result.status == "PASS" else 1)


@app.command()
def check(
    job_dir: Annotated[
        Path,
        typer.Argument(help="Directory containing the three fixed input files."),
    ],
) -> None:
    """Validate input structure and parsing without running the simulation."""
    try:
        row_count, _, _ = check_input(job_dir)
    except WaamValidatorError as exc:
        typer.echo("WAAM INPUT CHECK : FAIL")
        typer.echo(f"Code    : {exc.code}")
        typer.echo(f"Message : {exc.message}")
        raise typer.Exit(exc.exit_code) from None
    typer.echo("WAAM INPUT CHECK : PASS")
    typer.echo("config.yaml    : OK")
    typer.echo(f"trajectory.csv : OK (3 robots, {row_count} rows)")
    typer.echo("target.stl     : OK")


if __name__ == "__main__":
    app()

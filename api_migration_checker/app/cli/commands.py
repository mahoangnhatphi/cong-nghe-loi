from __future__ import annotations

import shutil
from pathlib import Path

import typer

from api_migration_checker.app.config import load_config, validate_config
from api_migration_checker.app.runner import run_check
from api_migration_checker.app.storage import SQLiteRepository


app = typer.Typer(help="API Migration Checker")


@app.command()
def validate(config: Path = typer.Option(..., "--config")) -> None:
    report = validate_config(load_config(config))
    if report.valid:
        typer.echo("Config is valid")
    for warning in report.warnings:
        typer.echo(f"WARNING: {warning}")
    for error in report.errors:
        typer.echo(f"ERROR: {error}")
    if not report.valid:
        raise typer.Exit(1)


@app.command()
def run(config: Path = typer.Option(..., "--config"), case_id: str | None = typer.Option(None, "--case-id")) -> None:
    result = run_check(config, case_id=case_id)
    typer.echo(f"Run ID: {result['run_id']}")
    for key, value in result["summary"].items():
        typer.echo(f"{key}: {value}")
    typer.echo(f"SQLite result: {result['db_path']}")
    for path in result["files"].values():
        typer.echo(path)


@app.command()
def report(db: Path = typer.Option(..., "--db"), run_id: int = typer.Option(..., "--run-id")) -> None:
    repo = SQLiteRepository(db)
    row = repo.get_run(run_id)
    if not row:
        typer.echo("Run not found")
        raise typer.Exit(1)
    typer.echo(row.get("summary_json") or "No summary")
    repo.close()


@app.command()
def init_config(output: Path = typer.Option(..., "--output")) -> None:
    sample = Path.cwd() / "examples" / "config.sample.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sample, output)
    typer.echo(f"Created {output}")


@app.command()
def web(host: str = "127.0.0.1", port: int = 8000, debug: bool = False) -> None:
    from api_migration_checker.app.web.main import run_server

    run_server(host=host, port=port, debug=debug)


if __name__ == "__main__":
    app()

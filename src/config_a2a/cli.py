"""CLI entry point: ``agent --config agents.yaml [--host H] [--port P]``."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
import uvicorn

from config_a2a import __version__
from config_a2a.api import create_app
from config_a2a.config.loader import ConfigError, load_server_config
from config_a2a.persistence import run_migrations

app = typer.Typer(add_completion=False, help="Run a multi-agent A2A server defined in a YAML file.")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"config-a2a {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        readable=True,
        help="Path to the server YAML configuration.",
    ),
    host: str | None = typer.Option(None, "--host", help="Override server.host from YAML."),
    port: int | None = typer.Option(None, "--port", help="Override server.port from YAML."),
    check: bool = typer.Option(False, "--check", help="Validate the configuration and exit."),
    show_version: bool = typer.Option(  # noqa: ARG001 — handled in callback
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Start the multi-agent A2A server defined by ``config``."""
    if ctx.invoked_subcommand is not None:
        return
    if config is None:
        typer.echo("error: --config is required", err=True)
        raise typer.Exit(code=2)
    try:
        server_config = load_server_config(config)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if check:
        agent_list = ", ".join(a.slug or a.name for a in server_config.agents) or "(none)"
        typer.echo(f"ok: {server_config.name} v{server_config.version} agents=[{agent_list}]")
        return
    bind_host = host or server_config.server.host
    bind_port = port or server_config.server.port
    if server_config.persistence.run_migrations_on_start:
        try:
            run_migrations(server_config.persistence)
        except Exception as exc:  # pylint: disable=broad-except
            typer.echo(f"warning: alembic upgrade failed: {exc}", err=True)
    fastapi_app = create_app(server_config)
    typer.echo(
        f"config-a2a: serving '{server_config.name}' on http://{bind_host}:{bind_port} "
        f"agents={[a.slug for a in server_config.agents]}"
    )
    uvicorn.run(fastapi_app, host=bind_host, port=bind_port, log_level="info")


def main() -> None:
    """Console-script entry point."""
    try:
        app()
    except SystemExit:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        typer.echo(f"fatal: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

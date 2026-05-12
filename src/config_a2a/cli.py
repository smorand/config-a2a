"""Command-line entry point: ``agent --config FILE [--host H] [--port P]``."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
import uvicorn

from config_a2a import __version__
from config_a2a.api import create_app
from config_a2a.config.loader import ConfigError, load_agent_config
from config_a2a.runtime import AgentRuntime

app = typer.Typer(add_completion=False, help="Run an A2A agent defined in a YAML file.")


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
        help="Path to the agent YAML configuration.",
    ),
    host: str | None = typer.Option(None, "--host", help="Override server.host from YAML."),
    port: int | None = typer.Option(None, "--port", help="Override server.port from YAML."),
    check: bool = typer.Option(False, "--check", help="Validate the configuration and exit."),
    show_version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),  # noqa: ARG001 — handled in callback
) -> None:
    """Start the FastAPI A2A server defined by ``config``."""
    if ctx.invoked_subcommand is not None:
        return
    if config is None:
        typer.echo("error: --config is required", err=True)
        raise typer.Exit(code=2)
    try:
        agent_config = load_agent_config(config)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if check:
        typer.echo(f"ok: {agent_config.name} v{agent_config.version} ({agent_config.pattern.type})")
        return
    bind_host = host or agent_config.server.host
    bind_port = port or agent_config.server.port
    runtime = AgentRuntime(agent_config)
    fastapi_app = create_app(runtime)
    typer.echo(
        f"config-a2a: serving '{agent_config.name}' on http://{bind_host}:{bind_port} "
        f"(pattern={agent_config.pattern.type})"
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

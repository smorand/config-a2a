# CLAUDE.md — config-a2a

Compact agent index. Load `.agent_docs/` files on demand for details.

## Project

FastAPI service that builds A2A agents from a YAML configuration file. Python 3.13, managed with `uv`.

## Key commands

```bash
uv sync --extra dev           # install deps incl. dev tools
uv run config-a2a             # start the FastAPI service
uv run pytest                 # run tests
uv run black -l 120 src tests # format
uv run pylint src             # lint (target: 10/10)
uv run bandit -r src          # security scan
uv run safety check           # dependency vulnerabilities
```

## Conventions

* Python 3.13 only; no compatibility shims for older versions.
* All public functions use type hints; `mypy --strict` clean.
* `black` line length = 120.
* f-strings for interpolation; `%`-style for logging.
* Async I/O with `aiohttp`, not `requests`.
* Pydantic models forbid extra fields (`extra="forbid"`).
* Settings loaded via `pydantic-settings` from env / `.env`.
* `# nosec` and pylint disables must be justified inline.

## Layout

* `src/config_a2a/api.py` — FastAPI app factory (`create_app`) and module-level `app`.
* `src/config_a2a/cli.py` — `main()` runs uvicorn; bound to `config-a2a` script.
* `src/config_a2a/loader.py` — `load_agent_config(Path) -> AgentConfig`.
* `src/config_a2a/models.py` — `AgentConfig`, `SkillConfig`.
* `src/config_a2a/settings.py` — `Settings`, `get_settings()`.
* `examples/agent.yaml` — reference configuration used by tests and the default `/agent` endpoint.

## Documentation index

See `.agent_docs/` for topic-focused notes (created as the project grows).

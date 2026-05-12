# config-a2a

Create advanced A2A (Agent-to-Agent) agents using YAML configuration files.

## Overview

`config-a2a` lets you declare agents, their skills, inputs, outputs, and metadata in a single YAML file, then exposes them through a FastAPI service. Configuration is validated with Pydantic so invalid agents fail fast.

## Tech stack

* Python 3.13
* FastAPI + Uvicorn (ASGI server)
* Pydantic v2 (validation) and `pydantic-settings`
* PyYAML for configuration parsing
* `uv` for dependency management

## Project layout

```
config-a2a/
├── src/config_a2a/      # Library code
│   ├── api.py           # FastAPI app factory
│   ├── cli.py           # `config-a2a` entry point
│   ├── loader.py        # YAML loader and validator
│   ├── models.py        # Pydantic models
│   └── settings.py      # Environment-driven settings
├── examples/agent.yaml  # Sample agent configuration
├── tests/               # Pytest test suite
├── pyproject.toml
└── README.md
```

## Installation

```bash
uv sync
```

Install development extras:

```bash
uv sync --extra dev
```

## Configuration

Settings come from environment variables (or a `.env` file). Copy `.env.example` to `.env` and adjust as needed.

| Variable      | Default                 | Description                          |
| ------------- | ----------------------- | ------------------------------------ |
| `APP_NAME`    | `config-a2a`            | Service name                         |
| `HOST`        | `0.0.0.0`               | Bind host                            |
| `PORT`        | `8000`                  | Bind port                            |
| `LOG_LEVEL`   | `INFO`                  | Uvicorn log level                    |
| `CONFIG_PATH` | `examples/agent.yaml`   | Path to the agent YAML configuration |

## Agent configuration

```yaml
name: example-agent
version: 0.1.0
description: An example A2A agent loaded from YAML configuration.
skills:
  - name: greet
    description: Return a friendly greeting.
    inputs:
      name: string
    outputs:
      message: string
metadata:
  author: Sebastien MORAND
```

## Running

```bash
uv run config-a2a
```

Then visit:

* `http://localhost:8000/health` for a health check
* `http://localhost:8000/agent` to read the loaded agent configuration
* `http://localhost:8000/docs` for the interactive Swagger UI

## Development

```bash
# Format
uv run black -l 120 src tests

# Lint
uv run pylint src

# Security
uv run bandit -r src
uv run safety check

# Tests
uv run pytest
```

## License

MIT

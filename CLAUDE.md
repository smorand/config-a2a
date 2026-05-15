# CLAUDE.md — config-a2a

Compact index. Topic-focused notes live in `.agent_docs/`; load only what you need.

## Project

Headless **multi-agent** A2A server factory. One YAML file (`ServerConfig`)
boots one FastAPI process exposing N agents under `/agents/<slug>`, plus an
admin REST surface for hot load / reload / unload. Started with
`uv run agent --config agents.yaml [--host H] [--port P]`. Python 3.13,
managed with `uv`. Talks to `web-a2a` over the A2A v1.0 wire (per-agent URL).

## Key commands

```bash
uv sync --extra dev
uv run agent --config <agents.yaml> [--host H] [--port P] [--check]
uv run pytest tests/unit
RUN_E2E=1 uv run pytest tests/e2e          # needs OPENROUTER_API_KEY
uv run alembic upgrade head                # SQLite by default
make format / lint / test / e2e            # see Makefile
```

## Conventions

* Python 3.13, no compat shims.
* Async-first; httpx, SQLAlchemy 2.x async, mcp SDK.
* No vendor SDKs for LLMs; every provider posts JSON through httpx.
* Pydantic models forbid extra keys (`extra="forbid"`).
* All path leaves listed in `_PATH_KEYS` (loader.py) are made absolute against the YAML directory; everything else is left untouched.
* OTel attributes use the GenAI 2025 semconv (`gen_ai.system`, `gen_ai.request.model`, ...); spans also carry `agent.slug`. The JSONL exporter redacts `authorization`, `api_key`, `cookie`, and prompt / response bodies.
* Sub-agents via `agent_ref` (Handoff pattern) run in-process with `depth` incremented; guardrails enforce `max_depth`.
* One TracerProvider, one DB engine, one MCP discovery pool per server process; agents share them.

## Layout (top-level)

```
src/config_a2a/
  cli.py                    # Typer entrypoint (loads ServerConfig)
  api.py                    # FastAPI factory + admin router + per-agent auth dependency
  server.py                 # Server container: agent registry, load/unload/reload
  runtime.py                # AgentRuntime composes config / provider / mcp / tasks
  config/{models,loader,prompts}.py
  providers/{base,openai_compat,anthropic,google,vertex,registry}.py
  mcp/{client,stdio,streamable_http,sse,tool_format}.py
  patterns/{simple,react,plan_execute,handoff,orchestrate,debate,tree_of_thoughts}.py
  a2a/{card,envelope,sse,routes,client}.py
  guardrails/{anti_loop,confirmations}.py
  observability/{otel,jsonl_exporter}.py
  persistence/{models,engine,repository,store}.py
alembic/                    # migrations (0001 initial, 0002 memory, 0003 multi-agent)
config_examples/*/agents.yaml   # one runnable server per directory
tests/unit / tests/e2e / tests/fixtures
```

## Documentation index

* `.agent_docs/yaml-schema.md` — full `ServerConfig` / `AgentConfig` field reference.
* `.agent_docs/a2a-protocol.md` — routes (`/agents/<slug>/...`), admin surface, directory.
* `.agent_docs/patterns.md` — when to pick each pattern, gotchas, anti-loop notes.
* `.agent_docs/providers.md` — per-provider auth and tool-format quirks.
* `.agent_docs/mcp.md` — transport rules, destructive-hint flow, filtering.
* `.agent_docs/observability.md` — OTel spans, redaction rules, OTLP switch.
* `.agent_docs/persistence.md` — schema, Alembic, Postgres vs SQLite.
* `.agent_docs/memory.md` — `memory:` block, hook flow, store interface, extractor, scopes.

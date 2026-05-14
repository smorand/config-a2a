# Persistence

Async SQLAlchemy 2.x. The same migration runs on SQLite (default) and PostgreSQL.

## Tables (see `persistence/models.py`)

- `tasks` — one row per A2A task. Stores the full `status_payload`, a `pending_action` (for INPUT_REQUIRED resume), and free-form `extra`. Indexed by `context_id`, `agent_slug`, and `agent_name`. `agent_slug` is the canonical filter in a multi-agent server; `agent_name` is denormalised for human inspection.
- `messages` — ordered messages within a task. JSON `parts` and `extra` columns.
- `run_steps` — structured trace events (`llm_call`, `tool_call`, `status_update`). Populated lazily by the runtime when the persistent store is used.
- `memory_records` — cross-task memory; same `agent_slug` discriminator.

## Multi-agent layout

One server, one database. Migration `0003_multi_agent` adds the `agent_slug`
column to `tasks` and `memory_records` (backfilled from `agent_name` for
backward compat). Every repository query filters by `agent_slug` so agents
cannot see each other's tasks even though they share the engine.

The server engine is built once from `ServerConfig.persistence` and reused
across all agents; an agent that needs an isolated DB sets its own
`persistence:` block (then it gets a private engine).

## Switching backends

```yaml
persistence:
  backend: postgresql
  url: postgresql+asyncpg://user:${PGPASS}@localhost/agents
```

`alembic/env.py` reads `CONFIG_A2A_DATABASE_URL` from the environment (set by the CLI to the YAML `url`). Run migrations manually with:

```bash
CONFIG_A2A_DATABASE_URL=postgresql+asyncpg://… uv run alembic upgrade head
```

## In-memory mode (tests)

`AgentRuntime(config, tasks=None)` uses `InMemoryTaskStore` so unit tests can run without touching disk. Production code never does this — the CLI always wires `build_task_store(...)`.

## Resume contract

When a message arrives with `taskId`:

1. The route checks the store; unknown id → 404.
2. The runtime hands the existing task to the pattern, which reads `pending_action` from the stored row.
3. The pattern decides whether to continue (e.g. approval of a destructive tool) or cancel.
4. `update_status(..., clear_pending=True)` resets the pending slot once the action has been consumed.

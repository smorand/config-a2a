# Persistence

Async SQLAlchemy 2.x. The same migration runs on SQLite (default) and PostgreSQL.

## Tables (see `persistence/models.py`)

- `tasks` — one row per A2A task. Stores the full `status_payload`, a `pending_action` (for INPUT_REQUIRED resume), and free-form `extra`. Indexed by `context_id` and `agent_name`.
- `messages` — ordered messages within a task. JSON `parts` and `extra` columns.
- `run_steps` — structured trace events (`llm_call`, `tool_call`, `status_update`). Populated lazily by the runtime when the persistent store is used.

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

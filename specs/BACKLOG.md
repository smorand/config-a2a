# Backlog

Captured ideas that have been validated by the user but not yet implemented.
Each entry is self-contained enough to be picked up in a separate session.

---

## Multi-agent server (one FastAPI process, N agents under `/agents/<slug>`)

**Why.** Today each YAML manifest boots its own FastAPI process on its own
port. Even a small pipeline (cf. `config_examples/08-coding-agent/`) needs 9
sidecar processes. Hosting N agents inside a single process simplifies dev
ergonomics (one `agent --config server.yaml`), deployment (one port, one
reverse-proxy route), and lets us introduce a clean admin REST surface for
runtime management. Breaking change accepted (we're in 0.1), no back-compat
path required.

**YAML shape (top-level).**

```yaml
name: coding-agent
description: 8-phase ticket-to-PR pipeline.
version: 0.1.0

server:
  host: 0.0.0.0
  port: 9100

persistence:                       # default, agents inherit
  backend: sqlite                  # sqlite OR postgresql, both single-DB
  url: sqlite+aiosqlite:///./state/server.db
  run_migrations_on_start: true

observability:                     # single TracerProvider, agent.slug attr
  otel:
    enabled: true
    service_name: coding-agent
    exporter: jsonl
    jsonl_path: traces/coding-agent.jsonl

card:                              # server-level Agent Card metadata, heritable
  provider:
    organization: IBM
    url: https://ibm.com

admin:
  enabled: true
  authentication:                  # separate from A2A traffic auth
    type: bearer
    value_env: ADMIN_TOKEN

agents: []                         # optional; empty allowed (server boots admin-only)
```

**YAML shape (each entry of `agents:`).**

```yaml
agents:
  - slug: classification           # optional, default = slugified name
    name: coding-agent-classification
    description: "Phase 1/8 — detect the project type."
    model: { provider: openai-compatible, ... }
    pattern: { type: simple }
    prompts: { system_file: prompts/01.md }
    tools: { mcp_servers: [...], filters: {...} }
    guardrails: { ... }
    confirmations: { ... }
    memory: { ... }
    authentication: { ... }       # optional override of server-level
    skills: [ { id: classify, ... } ]
    card:                          # optional Agent Card metadata sub-block
      documentation_url: https://...
      icon_url: https://...
      default_input_modes: [text]
      default_output_modes: [text]
      provider: { ... }            # overrides server card.provider
      capabilities:
        push_notifications: false
        state_transition_history: true
      supports_authenticated_extended_card: false
```

**Routes (A2A).**

| Route | Purpose |
|---|---|
| `GET /health` | Process liveness |
| `GET /.well-known/agent-card.json` | Directory `{name, description, version, agents: [{slug, url, name, description}, ...]}` (extension to A2A 1.0) |
| `GET /agents/{slug}/.well-known/agent-card.json` | Per-agent Agent Card |
| `POST /agents/{slug}/message:send` | Existing, prefixed |
| `POST /agents/{slug}/message:stream` | Existing, prefixed |
| `GET /agents/{slug}/tasks`, `/tasks/{id}`, `POST .../tasks/{id}:cancel` | Existing, prefixed |

**Admin REST (no verbs).**

| Method | URL | Effect |
|---|---|---|
| GET    | `/admin/status` | Server rollup (uptime, agents loaded, in-flight, memory) |
| GET    | `/admin/agents` | List `[{slug, name, model, status, uptime, in_flight, last_task_at}]` |
| POST   | `/admin/agents` | Atomic load. Body: inline YAML/JSON OR `{config_path: "..."}`. 201 on success, 4xx/5xx with detail on failure. No zombie agent. |
| GET    | `/admin/agents/{slug}` | Resolved config + status |
| GET    | `/admin/agents/{slug}/status` | Status only (cheap polling) |
| DELETE | `/admin/agents/{slug}` | Drain in-flight, close MCP children, unmount routes |
| POST   | `/admin/agents/{slug}/reloads` | Async reload op; returns `{id, status: "pending"}` |
| GET    | `/admin/agents/{slug}/reloads/{id}` | `pending → running → completed | failed` |

**Loader invariants.**

- `agents: []` allowed (admin-only server).
- Reject if `admin.enabled: false` AND `agents == []` (inert server).
- `slug` must match `^[a-z0-9][a-z0-9-]*$` and be unique.
- `persistence` and `authentication` at the agent level override server defaults.

**Degraded mode.** If MCP children die or model 5xx repeatedly mid-life, agent
transitions to `status: degraded` but stays exposed. Operator uses
`POST .../reloads` or `DELETE` to remediate.

**Critical files to touch.**

- `src/config_a2a/config/models.py` — split `AgentConfig` into `ServerConfig` (top) + `AgentConfig` (each); add `CardConfig`, `AdminConfig`, `slug` field.
- `src/config_a2a/config/loader.py` — new loader for `ServerConfig`.
- `src/config_a2a/api.py` — one FastAPI app; mount per-agent router with `prefix=f"/agents/{slug}"`; admin router; per-agent auth dependency.
- `src/config_a2a/a2a/routes.py` — keep `create_router(runtime)`; mounted N times with prefix.
- `src/config_a2a/a2a/card.py` — read `card:` sub-block; snake→camelCase; add `provider`, `documentationUrl`, `iconUrl`, `defaultInputModes`, `defaultOutputModes`, `supportsAuthenticatedExtendedCard`.
- `src/config_a2a/runtime.py` — one `AgentRuntime` per agent, registered in a `Server` container.
- `src/config_a2a/cli.py` — load `ServerConfig` instead of `AgentConfig`.
- `src/config_a2a/persistence/{engine,models,repository}.py` — single engine; `agent_slug` discriminator on task/memory tables; queries filter by `agent_slug`.
- `src/config_a2a/observability/otel.py` — single `TracerProvider`; `agent.slug` resource attribute on each span.
- `alembic/versions/<new>.py` — migration adding `agent_slug` columns.
- `config_examples/*/agent.yaml` plus the 9 YAMLs under `config_examples/08-coding-agent/` — rewritten as a single `server.yaml` each.
- `.agent_docs/yaml-schema.md`, `.agent_docs/a2a-protocol.md` — full rewrite.
- `tests/unit/test_loader*.py`, `tests/unit/test_api*.py`, `tests/e2e/*` fixtures.
- `README.md`, `CLAUDE.md` (project).

**Verification.**

1. `uv run agent --config config_examples/01-simple/server.yaml --check` validates.
2. Single-agent server: `GET /agents/simple/.well-known/agent-card.json` returns 200; `GET /.well-known/agent-card.json` returns directory with one entry.
3. Empty server (`agents: []`): admin responds, no `/agents/*` routes mounted.
4. `POST /admin/agents` with valid body → 201; agent immediately reachable.
5. `POST /admin/agents` with broken body (unreachable MCP) → 4xx with failure detail; `GET /admin/agents` shows no zombie.
6. 8-coding-agent example migrated to a single server YAML; the orchestrator's `orchestrate.agents[*].a2a_url` becomes `http://localhost:9100/agents/<slug>`.
7. Full pytest unit + e2e green.

---

## A2A skill behavioural support (validation + optional dispatch)

**Why.** `skills:` is currently advertised in the Agent Card (cf.
`src/config_a2a/a2a/card.py:27-38`) but the runtime ignores
`message.skillId` entirely. We either honor the protocol or remove the
noise. Stays inside A2A 1.0; Claude-style dynamic `SKILL.md` discovery is
explicitly out of scope (model/harness concern, not relevant for
purpose-built business agents).

**Scope, sequenced.**

1. **Validation (cheap).** Inbound `message.skillId` must match one of
   `agents[].skills[*].id` for that agent; otherwise return A2A 400 with
   body `{error: "unknown skill", skill_id: "..."}`. Empty / missing
   `skillId` continues to work (default behaviour). ~10 lines.
2. **Behavioural dispatch (optional, decide at implementation time).**
   Extend `SkillConfig` with optional overrides applied when that
   `skillId` is invoked:
   - `prompt` — replaces the agent's system prompt
   - `model` — overrides `agents[].model` for this request
   - `tools_filter` — `{include: [...], exclude: [...]}` restricts MCP tool surface
   - `examples` (already declared) — injected as few-shot prefix
   Coverage decided when implemented, depending on demand.

**Out of scope.** Claude-style on-disk `SKILL.md` discovery + progressive
disclosure. The user considers that a model/harness concern, not an A2A
agent concern.

**Critical files to touch (validation layer).**

- `src/config_a2a/a2a/envelope.py` — surface `skillId` if not already parsed.
- `src/config_a2a/a2a/routes.py` — check `skillId` against the agent's `config.skills` in `message:send` and `message:stream`.
- `src/config_a2a/runtime.py` — accept `skill_id: str | None` on `run_message` (no-op for layer 1, hook for layer 2).
- `tests/unit/test_routes.py` — coverage for unknown / missing / valid `skillId`.

**Verification.**

1. `POST /agents/<slug>/message:send` with valid `skillId` → 200.
2. Same call with unknown `skillId` → 400, body `{error: "unknown skill", skill_id: "..."}`.
3. Same call without `skillId` → 200 (backwards-compatible).

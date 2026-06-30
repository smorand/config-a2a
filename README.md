# config-a2a

Headless A2A (Agent-to-Agent) **multi-agent server factory** driven by YAML.

One YAML file describes a FastAPI process and the agents it hosts. Run
`uv run agent --config agents.yaml` and you get one HTTP port serving N
A2A 1.0 agents under `/agents/<slug>`, plus an admin REST surface for hot
load / reload / unload. No UI, no boilerplate.

## Why

The companion project `lcnc-a2a` builds A2A agents from a web form (Postgres
plus HTMX). That UI is great for exploration but the artefacts are not
version-controllable, not CI/CD-friendly, and the patterns are limited.
`config-a2a` solves the same problem from the other direction: agents are
pure YAML, every pattern from the recent best-practice playbook is supported,
several agents share one FastAPI process, and the whole runtime is headless.

## Patterns supported

| Pattern             | Topology                                | Use it for                                        |
| ------------------- | --------------------------------------- | ------------------------------------------------- |
| `simple`            | One LLM call, optional tool loop        | Q&A, single-shot tasks                            |
| `react`             | Thought, Action, Observation loop       | Tool-heavy tasks with reasoning trace             |
| `plan_execute`      | Planner, Sequential executor, Synth     | Multi-step research, transformations              |
| `handoff`           | Router, 1 of N (local or remote)        | Specialist routing                                |
| `orchestrate`       | Parallel/sequential fan-out, aggregator | Panel-of-experts, multi-source aggregation        |
| `debate`            | Parallel rounds of N debaters, judge    | Argumentation, devil's-advocate analysis          |
| `tree_of_thoughts`  | Branch, evaluate, prune, repeat         | Exploration of solution space                     |

## LLM providers

* `anthropic`, `google`, `vertex`, `openai-compatible` (OpenRouter, vLLM, llama.cpp, ...)

All providers go through `httpx.AsyncClient` directly; no vendor SDK pinning.

## MCP transports

* `stdio`, `streamable-http`, `sse` (legacy)

### Native JuiceFS

A `juicefs:` block on an agent is sugar over a `streamable-http` MCP server
(`mcp-juicefs`) with **per-request end-user identity forwarding** (the verified
Bearer JWT on `X-Forwarded-Authorization`, configured via the server-wide
`identity:` block) and an optional `default_mount_id` surfaced to the model as
its current project. Volumes are provisioned out of band; the runtime stays 100%
MCP over HTTP (no JuiceFS SDK). See `.agent_docs/juicefs.md` and
`specs/juicefs-integration.md`.

## Quickstart

```bash
uv sync --extra dev
export OPENROUTER_API_KEY=...
uv run agent --config config_examples/01-simple/agents.yaml --port 9001
```

Then in another shell:

```bash
curl http://localhost:9001/.well-known/agent-card.json | jq          # directory
curl http://localhost:9001/agents/simple/.well-known/agent-card.json | jq
curl -X POST http://localhost:9001/agents/simple/message:send \
  -H 'Content-Type: application/json' \
  -d '{"message":{"messageId":"q1","role":"ROLE_USER","parts":[{"text":"Say hi"}]}}'
```

Register the server in `web-a2a` by pointing the "add agent" form at the
relevant `/agents/<slug>` URL (one entry per agent).

## YAML at a glance

```yaml
name: my-server
description: A friendly assistant.
version: 0.1.0

server:
  host: 0.0.0.0
  port: 9001

persistence:
  backend: sqlite                     # sqlite | postgresql
  url: sqlite+aiosqlite:///./state/my-server.db
  run_migrations_on_start: true

observability:
  otel:
    enabled: true
    exporter: jsonl                   # jsonl | otlp
    jsonl_path: traces/my-server.jsonl

admin:
  enabled: true

agents:
  - slug: simple                      # mounted at /agents/simple
    name: simple-assistant
    description: One-shot chat.
    model:
      provider: openai-compatible
      model: openrouter/auto:free
      api_key_env: OPENROUTER_API_KEY
      base_url: https://openrouter.ai/api/v1
    pattern:
      type: simple
    prompts:
      system_file: prompts/system.md
    guardrails:
      max_loops: 5
      max_tokens: 8000
```

See `.agent_docs/yaml-schema.md` for the full field reference (including
per-pattern blocks, MCP, memory, confirmations, the per-agent `card:`
sub-block).

## Admin REST

When `admin.enabled` is `true` (the default), the server also exposes:

```
GET    /admin/status
GET    /admin/agents
POST   /admin/agents                              # body: inline YAML/JSON OR {"config_path": "..."}
GET    /admin/agents/{slug}
GET    /admin/agents/{slug}/status
DELETE /admin/agents/{slug}
POST   /admin/agents/{slug}/reloads               # 202, async op
GET    /admin/agents/{slug}/reloads/{op_id}
```

See `.agent_docs/a2a-protocol.md` for response shapes.

## Examples

| Folder                                  | Agents | Notes                                                  |
| --------------------------------------- | -----: | ------------------------------------------------------ |
| `config_examples/01-simple`             | 1      | One LLM call, no tools                                 |
| `config_examples/02-react`              | 1      | Filesystem MCP server over stdio                       |
| `config_examples/03-plan-execute`       | 1      | Planner / executor / synth prompts in files            |
| `config_examples/04-handoff`            | 3      | Router + two sub-agents (math, chat) in one server     |
| `config_examples/05-orchestrate`        | 1      | Parallel fan-out to remote A2A agents                  |
| `config_examples/06-debate`             | 1      | Pro / Con / Judge                                      |
| `config_examples/07-tree-of-thoughts`   | 1      | Branch / evaluate / prune                              |
| `config_examples/08-memory`             | 1      | User-fact recall across independent contexts           |
| `config_examples/08-coding-agent`       | 9      | 8-phase ticket-to-PR pipeline plus orchestrator        |
| `config_examples/09-juicefs`            | 1      | Native `juicefs:` block, per-user identity forwarding  |

## Development

```bash
make sync          # uv sync --extra dev
make format        # ruff format
make lint          # ruff + mypy
make test          # pytest tests/unit
make e2e           # RUN_E2E=1 pytest tests/e2e (needs OPENROUTER_API_KEY)
make migrate       # alembic upgrade head
```

## Persistence

Task state, message history, and (when enabled) memory records are persisted
via async SQLAlchemy 2.x against a single server-level database. SQLite is the
default; switch the YAML `persistence.url` to `postgresql+asyncpg://...` for
production. Every row carries `agent_slug` so multi-agent servers stay
correctly partitioned.

## Observability

One `TracerProvider` per server; spans carry an `agent.slug` attribute so
multi-agent logs filter cleanly. Sensitive headers and prompt / response text
are redacted at export time. Switch the exporter to `otlp` to ship to
Honeycomb, Datadog, etc.

## Authentication

Two independent surfaces:

* `agents[*].authentication` gates the per-agent A2A routes (`/agents/<slug>/...`).
* `admin.authentication` gates the admin REST surface (`/admin/...`).

In both cases, set `type: bearer` (or `api_key`) and `value_env: TOKEN_NAME`,
then export the env var. `/health` and `/.well-known/agent-card.json` are
always public.

## License

MIT

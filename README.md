# config-a2a

Create advanced A2A (Agent-to-Agent) agents from YAML configuration files.

`config-a2a` is an "agent factory": one YAML manifest turns into a production-grade A2A v1.0 server that `web-a2a` (or any A2A client) can register and chat with. No UI, no database to provision, no boilerplate — write a config, run `uv run agent --config file.yaml`, and you have an agent on a port.

## Why

The companion project `lcnc-a2a` builds A2A agents from a web form (Postgres + HTMX). That UI is great for exploration but the artefacts are not version-controllable, not CI/CD-friendly, and the patterns are limited to Simple, ReAct, and Plan & Execute. `config-a2a` solves the same problem from the other direction: agents are pure YAML, every pattern from the recent best-practice playbook is supported, and the whole runtime is headless.

## Patterns supported

| Pattern             | Topology                              | Use it for                                        |
| ------------------- | ------------------------------------- | ------------------------------------------------- |
| `simple`            | One LLM call, optional tool loop      | Q&A, single-shot tasks                            |
| `react`             | Thought → Action → Observation loop   | Tool-heavy tasks with reasoning trace             |
| `plan_execute`      | Planner → Sequential executor → Synth | Multi-step research, transformations              |
| `handoff`           | Router → 1 of N (local or remote)     | Specialist routing                                |
| `orchestrate`       | Parallel/sequential fan-out → aggregator | Panel-of-experts, multi-source aggregation     |
| `debate`            | Parallel rounds of N debaters → judge | Argumentation, devil's-advocate analysis          |
| `tree_of_thoughts`  | Branch → evaluate → prune → repeat    | Exploration of solution space                     |

## LLM providers

* `anthropic` — Anthropic Messages API, Claude 4.x
* `google` — Google Generative Language API (Gemini, API key)
* `vertex` — VertexAI Gemini with ADC (gcloud / service account)
* `openai-compatible` — any `/chat/completions` endpoint (OpenRouter, llama.cpp, vLLM, local servers)

All providers go through `httpx.AsyncClient` directly — no vendor SDK pinning.

## MCP transports

* `stdio` — environment-scrubbed, 10 s discovery timeout
* `streamable-http` — current MCP spec (2025-03-26)
* `sse` — legacy, emits a `DeprecationWarning` at boot (kept for Keboola / Atlassian compatibility windows)

## Quickstart

```bash
uv sync --extra dev
export OPENROUTER_API_KEY=...
uv run agent --config config_examples/01-simple/agent.yaml --port 9001
```

Then in another shell:

```bash
curl http://localhost:9001/.well-known/a2a/agent-card | jq
curl -X POST http://localhost:9001/message:send \
  -H 'Content-Type: application/json' \
  -d '{"message":{"messageId":"q1","role":"ROLE_USER","parts":[{"text":"Say hi"}]}}'
```

To register the agent in `web-a2a`, point its "add agent" form at `http://localhost:9001`.

## YAML schema (canonical)

```yaml
name: my-agent
version: 0.1.0
description: A friendly assistant.

server:
  host: 0.0.0.0
  port: 9001                # CLI --port overrides this

persistence:
  backend: sqlite           # sqlite | postgresql
  url: sqlite+aiosqlite:///./state/my-agent.db
  run_migrations_on_start: true

model:
  provider: anthropic
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY

pattern:
  type: simple              # simple | react | plan_execute | handoff | orchestrate | debate | tree_of_thoughts

prompts:
  system_file: prompts/system.md   # or `system: "inline..."`

tools:
  mcp_servers:
    - name: fs
      transport: stdio
      command: npx
      args: [-y, "@modelcontextprotocol/server-filesystem", /tmp]
  filters:
    exclude: ["*delete*"]

guardrails:
  max_loops: 30
  max_tokens: 200000
  timeout_seconds: 300
  max_depth: 5
  anti_loop:
    enabled: true
    similarity_threshold: 0.92

confirmations:
  destructive_hint: prompt           # prompt | auto_approve | auto_deny
  per_tool:
    fs.delete_file: prompt

observability:
  otel:
    enabled: true
    exporter: jsonl                  # jsonl | otlp
    jsonl_path: traces/my-agent.jsonl

skills:
  - id: chat
    name: Chat
    description: Open-ended chat.
    tags: [chat]
    examples: ["What can you do?"]

authentication:
  type: none                          # none | bearer | api_key
  # value_env: AGENT_BEARER_TOKEN
```

See `.agent_docs/yaml-schema.md` for the per-pattern blocks and full field reference.

## Examples

| Folder                              | Pattern              | Notes                                            |
| ----------------------------------- | -------------------- | ------------------------------------------------ |
| `config_examples/01-simple`         | `simple`             | One LLM call, no tools, OpenRouter free model    |
| `config_examples/02-react`          | `react` (with tools) | Filesystem MCP server over stdio                 |
| `config_examples/03-plan-execute`   | `plan_execute`       | Planner / executor / synth prompts in files      |
| `config_examples/04-handoff`        | `handoff`            | Router + two local sub-agents (`math`, `chat`)   |
| `config_examples/05-orchestrate`    | `orchestrate`        | Parallel fan-out to remote A2A agents            |
| `config_examples/06-debate`         | `debate`             | Pro / Con / Judge                                |
| `config_examples/07-tree-of-thoughts` | `tree_of_thoughts` | Branch / evaluate / prune                        |

## Development

```bash
make sync          # uv sync --extra dev
make format        # black -l 120 src tests
make lint          # pylint src
make test          # pytest tests/unit
make e2e           # RUN_E2E=1 pytest tests/e2e (needs OPENROUTER_API_KEY)
make migrate       # alembic upgrade head
make run-simple    # uv run agent --config config_examples/01-simple/agent.yaml
```

## Persistence

Task state and message history are persisted via async SQLAlchemy 2.x. SQLite is the default (`sqlite+aiosqlite://`). Switch the YAML `persistence.url` to a `postgresql+asyncpg://…` URL for production; the same Alembic migration runs on both backends.

## Observability

Spans are emitted with the OTel GenAI semantic conventions (2025) and written one-per-line to `traces/<service>.jsonl`. Sensitive headers (`Authorization`, `x-api-key`, `cookie`, prompt/response text) are redacted at export time. Switch the exporter to `otlp` to ship to Honeycomb, Datadog, etc.

## Authentication

When `authentication.type` is `bearer` or `api_key`, every endpoint except `/health` and `/.well-known/…` is gated; the value comes from the env var named in `value_env`. The Agent Card exposes the matching `securitySchemes` so clients know how to authenticate.

## License

MIT

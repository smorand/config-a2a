# YAML schema

One YAML file boots one FastAPI process exposing N agents under
`/agents/<slug>`. The top-level object is a `ServerConfig`; agents are listed
under `agents:`.

## Top level: `ServerConfig`

```yaml
name: my-server                  # required; service.name in OTel
description: short summary       # optional
version: 0.1.0                   # optional

server:
  host: 0.0.0.0                  # default
  port: 9000                     # default

persistence:                     # shared by all agents unless overridden
  backend: sqlite                # sqlite | postgresql
  url: sqlite+aiosqlite:///./state/server.db
  run_migrations_on_start: true

observability:                   # single TracerProvider shared by all agents
  otel:
    enabled: true
    service_name: my-server      # default = name
    exporter: jsonl              # jsonl | otlp
    jsonl_path: traces/server.jsonl
    otlp_endpoint: http://localhost:4318/v1/traces

card:                            # server-level Agent Card metadata (heritable)
  provider:
    organization: IBM
    url: https://ibm.com
  documentation_url: https://...
  icon_url: https://...
  default_input_modes: [text]
  default_output_modes: [text]
  capabilities:
    streaming: true
    push_notifications: false
    state_transition_history: true
  supports_authenticated_extended_card: false

admin:
  enabled: true                  # default. when false AND agents=[], server rejected.
  authentication:                # separate from per-agent A2A auth
    type: none                   # none | bearer | api_key
    header_name: Authorization
    value_env: ADMIN_TOKEN

agents: []                       # one entry per mounted agent (may be empty)
```

### Invariants

- `agents: []` is allowed (server boots admin-only).
- `admin.enabled: false` AND `agents: []` is rejected (the server would be inert).
- Each `agents[*].slug` must match `^[a-z0-9][a-z0-9-]*$` and be unique within
  the server. Defaults to `slugify(name)`.
- `persistence` and `authentication` from the server level are inherited by
  every agent that omits them.

## `agents[*]` entries

```yaml
- slug: classification           # optional; default = slugified `name`
  name: coding-agent-classification
  description: "Phase 1/8"
  version: 0.1.0                 # optional

  persistence:                   # optional override of server-level
    backend: sqlite
    url: sqlite+aiosqlite:///./state/agent-private.db
  authentication:                # optional override of server-level
    type: bearer
    value_env: AGENT_TOKEN

  model:
    provider: openai-compatible  # anthropic | google | vertex | openai-compatible
    model: openrouter/auto:free
    api_key_env: OPENROUTER_API_KEY
    base_url: https://openrouter.ai/api/v1
    temperature: 0.0
    max_output_tokens: 4000

  pattern:
    type: simple                 # simple | react | plan_execute | handoff |
                                 # orchestrate | debate | tree_of_thoughts
    # pattern-specific keys; see `patterns.md`

  prompts:
    system: |                    # OR system_file: path/to/prompt.md
      You are ...

  tools:
    mcp_servers: [...]
    filters: { include: [...], exclude: [...] }

  juicefs:                       # optional; sugar over an mcp-juicefs streamable-http server
    url: ${JUICEFS_MCP_URL}      # full field reference in .agent_docs/juicefs.md
    name: juicefs
    identity: { mode: forwarded_user, forwarded_user_header: X-Forwarded-User }
    default_mount_id: perso-alice
    service_identity: svc-config-a2a
    filters: { include: [], exclude: [] }

  guardrails:
    max_loops: 30
    max_tokens: 200000
    timeout_seconds: 300
    max_depth: 5
    anti_loop:
      enabled: true
      similarity_threshold: 0.92

  confirmations:
    destructive_hint: prompt     # prompt | auto_approve | auto_deny
    per_tool:
      "fs.delete_file": auto_deny

  memory:
    enabled: false
    working: { strategy: sliding_summary, window: 20, summarize_every: 10 }
    long_term:
      store: { backend: sqlite }
      read:  { when: first_turn, scopes: [user, agent], top_k: 5, max_chars: 1500 }
      write: { when: after_terminal, extract_with: llm, scope: infer }

  skills:
    - id: classify
      name: Classify project
      description: Detect project type.
      tags: [coding, classification]
      input_modes: [text]
      output_modes: [text]
      examples: ["specs/tickets/CR-001.md"]

  card:                          # optional per-agent overrides (merged on top of server.card)
    documentation_url: https://...
    icon_url: https://...
    default_input_modes: [text]
    default_output_modes: [text]
    provider: { organization: IBM, url: https://ibm.com }
    capabilities:
      push_notifications: false
      state_transition_history: true
    supports_authenticated_extended_card: false
```

## Pattern blocks

See `.agent_docs/patterns.md` for full per-pattern reference. The relevant
fields land directly on `agents[*].pattern`.

## Environment substitution

`${VAR}` anywhere in a string value is substituted from `os.environ`. Missing
variables are left untouched (so the loader does not crash on first parse).

## Path resolution

Path-typed leaves (listed in `_PATH_KEYS` in `config/loader.py`) are made
absolute against the YAML file's directory. Operational paths
(`jsonl_path`, `credentials_path`) are NOT path-resolved; they stay relative
to the working directory at boot.

## Source of truth

The Pydantic models in `src/config_a2a/config/models.py` are the authoritative
schema; this doc summarises them. Every config model uses `extra="forbid"`,
so typos surface immediately.

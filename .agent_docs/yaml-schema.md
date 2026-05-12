# YAML schema reference

Discriminated by `pattern.type`. The Pydantic models (`src/config_a2a/config/models.py`) are the source of truth; this doc summarises the fields.

## Top-level

| Key              | Type                  | Notes                                         |
| ---------------- | --------------------- | --------------------------------------------- |
| `name`           | string                | Required. Used as service.name in traces and the Agent Card. |
| `version`        | string                | Default `0.1.0`.                              |
| `description`    | string                | Surfaced in the Agent Card.                   |
| `server`         | `ServerConfig`        | `host`, `port`. CLI flags `--host`/`--port` override these. |
| `persistence`    | `PersistenceConfig`   | `backend` (`sqlite`/`postgresql`), `url`, `run_migrations_on_start`. |
| `model`          | `ModelConfig`         | Required.                                     |
| `pattern`        | `PatternConfig`       | Required. Discriminator: `type`.              |
| `prompts`        | `PromptsConfig`       | Either `system` (inline) **or** `system_file` (path). |
| `tools`          | `ToolsConfig`         | `mcp_servers` list + `filters`.               |
| `guardrails`     | `GuardrailsConfig`    | See below.                                    |
| `confirmations`  | `ConfirmationsConfig` | Destructive-hint policy.                      |
| `observability`  | `ObservabilityConfig` | OTel + exporter.                              |
| `skills`         | list of `SkillConfig` | Drives the Agent Card.                        |
| `authentication` | `AuthenticationConfig`| `none` / `bearer` / `api_key`.                |

## `model`

| Provider             | Required fields                                      |
| -------------------- | ---------------------------------------------------- |
| `openai-compatible`  | `model`, `base_url`, `api_key_env`                   |
| `anthropic`          | `model`, `api_key_env`                               |
| `google`             | `model`, `api_key_env`                               |
| `vertex`             | `model`, `project`, `location` (ADC via `gcloud auth application-default login`) |

`extra_headers` is forwarded raw (e.g. OpenRouter's `HTTP-Referer` and `X-Title`). `temperature` / `max_output_tokens` are passed through if set.

## Path resolution

The loader resolves these keys against the YAML directory:

```
system_file
prompt_file
executor_prompt_file
planner_prompt_file
evaluator_prompt_file
generator_prompt_file
agent_ref
jsonl_path
credentials_path
```

User-supplied dict keys (e.g. `confirmations.per_tool.fs.delete_file`) are **not** treated as paths.

## `${ENV}` substitution

Any string leaf gets `${NAME}` expanded from the environment. Unset variables are left as-is so the validation error is informative.

## Per-pattern blocks

```yaml
pattern: { type: simple }

pattern:
  type: react
  max_iterations: 10
  executor_prompt_file: prompts/react.md

pattern:
  type: plan_execute
  max_steps: 20
  max_replans: 3
  planner: { prompt_file: prompts/planner.md, model: claude-opus-4-7 }
  executor: { prompt_file: prompts/executor.md }

pattern:
  type: handoff
  router: { prompt_file: prompts/router.md }
  targets:
    - { name: math, agent_ref: ./math.yaml }
    - name: weather
      a2a_url: https://weather.example.com
      auth: { type: bearer, value_env: WEATHER_TOKEN }

pattern:
  type: orchestrate
  mode: parallel        # sequential | parallel
  agents:
    - { name: rag, a2a_url: http://localhost:9010 }
    - { name: web, a2a_url: http://localhost:9011, input_template: "{{ user_text }}" }
  aggregator: { prompt_file: prompts/aggregator.md }

pattern:
  type: debate
  rounds: 3
  debaters:
    - { name: pro, prompt_file: prompts/pro.md }
    - { name: con, prompt_file: prompts/con.md }
  judge: { prompt_file: prompts/judge.md }

pattern:
  type: tree_of_thoughts
  branches: 4
  depth: 3
  selection: top_k        # top_k | best
  top_k: 2
  evaluator_prompt_file: prompts/eval.md
  generator_prompt_file: prompts/gen.md
```

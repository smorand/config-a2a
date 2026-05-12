# Patterns

All patterns share the `ExecutionContext` (frozen dataclass in `patterns/base.py`) and emit task-state updates via the same SSE channel.

## `simple`

One LLM call. If the model emits `tool_calls`, dispatch them and loop. Bounded by `guardrails.max_loops` and `guardrails.max_tokens`. Destructive tools (MCP `annotations.destructiveHint`) route through `guardrails/confirmations.py` and emit a `TASK_STATE_INPUT_REQUIRED` event with `metadata.kind="confirm_tool"`. Resume by re-sending a message with the same `taskId`.

## `react`

Same loop, plus an explicit ReAct executor prompt and an anti-loop check (`guardrails.anti_loop`). The anti-loop detector is token-prefix based for the MVP; a cosine-similarity layer is documented as future work and would plug in here.

## `plan_execute`

Three phases:

1. **Plan** — planner LLM returns a JSON object `{"steps": [{"id": ..., "instruction": ...}]}`. Retried up to `max_replans` times on invalid JSON.
2. **Execute** — each step gets its own LLM call (with the same provider; per-step override is on the roadmap).
3. **Synthesise** — a final call combines step outputs into the user-facing reply.

## `handoff`

Router LLM picks one of N targets, then either:
- instantiates a local sub-agent in-process via `agent_ref` (a YAML path; `guardrails.max_depth` caps nesting), or
- calls a remote agent over A2A via `a2a_url` (uses `a2a.client.send_text`).

Sub-agents inherit the parent's provider, MCP registry, and task store.

## `orchestrate`

Parallel or sequential fan-out to remote A2A agents (`agents[].a2a_url`). The aggregator LLM call combines replies into one answer. `input_template` lets you re-shape the message per-target.

## `debate`

N debaters speak in parallel each round (rounds are sequential, debaters within a round run via `asyncio.gather`). The judge LLM produces a final verdict.

## `tree_of_thoughts`

Repeats `depth` times:
1. Each frontier thought spawns `branches` children (parallel).
2. Each child is scored 0–10 by the evaluator LLM.
3. Keep top-`top_k` (or just the best) for the next depth level.

A final synthesis pass turns the winning path into the user-facing answer.

## Anti-patterns to avoid

* Don't set `max_loops` very high without a hard `timeout_seconds` — a misbehaving model can burn tokens fast.
* Don't disable confirmations on destructive MCP tools in shared environments.
* Don't run `orchestrate` against agents on the same process (they share the asyncio loop — use `agent_ref` instead).
* Don't put secrets in YAML literals — use `${ENV}` substitution or `api_key_env`.

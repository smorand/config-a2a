# Patterns

All patterns share the `ExecutionContext` (frozen dataclass in `patterns/base.py`) and emit task-state updates via the same SSE channel.

## Destructive-tool confirmation (shared)

`simple` and `react` share the destructive-tool confirmation logic in `patterns/confirm.py` so they never drift. For every tool call annotated `destructiveHint`, `decide_tool` consults `confirmations.policy_for` (which honours `confirmations.destructive_hint` and the `confirmations.per_tool` overrides):

* `auto_approve` runs the tool immediately,
* `auto_deny` refuses without running (a deny message goes back to the model),
* `prompt` suspends the task with `TASK_STATE_INPUT_REQUIRED` (`metadata.kind="confirm_tool"`) and persists the pending call.

Resume by re-sending a message with the same `taskId`: `resume_pending` re-executes the persisted call on an approval (`yes`/`approve`) and continues the loop with the tool result in context; anything else cancels cleanly. Non-destructive tools always run immediately.

## `simple`

One LLM call. If the model emits `tool_calls`, dispatch them and loop, applying the shared confirmation flow above. Bounded by `guardrails.max_loops` and `guardrails.max_tokens`.

## `react`

Same loop and the same shared confirmation flow, plus an explicit ReAct executor prompt and an anti-loop check (`guardrails.anti_loop`). The anti-loop detector is token-prefix based for the MVP; a cosine-similarity layer is documented as future work and would plug in here.

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

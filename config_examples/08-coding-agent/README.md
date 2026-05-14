# 08-coding-agent — port of `agent-coding` to config-a2a

This example replicates the 8-phase ticket-to-PR pipeline from
`/Users/sebastien/Documents/Projects/perso/agent-coding/` by running each phase
as its own `simple` A2A server (different model, temperature, system prompt) and
chaining them with a top-level `orchestrate` (`mode: sequential`) manifest.

## Topology

```
+------------------- coding-agent.yaml (orchestrate, port 9100) ------------------+
|                                                                                 |
|  9101 classification  ->  9102 dor  ->  9103 comprehension  ->  9104 planning   |
|       (qwen3.5-9b)       (qwen3.5-9b)    (qwen3.6-flash)        (qwen3.6-27b)   |
|                                                                                 |
|  ->  9105 e2e_writing  ->  9106 implementation  ->  9107 review  ->  9108 PR    |
|       (qwen3.6-27b)        (qwen3.6-27b)             (qwen3.6-27b)    (27b)     |
+---------------------------------------------------------------------------------+
```

Inter-phase data flow happens via filesystem artifacts under
`.agent_work/<ticket_id>/` (the same convention `agent-coding` uses) because
`orchestrate sequential` broadcasts the same `user_text` to every agent — it
does NOT pipe phase N's reply into phase N+1.

## Booting

```bash
export OPENROUTER_API_KEY=sk-or-...

for n in 01-classification 02-dor 03-comprehension 04-planning \
         05-e2e-writing 06-implementation 07-review 08-pr-creation; do
  uv run agent --config config_examples/08-coding-agent/phases/$n.yaml &
done

uv run agent --config config_examples/08-coding-agent/coding-agent.yaml
```

Each phase needs an MCP filesystem and MCP git server; phase 8 additionally
needs an MCP wrapper around `gh` (which does not ship with this repo — see
*Missing features* below).

## Sending a ticket

```bash
curl -N -X POST http://localhost:9100/a2a/v1/messages \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":[{"type":"text","text":"specs/tickets/CR-001.md"}]}]}'
```

The ticket path (or ticket id) is sent verbatim; phases agree by convention to
read `.agent_work/<ticket_id>/<predecessor>.{json,md}` and to write their own
artifact before returning.

## Missing features vs. agent-coding

The plan file
(`~/.claude/plans/i-want-you-to-tidy-dove.md`) lists 15 gaps in detail. The
critical structural ones are:

1. **No pipeline data flow** — `orchestrate sequential` broadcasts `user_text`,
   it does not chain phase outputs. We tunnel state through
   `.agent_work/<ticket_id>/`.
2. **No short-circuit on phase failure** — when DoR (phase 2) decides the
   ticket is incomplete, phases 3 through 8 still run. `agent-coding` exits 1.
3. **Aggregator is forced LLM synthesis** — the orchestrator must run a final
   LLM call across all 8 replies. We instruct it to return phase 8's PR URL
   verbatim, but the cost and risk are real.
4. **No per-phase / per-path anti-cheat** — `agent-coding` blocks writes to
   `tests/test_*.py` during phase 6 via `AntiCheatGuard`. Closest workaround
   here is system-prompt discipline.
5. **No multi-approach implementation loop** — `max_iterations_per_approach`,
   `min_approaches`, `stagnation_threshold`, `wall_clock_seconds` are not
   expressible. Only a flat `guardrails.max_loops`.
6. **No reviewer one-shot re-run gate** — `REQUEST_CHANGES` cannot trigger a
   bounded rewind of phases 6+7.
7. **No phase-level state checkpoint** — config-a2a persists A2A task
   envelopes, not pipeline progress. A crashed orchestrator restarts from
   phase 1.
8. **No MCP wrappers for gh / make / pytest / pyright LSP** — phase 6 (test
   loop) and phase 8 (PR creation) are blocked until these exist or until
   config-a2a accepts a non-MCP tool surface.
9. **`guardrails.timeout_seconds` is capped at 3600 (1 h).** `agent-coding`
   defaults the implementation loop to 7200 s (2 h) and the overall pipeline
   has no hard cap. The orchestrator and the implementation phase therefore
   run at the config-a2a maximum (1 h each), not the agent-coding values.

In short: **config-a2a today can drive the same conceptual 8-phase walk, with
per-phase model tiering, but loses pipeline data flow, failure short-circuit,
anti-cheat enforcement, the multi-approach loop, the reviewer re-run gate, the
mid-run checkpoint, and the native gh / make / LSP tools.**

## Files

```
coding-agent.yaml                # orchestrator (port 9100)
phases/
  01-classification.yaml         # port 9101, qwen3.5-9b, temp 0.0
  02-dor.yaml                    # port 9102, qwen3.5-9b, temp 0.0
  03-comprehension.yaml          # port 9103, qwen3.6-flash, temp 0.2
  04-planning.yaml               # port 9104, qwen3.6-27b, temp 0.1
  05-e2e-writing.yaml            # port 9105, qwen3.6-27b, temp 0.1
  06-implementation.yaml         # port 9106, qwen3.6-27b, temp 0.2
  07-review.yaml                 # port 9107, qwen3.6-27b, temp 0.0
  08-pr-creation.yaml            # port 9108, qwen3.6-27b, temp 0.1
prompts/
  orchestrator.md
  01-classification.md
  02-dor.md
  03-comprehension.md
  04-planning.md
  05-e2e-writing.md
  06-implementation.md
  07-review.md
  08-pr-creation.md
```

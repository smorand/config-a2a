# 08-coding-agent — port of `agent-coding` to config-a2a

Replicates the 8-phase ticket-to-PR pipeline from
`/Users/sebastien/Documents/Projects/perso/agent-coding/` as a single
multi-agent config-a2a server. Each phase is a `simple` agent with its own
model and prompt; a 9th `orchestrate` agent chains them sequentially.

## Topology

One FastAPI process on port `9100`. Nine agents, all reachable under
`/agents/<slug>`:

```
                   /agents/orchestrator   <-- pipeline entry point
                            |
   sequential orchestrate broadcasts the same user_text to:
                            |
   /agents/classification   (qwen3.5-9b,    temp 0.0)
   /agents/dor              (qwen3.5-9b,    temp 0.0)
   /agents/comprehension    (qwen3.6-flash, temp 0.2)
   /agents/planning         (qwen3.6-27b,   temp 0.1)
   /agents/e2e-writing      (qwen3.6-27b,   temp 0.1)
   /agents/implementation   (qwen3.6-27b,   temp 0.2)
   /agents/review           (qwen3.6-27b,   temp 0.0)
   /agents/pr-creation      (qwen3.6-27b,   temp 0.1)
```

The orchestrator calls the eight phase agents via HTTP loopback to the same
process (`http://localhost:9100/agents/<slug>`).

## Inter-phase data flow

`orchestrate sequential` **broadcasts** the same `user_text` (the ticket id or
path) to every phase agent — it does NOT pipe phase N's reply into phase N+1.
The phases therefore communicate by reading and writing artifact files under
`.agent_work/<ticket_id>/`, the same convention `agent-coding` uses.

Each phase prompt instructs its agent to:

1. Read the predecessor artifacts (`fs.read_text_file`).
2. Do its work, optionally calling the model.
3. Write its own artifact (`fs.write_file`) and commit (`git.commit` with
   message `agent-code: phase <name>`).

## Booting

```bash
export OPENROUTER_API_KEY=sk-or-...
uv run agent --config config_examples/08-coding-agent/agents.yaml
```

One command boots all 9 agents on `http://0.0.0.0:9100`. Each phase requires
the MCP filesystem and (most of them) the MCP git server; phase 8 requires
an MCP wrapper around `gh` that does **not** ship with this repo (see
*Missing features* below).

## Sending a ticket

```bash
curl -N -X POST http://localhost:9100/agents/orchestrator/message:send \
  -H 'content-type: application/json' \
  -d '{"message":{"messageId":"t1","role":"ROLE_USER","parts":[{"text":"specs/tickets/CR-001.md"}]}}'
```

The orchestrator runs the 8 phases sequentially and returns phase 8's PR URL.

## YAML structure

`agents.yaml` uses a top-level `_anchors:` block (stripped by the loader)
to factor out the OpenRouter base, the per-phase model variants, the MCP
server entries, the tool bundles, and the confirmations bundles. Each
agent then declares only the fields that differ.

## Missing features vs. agent-coding

Documented in `specs/BACKLOG.md`-adjacent design notes. Critical gaps:

1. **No pipeline data flow** — `orchestrate sequential` broadcasts
   `user_text`; phases must use the filesystem bus.
2. **No short-circuit on phase failure** — if DoR (phase 2) fails, phases
   3–8 still run. The prompts include guards that read `dor.json` and
   short-circuit themselves.
3. **Aggregator forces a final LLM synthesis** — the orchestrator must
   return phase 8's PR URL verbatim, told to do so in `prompts/orchestrator.md`.
4. **No per-phase / per-path anti-cheat** — agent-coding's `AntiCheatGuard`
   blocking writes to `tests/e2e/*` during phase 6 is prompt discipline
   only here.
5. **No multi-approach implementation loop** — only a flat
   `guardrails.max_loops`.
6. **No reviewer one-shot re-run gate** — `REQUEST_CHANGES` cannot rewind
   phases 6+7.
7. **No phase-level checkpoint/resume** — a crashed orchestrator restarts
   from phase 1.
8. **No `gh` MCP wrapper ships** — phase 8 (PR creation) is therefore
   functionally limited; we prepare the PR body to disk and rely on the
   operator to run `gh pr create --body-file ...`.
9. **`guardrails.timeout_seconds` is capped at 3600s** — agent-coding
   allows 2h for the implementation loop and 4h for the full pipeline.

In short: config-a2a today can drive the same conceptual 8-phase walk, with
per-phase model tiering, but loses pipeline data flow, failure
short-circuit, anti-cheat enforcement, the multi-approach loop, the
reviewer re-run gate, and the mid-run checkpoint.

## Files

```
agents.yaml                # single multi-agent server (9 agents, ports = one)
prompts/
  orchestrator.md          # aggregator: return phase 8's PR URL verbatim
  01-classification.md
  02-dor.md
  03-comprehension.md
  04-planning.md
  05-e2e-writing.md
  06-implementation.md
  07-review.md
  08-pr-creation.md
README.md                  # this file
```

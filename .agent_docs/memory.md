# Memory subsystem

Opt-in via `memory.enabled: true`. The runtime owns the WHEN (read/manage/write hooks); a `MemoryStore` owns the WHERE (rows in SQLite or in-memory dict, with mem0/Letta/Zep as future drop-in backends).

## Why hook-driven and not pure tool-mode

State of the art consensus in 2026 (mem0, LangMem, SurePrompts comparisons, Lawson TDS guide): production-grade memory does not delegate hygiene to the LLM. The runtime fixes the moments at which memory is touched; an LLM call may be used to *distill* what to write, but never to decide *whether* to write. This avoids the four documented failure modes — summarisation drift, confirmation loops, over-generalisation, memory blindness.

We keep `expose_as_tools: false` as the default. The opt-in Letta-style "agent self-edits memory" mode is reserved for a future iteration.

## Three hooks

| Hook                       | When                                  | What                                                              |
| -------------------------- | ------------------------------------- | ----------------------------------------------------------------- |
| **read**                   | Pre-pattern, in `AgentRuntime.run_message` | `store.search(user_text, scopes, top_k)` → injected into system prompt as a `Relevant memory from past interactions:` block. |
| **manage** (working memory)| Inside every `call_llm`               | If `messages` exceeds `working.window`, replace older non-head messages with a `[memory:summary] …` system message produced by a cheap LLM call. |
| **write**                  | Post-pattern, on `TASK_STATE_COMPLETED` | LLM-based extractor produces 0..N facts as strict JSON; runtime writes them with the configured scope (`user`/`agent`/`infer`). |

## YAML

```yaml
memory:
  enabled: true
  working:
    strategy: sliding_summary       # none | sliding_summary
    window: 12                      # keep last N messages verbatim
    summarize_every: 6              # ignored at the moment; window is the live threshold
    summary_prompt: "..."           # optional override
  long_term:
    store:
      backend: sqlite               # sqlite | in_memory
      url: sqlite+aiosqlite:///...  # defaults to persistence.url
    read:
      when: first_turn              # none | first_turn | every_turn
      scopes: [user, agent]
      top_k: 5
      max_chars: 1500
    write:
      when: after_terminal          # off | after_terminal
      extract_with: llm             # llm | none
      scope: infer                  # user | agent | infer
  expose_as_tools: false            # reserved
```

## Scopes

* `user` — durable facts about the human (preferences, identity). Optionally tagged with `user_id` for multi-tenant isolation (search filters by `user_id` when supplied).
* `agent` — lessons the agent learned (gotchas, successful tactics). Scoped per `agent_name`, never per user.

`scope: infer` lets the extractor pick; `scope: user|agent` forces every harvested fact into that bucket regardless of the model's suggestion.

## Retrieval

For Iter 12 we ship a **token-overlap** score (`overlap_score` in `memory/store.py`): the candidate record's lower-cased alphanumeric tokens are intersected with the query's; the score is the recall of the query. Tokens shorter than 3 chars are dropped. This is enough for unit tests and the common "did this fact come up" lookup, and works on both SQLite and Postgres without extensions.

Vector retrieval is a planned upgrade — drop in an embedding provider, store a vector alongside `memory_records.text`, switch the rank to cosine. The interface in `MemoryStore.search` is unchanged.

## Extractor

`memory/extractor.py` runs one chat completion against the agent's provider with a strict system prompt and `temperature=0`. The output is parsed as:

```json
{"facts":[{"text":"...","scope":"user|agent","tags":["..."]}]}
```

Robustness:
- code-fence stripping (`_strip_fences`)
- embedded-JSON recovery (`_find_json_object`) — finds the first balanced `{...}` substring so free models that wrap JSON in prose still parse cleanly
- empty list on any failure (never raises)

The extractor uses the agent's main provider in Iter 12. A future iteration will let you point it at a cheaper/faster sub-model via `long_term.write.model:`.

## Failure modes & mitigations

| Risk                       | Mitigation in Iter 12                                                              |
| -------------------------- | ---------------------------------------------------------------------------------- |
| Summarisation drift        | Keep working summary as a single `[memory:summary]` system message that grows additively; raw turns are still in the `messages` table for inspection. Drift-detection from raw→summary linking is future work. |
| Memory blindness           | Token-overlap search is recall-biased; queries with no matching tokens correctly return nothing instead of silently injecting garbage. |
| Multi-tenant leak          | `user`-scope records carry an optional `user_id`; `MemoryStore.search` filters by it when the runtime knows the user. Without authentication, the `user_id` is unset and all records in scope=user are visible — document this when running multi-tenant. |
| Contradiction oscillation  | Not yet handled — the store appends; reconciliation between conflicting facts is future work. Document by adopting `updated_at` linked records or by passing the existing memory to the extractor and asking it to UPDATE. |

## End-to-end demo

```bash
export OPENROUTER_API_KEY=...
uv run agent --config config_examples/08-memory/agent.yaml --port 9008
```

Terminal 2:

```bash
# Turn 1 — establish a fact.
curl -X POST http://localhost:9008/message:send \
  -H 'Content-Type: application/json' \
  -d '{"message":{"messageId":"t1","role":"ROLE_USER","contextId":"ctx-A","parts":[{"text":"Remember: my favourite city is Kenitra."}]}}'

# Verify it landed.
sqlite3 state/memory-assistant.db 'SELECT scope, text FROM memory_records;'
# user|User's favourite city is Kenitra

# Turn 2 — new context, runtime should still recall.
curl -X POST http://localhost:9008/message:send \
  -H 'Content-Type: application/json' \
  -d '{"message":{"messageId":"t2","role":"ROLE_USER","contextId":"ctx-B","parts":[{"text":"What is my favourite city?"}]}}'
# → "Kenitra"
```

The headline e2e test `tests/e2e/test_memory_recall.py::test_memory_carries_a_user_fact_across_two_turns` automates this against real OpenRouter (free `openai/gpt-oss-120b:free`).

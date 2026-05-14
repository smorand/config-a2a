You are the **implementation** phase (6/8) of the coding pipeline.

# Short-circuit guard

Read `.agent_work/<ticket_id>/dor.json`. If `verdict != "READY"`, write
`.agent_work/<ticket_id>/implementation.json` with `{"skipped": true}` and reply
with that JSON. Stop.

# Anti-cheat rules (PROMPT DISCIPLINE — NOT ENFORCED BY RUNTIME)

- **NEVER** modify, create, or delete any file under `tests/e2e/`. Those are
  the acceptance tests written by phase 5. The runtime does not block this.
  If you write to a `tests/e2e/*.py` file the run is invalid.
- Do not weaken assertions, do not skip tests, do not mark tests as expected
  failures.

# Task

Make the E2E tests pass.

1. Read the ticket, `comprehension.json`, `plan.md`, `todo.md`, `e2e.json`.
2. Loop:
   a. Run `pytest -m e2e -q` via the MCP `shell.run` tool (or, if no shell MCP
      is available, infer failures from `make.test`).
   b. If all tests pass, commit (`git.commit` message
      `agent-code: phase implementation`) and break.
   c. Otherwise, edit production source files only (not `tests/e2e/*`) and try
      again. Cap at the runtime-imposed `guardrails.max_loops` iterations.
3. Write `.agent_work/<ticket_id>/implementation.json`:
   ```json
   {"approach": "<one-line description>",
    "iterations": <int>,
    "all_tests_passing": <bool>,
    "commit_sha": "<sha>",
    "files_modified": ["..."]}
   ```
4. Reply with that JSON.

# Known gaps vs. agent-coding

- No multi-approach exploration: a single approach is attempted until
  `guardrails.max_loops` is exhausted.
- No stagnation detection: identical iterations are not penalised.
- No wall-clock budget separate from `guardrails.timeout_seconds`.
- No MCP wrapper for `pytest`, `make`, or pyright LSP ships with this repo;
  this phase assumes such a wrapper exists at runtime.

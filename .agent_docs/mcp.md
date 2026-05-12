# MCP integration

Three transports are supported. The unified entry point is `mcp/client.McpRegistry`.

## Transports

| Transport         | Module                          | Notes                                              |
| ----------------- | ------------------------------- | -------------------------------------------------- |
| `stdio`           | `mcp/stdio.py`                  | Env scrubbed (only PATH + caller-supplied env), 10 s discovery timeout. |
| `streamable-http` | `mcp/streamable_http.py`        | Current MCP spec (2025-03-26).                     |
| `sse`             | `mcp/sse.py`                    | Deprecated; emits `DeprecationWarning` at boot.    |

## Discovery

`McpRegistry.discover(servers, filters)` opens each server in turn, calls `session.list_tools()`, and registers each tool under a qualified name `<server>.<tool>`. Stdio sessions are opened *per call*; we don't keep long-lived child processes around.

## Filters

```yaml
tools:
  filters:
    include: ["fs.*", "web.search"]    # fnmatch globs
    exclude: ["*.delete*"]
```

Exclude wins over include. Without `include`, everything that survives `exclude` is registered.

## Destructive-hint flow

When the LLM emits a `tool_call` for a tool whose MCP `annotations.destructiveHint=true`, `guardrails/confirmations.py` decides:

- `auto_approve` → run immediately,
- `auto_deny` → return a deny message without running,
- `prompt` (default) → suspend the task with `TASK_STATE_INPUT_REQUIRED`. The next user message (same `taskId`) resumes; `yes`/`approve` runs the tool, anything else cancels.

## Caveats

- We don't yet keep persistent stdio sessions — every `call_tool` spawns a new child. Fine for safe tools; expensive for very chatty ones. A long-lived-session optimisation is on the backlog.
- The official mcp Python SDK is still pre-1.0 stable. If a server returns non-text content (images, blobs), `mcp/stdio.py` will currently drop it. The flattener only joins `text` parts.

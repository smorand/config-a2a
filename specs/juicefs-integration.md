# Spec: native JuiceFS integration

Status: implemented on branch `feat/juicefs-native`.

## Goal

Give a config-a2a agent first-class file tools backed by a JuiceFS volume,
exposed through the `mcp-juicefs` server, with **per-user** access control. The
agent author writes one compact `juicefs:` block instead of hand-wiring an MCP
server plus identity propagation.

config-a2a stays a pure MCP-over-HTTP consumer: no `libjfs` / JuiceFS SDK
dependency, so the runtime remains cross-platform. Volumes are provisioned
out of band (via `admin.create_project` on mcp-juicefs); config-a2a never
provisions.

## Contract with mcp-juicefs (consumed, not modified)

* Streamable-HTTP MCP server on `/mcp`.
* Identity per request: header `X-Forwarded-User: <person>` (debug mode, trusted
  network) or a Bearer JWT (production). v1 here uses `X-Forwarded-User`.
* Every `fs.*` tool takes an explicit `mount_id`. A user may own several volumes.
* `fs.list_allowed_roots` returns the volumes the current identity can access.
* The server ACL (`require_member`) returns `ERR_FORBIDDEN` on a wrong
  `mount_id`, so letting the model choose the volume is safe as long as the
  forwarded identity is correct.

See `mcp-juicefs/.agent_docs/integration.md`.

## Design decisions

### Identity = `X-Forwarded-User` (v1)

config-a2a propagates the **end user** identity per request. It is captured at
the A2A boundary by an ASGI middleware (`IdentityCaptureMiddleware`) reading a
trusted header (default `X-Forwarded-User`), stored in a `ContextVar`
(`config_a2a.identity`), and re-emitted on the outbound MCP call. A future
switch to a re-signed JWT changes only the capture and emit steps, not the
propagation seam.

For tool **discovery** at load time there is no end user in context, so a
configurable **service identity** is forwarded instead, letting `list_tools`
pass the mcp-juicefs auth middleware.

### `mount_id` = explicit, model-chosen

`mount_id` is never injected or hidden by force. Three mechanisms help the model
pick the right one:

1. `fs.list_allowed_roots` (provided by mcp-juicefs) lists the user's volumes.
2. A prompt convention, appended automatically when a `juicefs:` block is
   present (see `juicefs_prompt_suffix`).
3. An optional `default_mount_id` surfaced as the "current project". It can come
   from the agent YAML **or** from A2A message metadata (`mount_id`) injected by
   another UI / API, so the active volume can follow a conversation without
   editing YAML. The model stays free to switch.

Safety net: a wrong `mount_id` yields `ERR_FORBIDDEN` from mcp-juicefs.

### Provisioning is out of band

Volumes are pre-created via `admin.create_project` on mcp-juicefs. The
config-a2a runtime does not provision.

## Implementation map

| Concern | Where |
|---|---|
| Identity ContextVar + capture middleware | `src/config_a2a/identity.py` |
| `juicefs:` model | `src/config_a2a/config/juicefs.py` (`JuiceFSConfig`) |
| `juicefs:` field on agent + desugaring validator | `src/config_a2a/config/models.py` |
| Forward-ref resolution (`model_rebuild`) | `src/config_a2a/config/__init__.py` |
| Desugaring + prompt fragment | `src/config_a2a/juicefs/binding.py` |
| Inbound capture wiring | `src/config_a2a/api.py` (`IdentityCaptureMiddleware`) |
| Outbound header injection | `src/config_a2a/mcp/streamable_http.py` |
| `default_mount_id` / per-message `mount_id` -> prompt | `src/config_a2a/runtime.py`, `src/config_a2a/a2a/routes.py` |
| Example | `config_examples/09-juicefs/` |
| Tests | `tests/unit/test_juicefs.py` |

## Flow

1. Load: the `juicefs:` block is desugared (in the `AgentConfig` validator) into
   an `McpStreamableHttpServer` with `forward_identity=True`, appended to
   `tools.mcp_servers`. Discovery forwards the service identity.
2. Request: `IdentityCaptureMiddleware` binds `X-Forwarded-User` to the
   ContextVar. The route extracts an optional per-message `mount_id` from message
   metadata. `run_message` appends the JuiceFS convention plus the effective
   `mount_id` (per-message override, else `default_mount_id`) to the system
   prompt.
3. Tool call: `streamable_http.call_tool` injects the bound end user into
   `X-Forwarded-User` on the outbound call to mcp-juicefs.

## Open questions / future work

* JWT mode: capture a re-signed token and forward it instead of
  `X-Forwarded-User` (only the capture/emit steps change).
* Per-server inbound header: today one inbound header name is resolved per
  process (first juicefs agent wins); multi-tenant servers mixing header names
  would need per-route capture.
* Surfacing `fs.list_allowed_roots` results into the prompt automatically (today
  the model calls it on demand).

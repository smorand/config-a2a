# JuiceFS (`juicefs:` block)

Native, ergonomic config for giving an agent file tools backed by a JuiceFS
volume through the `mcp-juicefs` server, with per-user access control. It is
sugar over a `streamable-http` MCP server plus per-request identity forwarding.

Design rationale lives in `specs/juicefs-integration.md`. This file is the
operational reference.

## YAML

```yaml
agents:
  - slug: files
    name: juicefs-assistant
    model: { provider: openai-compatible, model: openrouter/auto:free, api_key_env: OPENROUTER_API_KEY, base_url: https://openrouter.ai/api/v1 }
    pattern: { type: react, max_iterations: 8 }
    prompts: { system_file: prompts/system.md }
    juicefs:
      url: ${JUICEFS_MCP_URL}            # mcp-juicefs streamable-http endpoint, e.g. http://host:8000/mcp
      name: juicefs                      # MCP server name; tools surface as juicefs.fs.*
      identity:
        mode: forwarded_user             # v1: trusted network, X-Forwarded-User
        forwarded_user_header: X-Forwarded-User
      default_mount_id: ${JUICEFS_DEFAULT_MOUNT_ID}   # optional "current project"
      service_identity: ${JUICEFS_SERVICE_IDENTITY}   # identity used for tool discovery
      filters: { include: [], exclude: [] }           # optional, applied like tools.filters
```

### Fields

| Field | Default | Meaning |
|---|---|---|
| `url` | required | mcp-juicefs streamable-HTTP endpoint. |
| `name` | `juicefs` | MCP server name; tools are `name.fs.*`. |
| `identity.mode` | `forwarded_user` | v1 only mode (trusted network). |
| `identity.forwarded_user_header` | `X-Forwarded-User` | Header used both to read the inbound A2A request and to re-emit the outbound MCP call. |
| `default_mount_id` | `null` | Volume surfaced to the model as its current project. |
| `service_identity` | `null` | Identity forwarded during tool discovery (no end user in context). |
| `filters` | empty | Optional include/exclude tool filters. |

## What desugaring produces

At load time the `juicefs:` block compiles into an entry appended to
`tools.mcp_servers`:

```yaml
- name: juicefs
  transport: streamable-http
  url: <url>
  forward_identity: true
  identity_header: <identity.forwarded_user_header>
  service_identity: <service_identity>
```

The desugaring runs in the `AgentConfig` validator, so it also applies to agents
loaded through the admin `POST /admin/agents` surface. It is idempotent (skips
when a server with the same name already exists).

## Identity propagation

1. `IdentityCaptureMiddleware` (installed in `create_app`) reads the inbound
   header and binds it to a `ContextVar` (`config_a2a.identity.current_user`).
2. `streamable_http.call_tool` injects that user into `identity_header` on the
   outbound call. With no user bound (discovery), the `service_identity` is used
   instead.

The middleware reads the header name resolved from the first juicefs agent in
the process (default `X-Forwarded-User`). The caller is responsible for having
authenticated the user upstream; use only on a trusted/private network in v1.

## Choosing the `mount_id`

`mount_id` is an explicit argument of every `fs.*` tool (a user may have several
volumes). The model picks it; three aids:

* `fs.list_allowed_roots` lists the volumes the current identity can access.
* A convention paragraph is auto-appended to the system prompt whenever a
  `juicefs:` block is present (`juicefs_prompt_suffix`).
* `default_mount_id` is surfaced as the current project. It can come from YAML
  **or** per message via A2A message metadata:

  ```json
  {"message": {"messageId": "m1", "role": "ROLE_USER",
               "metadata": {"mount_id": "projet-marketing"},
               "parts": [{"text": "list my files"}]}}
  ```

  The per-message value overrides the YAML default for that turn. This lets
  another UI / API drive the active volume per conversation without editing YAML.

Safety net: a wrong `mount_id` returns `ERR_FORBIDDEN` from mcp-juicefs, so the
model can never touch a volume outside the authenticated person.

## Running the example

```bash
export OPENROUTER_API_KEY=...
export JUICEFS_MCP_URL=http://localhost:8000/mcp
export JUICEFS_DEFAULT_MOUNT_ID=perso-alice      # optional
export JUICEFS_SERVICE_IDENTITY=svc-config-a2a   # needed for discovery
uv run agent --config config_examples/09-juicefs/agents.yaml --port 9009

curl -X POST http://localhost:9009/agents/files/message:send \
  -H 'Content-Type: application/json' \
  -H 'X-Forwarded-User: alice' \
  -d '{"message":{"messageId":"q1","role":"ROLE_USER","parts":[{"text":"List my volumes"}]}}'
```

Volumes must be pre-provisioned on mcp-juicefs (out of band); config-a2a does
not provision.

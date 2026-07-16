# JuiceFS (`juicefs:` block)

Native, ergonomic config for giving an agent file tools backed by a JuiceFS
volume through the `mcp-juicefs` server, with per-user access control. It is
sugar over a `streamable-http` MCP server plus per-request identity forwarding.

Design rationale lives in `specs/juicefs-integration.md`. This file is the
operational reference.

## YAML

```yaml
identity:                              # ServerConfig level: JWT verification (the only mode)
  public_key_path: .keys/jwt.pub       # RS256 verifier public key (required)
  header: X-Forwarded-Authorization    # inbound Bearer JWT header (default)
  issuer: web-a2a                       # pinned issuer (default)
  claim: email                          # identity claim bound as the end user (default)
  service_token_path: .keys/service.jwt # Bearer presented during tool discovery

agents:
  - slug: files
    name: juicefs-assistant
    model: { provider: openai-compatible, model: openrouter/auto:free, api_key_env: OPENROUTER_API_KEY, base_url: https://openrouter.ai/api/v1 }
    pattern: { type: react, max_iterations: 8 }
    prompts: { system_file: prompts/system.md }
    juicefs:
      url: ${JUICEFS_MCP_URL}            # mcp-juicefs streamable-http endpoint, e.g. http://host:8000/mcp
      name: juicefs                      # MCP server name; tools surface as juicefs.fs.*
      default_mount_id: ${JUICEFS_DEFAULT_MOUNT_ID}   # optional "current project"
      filters: { include: [], exclude: [] }           # optional, merged into tools.filters
```

### Fields

| Field | Default | Meaning |
|---|---|---|
| `url` | required | mcp-juicefs streamable-HTTP endpoint. |
| `name` | `juicefs` | MCP server name; tools are `name.fs.*`. |
| `default_mount_id` | `null` | Volume surfaced to the model as its current project. |
| `filters` | empty | Optional include/exclude tool filters, merged (deduplicated union) into `tools.filters`. |

The `juicefs:` block carries no identity settings: identity is entirely
server-wide and JWT-based (the verified Bearer credential is re-forwarded to
mcp-juicefs, and the service token is used for tool discovery).

Server-level (`ServerConfig.identity`, the only end-user identity mechanism):

| Field | Default | Meaning |
|---|---|---|
| `public_key_path` | required | RS256 public key verifying the inbound Bearer JWT. |
| `header` | `X-Forwarded-Authorization` | Inbound (and re-forwarded outbound) Bearer JWT header. |
| `algorithms` | `[RS256]` | Accepted JWT signature algorithms. |
| `issuer` | `web-a2a` | Pinned `iss` claim. |
| `audience` | `null` | Optional `aud` check (off by default). |
| `claim` | `email` | JWT claim bound as the end user. |
| `service_token_path` | `null` | Pre-minted service JWT presented (as `Bearer`) during tool discovery. |

## Tool names: dots are sanitized for the LLM

mcp-juicefs names its tools with dots (`fs.read`), so config-a2a registers them
as `juicefs.fs.read`. LLM providers reject dotted function names
(`^[a-zA-Z0-9_-]+$`), so the dotted name is sanitized to `juicefs_fs_read` only
when talking to the provider, and remapped back on the response. This is a
**generic provider-boundary behavior** (not juicefs-specific); see the
"Tool-name sanitization" section in `.agent_docs/mcp.md`. Internally (registry
dispatch, `confirmations.per_tool: { "juicefs.fs.delete": prompt }`) the dotted
names stay as-is, so your YAML keys remain dotted.

## What desugaring produces

At load time the `juicefs:` block compiles into an entry appended to
`tools.mcp_servers`:

```yaml
- name: juicefs
  transport: streamable-http
  url: <url>
  forward_identity: true
  identity_header: <ServerConfig.identity.header>   # default X-Forwarded-Authorization
  service_credential: "Bearer <contents of ServerConfig.identity.service_token_path>"
```

The desugaring runs in the `AgentConfig` validator, so it also applies to agents
loaded through the admin `POST /admin/agents` surface. It is idempotent (skips
when a server with the same name already exists, and the filter union never
grows on revalidation).

It also folds `juicefs.filters` into the agent-wide `tools.filters` (see
"Tool filters" below).

## Identity: server-wide JWT (the only mechanism)

Identity is a single **server-wide** JWT setting; there is no per-`juicefs`-block
identity and no `forwarded_user` trust mode. The contract: RS256, claim `email`,
`iss == web-a2a`, no audience, inbound on `X-Forwarded-Authorization`.

```yaml
identity:                          # ServerConfig level
  public_key_path: .keys/jwt.pub
  service_token_path: .keys/service.jwt
```

Propagation:

1. `IdentityCaptureMiddleware` (installed in `create_app`) verifies the inbound
   Bearer JWT on `ServerConfig.identity.header`, binds the `email` claim to
   `config_a2a.identity.current_user` and the raw `Bearer <jwt>` to
   `current_credential`. A missing or invalid token yields `401`.
2. `streamable_http.call_tool` re-forwards that same `Bearer <jwt>` on the
   juicefs server's `identity_header`. With no user bound (tool discovery), the
   static `service_credential` (`Bearer <service token>`) is used instead.

When no `identity:` block is configured the middleware is a pass-through and no
end user is bound (anonymous); a juicefs agent then has no caller identity to
forward, so configure `identity:` for any per-user deployment.

## Tool filters

`juicefs.filters` is merged into the agent's `tools.filters` at desugaring as a
deduplicated **union** (`include` and `exclude` lists concatenated, duplicates
dropped, order preserved). `ToolFilters` semantics are unchanged: `include` is
an OR allowlist (empty means "allow all"), `exclude` is an OR denylist. The
merge is idempotent, so revalidating a config never grows the lists. Use either
`tools.filters`, `juicefs.filters`, or both; the result is the same union.

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
uv run agent --config config_examples/09-juicefs/agents-jwt.yaml --port 9019

curl -X POST http://localhost:9019/agents/files/message:send \
  -H 'Content-Type: application/json' \
  -H "X-Forwarded-Authorization: Bearer ${END_USER_JWT}" \
  -d '{"message":{"messageId":"q1","role":"ROLE_USER","parts":[{"text":"List my volumes"}]}}'
```

`END_USER_JWT` is an RS256 token minted by `web-a2a` (claim `email`, `iss`
`web-a2a`), verified here against `identity.public_key_path`.

Volumes must be pre-provisioned on mcp-juicefs (out of band); config-a2a does
not provision.

# End-user identity (`identity:` block)

How config-a2a transmits and verifies the identity of the **end user** (the
person on whose behalf an agent acts), and which providers mint the tokens it
trusts. The JuiceFS-specific propagation notes live in `.agent_docs/juicefs.md`;
this file is the identity contract itself.

## Where config-a2a sits in the chain

```
web-a2a (UI)  ->  config-a2a (A2A gateway)  ->  mcp-fs / mcp-juicefs (downstream MCP filesystem)
```

config-a2a is an A2A gateway between a UI (`web-a2a`) and a downstream MCP
filesystem server. Its job is to **transmit and verify identity in transit**: it
proves who the caller is, then carries that proof onward. It does **not** own the
authorization decision; the downstream server decides what that person may touch
(see "Where authorization actually happens" below).

## Pinned wire contract (shared across the three repos)

RS256; claim `email`; issuer `web-a2a`; header
`X-Forwarded-Authorization: Bearer <jwt>`. `web-a2a` signs with the private key;
config-a2a and mcp-fs each verify with the public key. This contract is shared
verbatim by `web-a2a`, `config-a2a`, and `mcp-fs`; do not change one side alone.

## Inbound: receiving and controlling identity

Identity is **JWT-only**. The earlier `forwarded_user` trust mode was removed;
there is no per-request fallback and no bypass.

`IdentityCaptureMiddleware` (in `src/config_a2a/identity.py`, installed at the
A2A boundary by `create_app`) does the following on every HTTP request, driven by
the server-wide `ServerIdentityConfig`:

1. Reads the bearer on `identity.header` (default `X-Forwarded-Authorization`).
   A value not starting with `Bearer ` yields `401`.
2. **Verifies the signature** with the public key loaded from
   `identity.public_key_path` (real RS256 verification via PyJWT `jwt.decode`,
   not a bare unsigned decode), checking the `issuer` (default `web-a2a`),
   expiry, and the optional `audience` (off when `audience` is `null`).
   `identity.algorithms` defaults to `[RS256]`. An invalid, expired, or
   wrong-issuer token yields `401`.
3. Reads the `email` claim (configurable via `identity.claim`). A token missing
   that claim yields `401`.
4. Binds two context vars for the lifetime of the request: the user via
   `bind_user` (read back with `current_user()`), and the raw `Bearer <jwt>` via
   `bind_credential` (read back with `current_credential()`), so outbound
   transports can re-forward it without threading it through every call.

An inbound `X-Forwarded-User` header is **ignored**: there is no header-based
identity, no fallback, no anonymous bypass when `identity:` is configured. When
no `identity:` block is configured the middleware is a pure pass-through: no end
user is bound and no request is rejected (anonymous deployment).

`ServerIdentityConfig` fields (`src/config_a2a/config/models.py`):

| Field | Default | Meaning |
|---|---|---|
| `public_key_path` | required | RS256 public key verifying the inbound bearer JWT. |
| `header` | `X-Forwarded-Authorization` | Inbound (and re-forwarded outbound) bearer JWT header. |
| `algorithms` | `[RS256]` | Accepted JWT signature algorithms. |
| `issuer` | `web-a2a` | Pinned `iss` claim. |
| `audience` | `null` | Optional `aud` check; off by default. |
| `claim` | `email` | JWT claim bound as the end user. |
| `service_token_path` | `null` | Pre-minted service JWT presented (as `Bearer`) during tool discovery. |

Config shape, from `config_examples/09-juicefs/agents-jwt.yaml`:

```yaml
identity:
  public_key_path: ../../.keys/jwt.pub        # RS256 verifier public key
  header: X-Forwarded-Authorization           # inbound Bearer JWT header
  algorithms: [RS256]
  issuer: web-a2a                              # pinned issuer
  audience: null                               # no audience check
  claim: email                                 # identity claim
  service_token_path: ../../.keys/service.jwt  # Bearer used for tool discovery
```

## Outbound: transmitting identity downstream

The downstream MCP transport (`src/config_a2a/mcp/streamable_http.py`,
`_request_headers`) builds outbound headers when `forward_identity` is true on
the server (the JuiceFS desugaring sets this; see `juicefs/binding.py`,
`compile_juicefs`):

* **On a user tool call**: config-a2a re-forwards the **same** bearer
  (pass-through) on `identity_header`, reading it from `current_credential()`.
  The downstream server verifies that JWT itself; config-a2a does not re-sign or
  rewrite it.
* **On tool discovery** (`list_tools`, no end user in context): config-a2a
  presents a configured **service token** as the bearer. It is loaded from
  `identity.service_token_path`, materialized as `service_credential`
  (`Bearer <service token>`), so discovery passes the downstream auth middleware
  even though no person is on the call.

`_request_headers(server, discovery=...)` picks `server.service_credential` when
`discovery` is true, else `current_credential()`; a `None` value sets no header.

## Providers supported

**Today.** Tokens are minted **upstream by `web-a2a`** (RS256, claim `email`,
issuer `web-a2a`). config-a2a verifies them with a **static public key** read
from `identity.public_key_path`. One key, one issuer.

**Future (cross-repo backlog, not yet implemented).** Verify provider tokens
(Azure AD, Google) by matching their `iss` against the provider's JWKS, via a
`jwks_url` option sitting next to `public_key_path`, using the provider libraries
(PyJWT `PyJWKClient`, google-auth, MSAL or authlib). The verification contract
and the downstream pass-through stay identical; only the **key source** moves
from a static file to a fetched JWKS. Do not document `jwks_url` as available; it
is a backlog item, not a current field.

## Where authorization actually happens

config-a2a proves identity in transit; it does **not** decide access. The ACL
decision, which person may touch which project, is enforced by the **downstream**
server (`mcp-fs`) by matching the verified `email` against a per-project ACL
(platform admin outranks owner, owner outranks member; email compared
caselessly). A wrong target therefore comes back as a downstream authorization
error, never a config-a2a allow. For the ACL model and its precedence rules, see
`mcp-fs` `.agent_docs/authorization.md`.

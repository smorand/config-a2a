# A2A protocol surface

One FastAPI process exposes N agents under `/agents/<slug>`. Routes live in
`src/config_a2a/a2a/routes.py` (per-agent) and `src/config_a2a/api.py`
(server-level + admin). The wire model is in `src/config_a2a/a2a/envelope.py`.

## Server-level routes

```
GET /health                            # liveness probe
GET /.well-known/agent-card.json       # directory of mounted agents
```

The directory payload is a small extension to A2A 1.0; it lists each agent's
slug, URL prefix, name and description:

```json
{
  "name": "coding-agent",
  "description": "8-phase ticket-to-PR pipeline.",
  "version": "0.1.0",
  "agents": [
    {"slug": "classification", "url": "http://host:9100/agents/classification",
     "name": "coding-agent-classification", "description": "Phase 1/8 ..."},
    ...
  ]
}
```

## Per-agent routes

Every agent is mounted under `/agents/<slug>/` and exposes the standard A2A 1.0
surface, prefix-prefixed:

```
GET  /agents/<slug>/.well-known/agent-card.json     # per-agent Agent Card
GET  /agents/<slug>/.well-known/a2a/agent-card      # legacy alias
POST /agents/<slug>/message:send                    # JSON in, drained SSE
POST /agents/<slug>/message:stream                  # SSE response
GET  /agents/<slug>/tasks                           # recent tasks (debug)
GET  /agents/<slug>/tasks/{task_id}
POST /agents/<slug>/tasks/{task_id}:cancel
```

The per-agent Agent Card includes the new `card:` sub-block (camelCase on the
wire): `provider`, `documentationUrl`, `iconUrl`, `defaultInputModes`,
`defaultOutputModes`, `capabilities`, `supportsAuthenticatedExtendedCard`,
plus the existing `name`, `description`, `version`, `url`, `skills`,
`interface`, `securitySchemes` / `security`. Per-agent values override
server-level ones; missing values fall back to the server card.

## Per-agent authentication

`agents[*].authentication.type` (independent of the admin auth). When set to
`bearer` or `api_key`, the per-agent routes return 401 unless the request
carries the configured credential. `/health`, `/.well-known/agent-card.json`,
and the admin surface bypass per-agent auth.

## Admin REST surface

Enabled by default; protected by `admin.authentication` (separate from
per-agent auth). All routes are verb-less:

| Method | URL                                          | Effect |
|--------|----------------------------------------------|--------|
| GET    | `/admin/status`                              | Server rollup (uptime, agents loaded, in-flight) |
| GET    | `/admin/agents`                              | List of `{slug, name, model, pattern, status, uptime_seconds, in_flight, last_task_at}` |
| POST   | `/admin/agents`                              | Atomic load. Body: inline JSON `AgentConfig` OR `{config_path: "..."}`. 201 on success; 4xx on failure with detail; no zombie left behind. |
| GET    | `/admin/agents/{slug}`                       | Resolved config + status |
| GET    | `/admin/agents/{slug}/status`                | Status only (cheap polling) |
| DELETE | `/admin/agents/{slug}`                       | Drain in-flight, close MCP children, unmount routes |
| POST   | `/admin/agents/{slug}/reloads`               | Async reload op; returns 202 with `{id, status: "pending"}` |
| GET    | `/admin/agents/{slug}/reloads/{op_id}`       | `pending → running → completed | failed` |

## Wire model recap

`Message`, `Part`, `Task`, `TaskStatus`, `StatusUpdate`, `SendMessageRequest`
are defined in `src/config_a2a/a2a/envelope.py`. The `message:stream`
endpoint emits SSE events:

- `event: task`             (initial `Task` envelope)
- `event: statusUpdate`     (zero or more, with the final one carrying `final: true`)
- `event: thinking`         (free-text breadcrumbs for the operator UI)

`statusUpdate.metadata` can carry confirmation prompts when the agent enters
`TASK_STATE_INPUT_REQUIRED`; the client resumes by sending a new message with
the same `taskId`.

## Degraded mode

When MCP children die or model 5xx repeatedly mid-life, the agent transitions
to `status: degraded` but stays exposed. Use
`POST /admin/agents/<slug>/reloads` or `DELETE /admin/agents/<slug>` to
remediate.

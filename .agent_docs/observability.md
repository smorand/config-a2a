# Observability

`observability/otel.py` sets up an OpenTelemetry `TracerProvider` once per process. Off by default → set `observability.otel.enabled: true` in YAML.

## Exporters

- `jsonl` (default) → `observability/jsonl_exporter.py` writes one redacted JSON line per span to `observability.otel.jsonl_path`.
- `otlp` → ships to an OTLP HTTP collector at `observability.otel.otlp_endpoint` (default `http://localhost:4318/v1/traces`). Falls back to JSONL if the `opentelemetry-exporter-otlp-proto-http` dependency is missing.

## Redaction

`_REDACT_KEYS` in `observability/jsonl_exporter.py` (case-insensitive): `api_key`, `authorization`, `x-api-key`, `password`, `token`, `cookie`, `set-cookie`, `llm.prompt`, `llm.response`. Add to this set if you introduce new sensitive attrs.

## Conventions

Spans follow the OTel **GenAI semantic conventions (2025)**:

- `gen_ai.system` — provider name (`openai-compatible`, `anthropic`, `google`, `vertex`).
- `gen_ai.request.model` — model id.
- `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens`.
- `gen_ai.server.time_to_first_token` — emitted by streaming providers (future work).

Build attribute dicts with `observability.otel.gen_ai_attributes(...)`.

## Resource attributes

The exporter tags every span with:

- `service.name` (from YAML `observability.otel.service_name` or falls back to `config.name`),
- `service.version` (from `config.version`),
- `agent.pattern` (`simple`, `react`, …).

That's enough cardinality for Datadog/Honeycomb dashboards without leaking PII (we never label by user id).

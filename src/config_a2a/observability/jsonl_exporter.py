"""SpanExporter that writes one JSON object per line, with key redaction."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

_REDACT_KEYS = {
    "api_key",
    "authorization",
    "x-api-key",
    "password",
    "token",
    "cookie",
    "set-cookie",
    "llm.prompt",
    "llm.response",
}


def _redact(key: str, value: Any) -> Any:
    if key.lower() in _REDACT_KEYS:
        return "[REDACTED]"
    return value


class JsonlSpanExporter(SpanExporter):
    """Append every span as a single JSON line to ``path``."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                for span in spans:
                    payload = _serialise(span)
                    handle.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")
        except OSError:
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # noqa: ARG002
        return True


def _serialise(span: ReadableSpan) -> dict[str, Any]:
    context = span.get_span_context() if span.get_span_context() else None
    attrs = {k: _redact(k, v) for k, v in (span.attributes or {}).items()}
    parent_ctx = span.parent
    return {
        "name": span.name,
        "trace_id": f"{context.trace_id:032x}" if context else None,
        "span_id": f"{context.span_id:016x}" if context else None,
        "parent_id": f"{parent_ctx.span_id:016x}" if parent_ctx else None,
        "kind": span.kind.name if hasattr(span.kind, "name") else str(span.kind),
        "status": span.status.status_code.name if hasattr(span.status.status_code, "name") else str(span.status.status_code),
        "start": span.start_time,
        "end": span.end_time,
        "attributes": attrs,
        "resource": dict(span.resource.attributes or {}) if span.resource else {},
        "events": [
            {"name": e.name, "timestamp": e.timestamp, "attributes": dict(e.attributes or {})}
            for e in span.events or []
        ],
        "pid": os.getpid(),
    }

"""Idempotent OpenTelemetry setup, honouring the YAML observability block."""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

from config_a2a.config.models import AgentConfig
from config_a2a.observability.jsonl_exporter import JsonlSpanExporter

log = logging.getLogger(__name__)
_INITIALISED = False


def setup_otel(config: AgentConfig) -> None:
    """Wire a TracerProvider once per process. Safe to call repeatedly."""
    global _INITIALISED  # noqa: PLW0603
    if _INITIALISED or not config.observability.otel.enabled:
        return
    otel = config.observability.otel
    resource = Resource.create(
        {
            "service.name": otel.service_name or config.name,
            "service.version": config.version,
            "agent.pattern": config.pattern.type,
        }
    )
    provider = TracerProvider(resource=resource)
    if otel.exporter == "jsonl":
        # SimpleSpanProcessor (no buffering) — local JSONL is a debug aid; we want
        # spans on disk immediately, no flush dance on shutdown.
        provider.add_span_processor(SimpleSpanProcessor(JsonlSpanExporter(otel.jsonl_path)))
    elif otel.exporter == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            endpoint = otel.otlp_endpoint or "http://localhost:4318/v1/traces"
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        except ImportError:
            log.warning("OTLP exporter not installed; falling back to JSONL")
            provider.add_span_processor(BatchSpanProcessor(JsonlSpanExporter(otel.jsonl_path)))
    trace.set_tracer_provider(provider)
    _INITIALISED = True


def get_tracer(name: str = "config-a2a") -> trace.Tracer:
    return trace.get_tracer(name)


def gen_ai_attributes(*, provider: str, model: str, **kwargs: Any) -> dict[str, Any]:
    """Build a dict of OTel GenAI semconv 2025 attributes."""
    base: dict[str, Any] = {
        "gen_ai.system": provider,
        "gen_ai.request.model": model,
    }
    for key, value in kwargs.items():
        if value is None:
            continue
        base[f"gen_ai.{key}"] = value
    return base

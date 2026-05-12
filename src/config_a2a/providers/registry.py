"""Provider factory: build the right `LlmProvider` for a `ModelConfig`."""

from __future__ import annotations

from config_a2a.config.models import ModelConfig
from config_a2a.providers.anthropic import build_anthropic
from config_a2a.providers.base import LlmProvider, ProviderError
from config_a2a.providers.google import build_google
from config_a2a.providers.openai_compat import build_openai_compatible
from config_a2a.providers.vertex import build_vertex


def build_provider(model: ModelConfig) -> LlmProvider:
    if model.provider == "openai-compatible":
        if not model.base_url:
            raise ProviderError("model.base_url is required for openai-compatible providers")
        return build_openai_compatible(
            model=model.model,
            base_url=model.base_url,
            api_key_env=model.api_key_env,
            extra_headers=model.extra_headers,
        )
    if model.provider == "anthropic":
        return build_anthropic(
            model=model.model,
            api_key_env=model.api_key_env,
            base_url=model.base_url,
        )
    if model.provider == "google":
        return build_google(
            model=model.model,
            api_key_env=model.api_key_env,
            base_url=model.base_url,
        )
    if model.provider == "vertex":
        return build_vertex(
            model=model.model,
            project=model.project,
            location=model.location,
            credentials_path=model.credentials_path,
        )
    raise ProviderError(f"Unknown provider '{model.provider}'")

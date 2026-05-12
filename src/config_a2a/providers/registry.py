"""Provider factory: build the right `LlmProvider` for a `ModelConfig`."""

from __future__ import annotations

from config_a2a.config.models import ModelConfig
from config_a2a.providers.base import LlmProvider, ProviderError
from config_a2a.providers.openai_compat import build_openai_compatible


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
    raise ProviderError(
        f"Provider '{model.provider}' is not implemented yet (planned in a later iteration)."
    )

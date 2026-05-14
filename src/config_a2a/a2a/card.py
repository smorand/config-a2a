"""Agent Card builder (A2A v1.0) and server-level directory builder."""

from __future__ import annotations

from typing import Any

from config_a2a.config.models import (
    AgentConfig,
    CardCapabilitiesConfig,
    CardConfig,
    CardProviderConfig,
    ServerConfig,
)


def _merge_card(agent_card: CardConfig | None, server_card: CardConfig) -> CardConfig:
    """Per-agent values win; missing fields fall back to the server card."""
    if agent_card is None:
        return server_card
    return CardConfig(
        provider=agent_card.provider or server_card.provider,
        documentation_url=agent_card.documentation_url or server_card.documentation_url,
        icon_url=agent_card.icon_url or server_card.icon_url,
        default_input_modes=(
            agent_card.default_input_modes
            if agent_card.default_input_modes is not None
            else server_card.default_input_modes
        ),
        default_output_modes=(
            agent_card.default_output_modes
            if agent_card.default_output_modes is not None
            else server_card.default_output_modes
        ),
        capabilities=agent_card.capabilities or server_card.capabilities,
        supports_authenticated_extended_card=(
            agent_card.supports_authenticated_extended_card
            if agent_card.supports_authenticated_extended_card is not None
            else server_card.supports_authenticated_extended_card
        ),
    )


def _provider_dict(provider: CardProviderConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {"organization": provider.organization}
    if provider.url is not None:
        payload["url"] = provider.url
    return payload


def _capabilities_dict(capabilities: CardCapabilitiesConfig | None) -> dict[str, Any]:
    cap = capabilities or CardCapabilitiesConfig()
    return {
        "streaming": cap.streaming,
        "pushNotifications": cap.push_notifications,
        "stateTransitionHistory": cap.state_transition_history,
    }


def build_agent_card(
    agent: AgentConfig,
    base_url: str,
    *,
    server_card: CardConfig | None = None,
) -> dict[str, Any]:
    """Return a JSON-serialisable Agent Card for the given agent.

    ``base_url`` should already point at the mounted prefix
    (e.g. ``http://host:port/agents/<slug>``).
    """
    card_cfg = _merge_card(agent.card, server_card or CardConfig())
    default_in = card_cfg.default_input_modes or ["text"]
    default_out = card_cfg.default_output_modes or ["text"]

    card: dict[str, Any] = {
        "name": agent.name,
        "description": agent.description,
        "version": agent.version,
        "url": base_url.rstrip("/"),
        "capabilities": _capabilities_dict(card_cfg.capabilities),
        "defaultInputModes": list(default_in),
        "defaultOutputModes": list(default_out),
        "skills": [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "tags": skill.tags,
                "inputModes": skill.input_modes,
                "outputModes": skill.output_modes,
                "examples": skill.examples,
            }
            for skill in agent.skills
        ],
        "interface": {"transport": "HTTP+JSON", "version": "1.0"},
    }
    if card_cfg.provider is not None:
        card["provider"] = _provider_dict(card_cfg.provider)
    if card_cfg.documentation_url is not None:
        card["documentationUrl"] = card_cfg.documentation_url
    if card_cfg.icon_url is not None:
        card["iconUrl"] = card_cfg.icon_url
    if card_cfg.supports_authenticated_extended_card is not None:
        card["supportsAuthenticatedExtendedCard"] = card_cfg.supports_authenticated_extended_card
    auth = agent.effective_authentication
    if auth.type != "none":
        scheme: dict[str, Any]
        if auth.type == "bearer":
            scheme = {"type": "http", "scheme": "bearer"}
        else:
            scheme = {"type": "apiKey", "in": "header", "name": auth.header_name}
        card["securitySchemes"] = {"default": scheme}
        card["security"] = [{"default": []}]
    return card


def build_directory(server: ServerConfig, base_url: str) -> dict[str, Any]:
    """Server-level ``/.well-known/agent-card.json`` directory listing."""
    root = base_url.rstrip("/")
    return {
        "name": server.name,
        "description": server.description,
        "version": server.version,
        "agents": [
            {
                "slug": agent.slug,
                "url": f"{root}/agents/{agent.slug}",
                "name": agent.name,
                "description": agent.description,
            }
            for agent in server.agents
        ],
    }


__all__ = ["build_agent_card", "build_directory"]

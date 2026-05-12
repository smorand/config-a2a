"""Agent Card builder (A2A v1.0)."""

from __future__ import annotations

from typing import Any

from config_a2a.config.models import AgentConfig


def build_agent_card(config: AgentConfig, base_url: str) -> dict[str, Any]:
    """Return a JSON-serialisable Agent Card for the given configuration.

    The structure follows A2A v1.0 §4.4.1 with the additions consumed by `web-a2a`.
    """
    card: dict[str, Any] = {
        "name": config.name,
        "description": config.description,
        "version": config.version,
        "url": base_url.rstrip("/"),
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
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
            for skill in config.skills
        ],
        "interface": {"transport": "HTTP+JSON", "version": "1.0"},
    }
    if config.authentication.type != "none":
        scheme: dict[str, Any]
        if config.authentication.type == "bearer":
            scheme = {"type": "http", "scheme": "bearer"}
        else:
            scheme = {"type": "apiKey", "in": "header", "name": config.authentication.header_name}
        card["securitySchemes"] = {"default": scheme}
        card["security"] = [{"default": []}]
    return card

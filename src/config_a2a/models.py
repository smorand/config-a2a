"""Pydantic models describing an A2A agent configuration."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SkillConfig(BaseModel):
    """A single skill exposed by the agent."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Unique skill identifier")
    description: str = Field(..., description="Human-readable skill description")
    inputs: dict[str, str] = Field(default_factory=dict, description="Input name to type mapping")
    outputs: dict[str, str] = Field(default_factory=dict, description="Output name to type mapping")


class AgentConfig(BaseModel):
    """Top-level agent configuration loaded from YAML."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Agent name")
    version: str = Field(default="0.1.0", description="Agent version")
    description: str = Field(default="", description="Agent description")
    skills: list[SkillConfig] = Field(default_factory=list, description="Skills offered by the agent")
    metadata: dict[str, str] = Field(default_factory=dict, description="Free-form metadata")

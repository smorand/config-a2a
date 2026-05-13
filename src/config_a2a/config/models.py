"""Pydantic v2 models describing the YAML configuration of an A2A agent."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    """Base model: forbid unknown keys to catch typos in YAML."""

    model_config = ConfigDict(extra="forbid")


class ServerConfig(_Strict):
    host: str = "0.0.0.0"  # nosec B104
    port: int = Field(default=9000, ge=1, le=65535)


class PersistenceConfig(_Strict):
    backend: Literal["sqlite", "postgresql"] = "sqlite"
    url: str = "sqlite+aiosqlite:///./state/agent.db"
    run_migrations_on_start: bool = True


class ModelConfig(_Strict):
    provider: Literal["anthropic", "google", "vertex", "openai-compatible"]
    model: str
    api_key_env: str | None = None
    base_url: str | None = None
    project: str | None = None
    location: str | None = None
    credentials_path: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    temperature: float | None = None
    max_output_tokens: int | None = None


class PromptsConfig(_Strict):
    system: str | None = None
    system_file: Path | None = None

    @model_validator(mode="after")
    def _at_most_one(self) -> "PromptsConfig":
        if self.system and self.system_file:
            raise ValueError("Set either `system` or `system_file`, not both.")
        return self


class McpStdioServer(_Strict):
    name: str
    transport: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    discovery_timeout_seconds: float = 10.0


class McpStreamableHttpServer(_Strict):
    name: str
    transport: Literal["streamable-http"] = "streamable-http"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


class McpSseServer(_Strict):
    name: str
    transport: Literal["sse"] = "sse"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


McpServer = Annotated[
    McpStdioServer | McpStreamableHttpServer | McpSseServer,
    Field(discriminator="transport"),
]


class ToolFilters(_Strict):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class ToolsConfig(_Strict):
    mcp_servers: list[McpServer] = Field(default_factory=list)
    filters: ToolFilters = Field(default_factory=ToolFilters)


class AntiLoopConfig(_Strict):
    enabled: bool = True
    similarity_threshold: float = Field(default=0.92, ge=0.0, le=1.0)


class GuardrailsConfig(_Strict):
    max_loops: int = Field(default=30, ge=1, le=200)
    max_tokens: int = Field(default=200_000, ge=128, le=2_000_000)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    max_depth: int = Field(default=5, ge=1, le=20)
    anti_loop: AntiLoopConfig = Field(default_factory=AntiLoopConfig)


class ConfirmationsConfig(_Strict):
    destructive_hint: Literal["prompt", "auto_approve", "auto_deny"] = "prompt"
    per_tool: dict[str, Literal["prompt", "auto_approve", "auto_deny"]] = Field(default_factory=dict)


class OtelConfig(_Strict):
    enabled: bool = True
    service_name: str | None = None
    exporter: Literal["jsonl", "otlp"] = "jsonl"
    jsonl_path: Path = Path("traces/agent.jsonl")
    otlp_endpoint: str | None = None


class ObservabilityConfig(_Strict):
    otel: OtelConfig = Field(default_factory=OtelConfig)


class SkillConfig(_Strict):
    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=lambda: ["text"])
    output_modes: list[str] = Field(default_factory=lambda: ["text"])
    examples: list[str] = Field(default_factory=list)


class AuthenticationConfig(_Strict):
    type: Literal["none", "bearer", "api_key"] = "none"
    header_name: str = "Authorization"
    value_env: str | None = None


# --- Memory ----------------------------------------------------------------


class WorkingMemoryConfig(_Strict):
    strategy: Literal["none", "sliding_summary"] = "none"
    window: int = Field(default=20, ge=2, le=500)
    summarize_every: int = Field(default=10, ge=1, le=200)
    summary_prompt: str = (
        "Summarise the conversation so far in 3-5 short sentences. "
        "Preserve concrete facts (numbers, names, decisions); drop pleasantries."
    )


class MemoryStoreConfig(_Strict):
    backend: Literal["sqlite", "in_memory"] = "sqlite"
    url: str | None = None  # defaults to persistence.url


class MemoryReadConfig(_Strict):
    when: Literal["none", "first_turn", "every_turn"] = "first_turn"
    scopes: list[Literal["user", "agent"]] = Field(default_factory=lambda: ["user", "agent"])
    top_k: int = Field(default=5, ge=1, le=50)
    max_chars: int = Field(default=1500, ge=100, le=20000)


class MemoryWriteConfig(_Strict):
    when: Literal["off", "after_terminal"] = "after_terminal"
    extract_with: Literal["none", "llm"] = "llm"
    scope: Literal["user", "agent", "infer"] = "infer"


class LongTermMemoryConfig(_Strict):
    store: MemoryStoreConfig = Field(default_factory=MemoryStoreConfig)
    read: MemoryReadConfig = Field(default_factory=MemoryReadConfig)
    write: MemoryWriteConfig = Field(default_factory=MemoryWriteConfig)


class MemoryConfig(_Strict):
    enabled: bool = False
    working: WorkingMemoryConfig = Field(default_factory=WorkingMemoryConfig)
    long_term: LongTermMemoryConfig = Field(default_factory=LongTermMemoryConfig)
    expose_as_tools: bool = False  # opt-in Letta-style mode (future)


# --- Pattern variants -------------------------------------------------------


class _PatternBase(_Strict):
    pass


class SimplePattern(_PatternBase):
    type: Literal["simple"] = "simple"


class ReactPattern(_PatternBase):
    type: Literal["react"] = "react"
    max_iterations: int = Field(default=10, ge=1, le=50)
    executor_prompt: str | None = None
    executor_prompt_file: Path | None = None


class PlannerSubConfig(_Strict):
    prompt: str | None = None
    prompt_file: Path | None = None
    model: str | None = None


class ExecutorSubConfig(_Strict):
    prompt: str | None = None
    prompt_file: Path | None = None
    model: str | None = None


class PlanExecutePattern(_PatternBase):
    type: Literal["plan_execute"] = "plan_execute"
    max_steps: int = Field(default=20, ge=1, le=100)
    max_replans: int = Field(default=3, ge=0, le=10)
    planner: PlannerSubConfig = Field(default_factory=PlannerSubConfig)
    executor: ExecutorSubConfig = Field(default_factory=ExecutorSubConfig)


class HandoffAuth(_Strict):
    type: Literal["none", "bearer", "api_key"] = "none"
    header_name: str = "Authorization"
    value_env: str | None = None


class HandoffTarget(_Strict):
    name: str
    description: str | None = None
    agent_ref: Path | None = None
    a2a_url: str | None = None
    auth: HandoffAuth = Field(default_factory=HandoffAuth)

    @model_validator(mode="after")
    def _exactly_one(self) -> "HandoffTarget":
        if bool(self.agent_ref) == bool(self.a2a_url):
            raise ValueError("Set exactly one of `agent_ref` or `a2a_url` on a handoff target.")
        return self


class RouterSubConfig(_Strict):
    prompt: str | None = None
    prompt_file: Path | None = None
    model: str | None = None


class HandoffPattern(_PatternBase):
    type: Literal["handoff"] = "handoff"
    router: RouterSubConfig = Field(default_factory=RouterSubConfig)
    targets: list[HandoffTarget]


class OrchestrateAgentRef(_Strict):
    name: str
    a2a_url: str
    input_template: str = "{{ user_text }}"
    auth: HandoffAuth = Field(default_factory=HandoffAuth)


class AggregatorSubConfig(_Strict):
    prompt: str | None = None
    prompt_file: Path | None = None
    model: str | None = None


class OrchestratePattern(_PatternBase):
    type: Literal["orchestrate"] = "orchestrate"
    mode: Literal["sequential", "parallel"] = "parallel"
    agents: list[OrchestrateAgentRef]
    aggregator: AggregatorSubConfig = Field(default_factory=AggregatorSubConfig)


class DebaterConfig(_Strict):
    name: str
    prompt: str | None = None
    prompt_file: Path | None = None
    model: str | None = None


class JudgeSubConfig(_Strict):
    prompt: str | None = None
    prompt_file: Path | None = None
    model: str | None = None


class DebatePattern(_PatternBase):
    type: Literal["debate"] = "debate"
    rounds: int = Field(default=3, ge=1, le=20)
    debaters: list[DebaterConfig]
    judge: JudgeSubConfig = Field(default_factory=JudgeSubConfig)


class ToTPattern(_PatternBase):
    type: Literal["tree_of_thoughts"] = "tree_of_thoughts"
    branches: int = Field(default=4, ge=2, le=16)
    depth: int = Field(default=3, ge=1, le=10)
    selection: Literal["top_k", "best"] = "top_k"
    top_k: int = Field(default=2, ge=1, le=8)
    evaluator_prompt: str | None = None
    evaluator_prompt_file: Path | None = None
    generator_prompt: str | None = None
    generator_prompt_file: Path | None = None


PatternConfig = Annotated[
    SimplePattern
    | ReactPattern
    | PlanExecutePattern
    | HandoffPattern
    | OrchestratePattern
    | DebatePattern
    | ToTPattern,
    Field(discriminator="type"),
]


class AgentConfig(_Strict):
    """Top-level configuration of one A2A agent server."""

    name: str
    version: str = "0.1.0"
    description: str = ""

    server: ServerConfig = Field(default_factory=ServerConfig)
    persistence: PersistenceConfig = Field(default_factory=PersistenceConfig)
    model: ModelConfig
    pattern: PatternConfig
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    confirmations: ConfirmationsConfig = Field(default_factory=ConfirmationsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    skills: list[SkillConfig] = Field(default_factory=list)
    authentication: AuthenticationConfig = Field(default_factory=AuthenticationConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

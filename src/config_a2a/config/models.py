"""Pydantic v2 models describing the YAML configuration of a multi-agent server.

A YAML file produces one ``ServerConfig`` (the FastAPI process) that owns N
``AgentConfig`` entries, each mounted under ``/agents/<slug>``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:  # pragma: no cover — forward ref resolved via model_rebuild
    from config_a2a.config.juicefs import JuiceFSConfig

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase, replace runs of non-alphanumerics with ``-``, strip the edges."""
    lowered = value.strip().lower()
    cleaned = _NON_ALNUM.sub("-", lowered).strip("-")
    return cleaned


class _Strict(BaseModel):
    """Base model: forbid unknown keys to catch typos in YAML."""

    model_config = ConfigDict(extra="forbid")


class ServerBindConfig(_Strict):
    """Network binding for the FastAPI process."""

    host: str = "0.0.0.0"  # nosec B104
    port: int = Field(default=9000, ge=1, le=65535)


class ServerIdentityConfig(_Strict):
    """Server-wide end-user identity: signed-JWT verification (the only mode).

    The inbound Bearer JWT on ``header`` is signature-verified with
    ``public_key_path`` (RS256 by default), the issuer is pinned to ``web-a2a``
    and the identity is read from the ``email`` claim. A missing or invalid token
    yields ``401`` at the A2A boundary. ``service_token_path`` points at a
    pre-minted service JWT presented during tool discovery (no end user in
    context). ``public_key_path`` is required: configuring ``identity:`` at all
    means turning on JWT verification.
    """

    public_key_path: str
    header: str = "X-Forwarded-Authorization"
    algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    issuer: str | None = "web-a2a"
    audience: str | None = None
    claim: str = "email"
    service_token_path: str | None = None


class PersistenceConfig(_Strict):
    backend: Literal["sqlite", "postgresql"] = "sqlite"
    url: str = "sqlite+aiosqlite:///./state/server.db"
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
    # Per-request end-user identity forwarding (set by the juicefs desugaring).
    # When ``forward_identity`` is true the verified ``Bearer <jwt>`` of the
    # current end user (see ``config_a2a.identity``) is injected into
    # ``identity_header`` on every outbound call; during tool discovery (no user
    # in context) the static ``service_credential`` (``Bearer <service token>``)
    # is used instead so ``list_tools`` passes the downstream auth middleware.
    forward_identity: bool = False
    identity_header: str = "X-Forwarded-Authorization"
    service_credential: str | None = None


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
    jsonl_path: Path = Path("traces/server.jsonl")
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


class AdminConfig(_Strict):
    """Admin REST surface. Disabled and empty `agents` makes the server inert."""

    enabled: bool = True
    authentication: AuthenticationConfig = Field(default_factory=AuthenticationConfig)


class CardProviderConfig(_Strict):
    organization: str
    url: str | None = None


class CardCapabilitiesConfig(_Strict):
    streaming: bool = True
    push_notifications: bool = False
    state_transition_history: bool = True


class CardConfig(_Strict):
    """Agent Card metadata: inherited from server-level, agents may override."""

    provider: CardProviderConfig | None = None
    documentation_url: str | None = None
    icon_url: str | None = None
    default_input_modes: list[str] | None = None
    default_output_modes: list[str] | None = None
    capabilities: CardCapabilitiesConfig | None = None
    supports_authenticated_extended_card: bool | None = None


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
    """One agent mounted under ``/agents/<slug>``.

    ``slug`` defaults to ``slugify(name)``. ``persistence`` and ``authentication``
    are optional; when omitted the server-level defaults apply (the loader fills
    them in).
    """

    slug: str | None = None
    name: str
    version: str = "0.1.0"
    description: str = ""

    persistence: PersistenceConfig | None = None
    authentication: AuthenticationConfig | None = None

    model: ModelConfig
    pattern: PatternConfig
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    confirmations: ConfirmationsConfig = Field(default_factory=ConfirmationsConfig)
    skills: list[SkillConfig] = Field(default_factory=list)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    card: CardConfig | None = None
    juicefs: "JuiceFSConfig | None" = None

    @model_validator(mode="after")
    def _desugar_juicefs(self) -> "AgentConfig":
        """Compile a ``juicefs:`` block into an identity-forwarding MCP server.

        Runs on every validation (server load and admin ``/agents`` load) and is
        idempotent: it skips when a server with the same name is already present.
        """
        if self.juicefs is None:
            return self
        # Lazy import breaks the import cycle (binding imports this module for
        # ``McpStreamableHttpServer``). The forward ref is resolved by
        # ``config_a2a.config`` at package import time.
        from config_a2a.juicefs.binding import (  # pylint: disable=import-outside-toplevel
            compile_juicefs,
            merge_filters,
        )

        compiled = compile_juicefs(self.juicefs)
        if not any(server.name == compiled.name for server in self.tools.mcp_servers):
            self.tools.mcp_servers.append(compiled)
        # Fold the juicefs.filters into the agent-wide tools.filters (idempotent
        # deduplicated union), so they apply uniformly during MCP discovery.
        self.tools.filters = merge_filters(self.tools.filters, self.juicefs.filters)
        return self

    @model_validator(mode="after")
    def _slug_default_and_shape(self) -> "AgentConfig":
        if self.slug is None:
            candidate = slugify(self.name)
            if not candidate:
                raise ValueError(f"cannot derive slug from agent name {self.name!r}; set `slug:` explicitly")
            self.slug = candidate
        if not _SLUG_PATTERN.match(self.slug):
            raise ValueError(f"slug {self.slug!r} must match {_SLUG_PATTERN.pattern!r}")
        return self

    @property
    def effective_persistence(self) -> PersistenceConfig:
        """Persistence config, defaulting to the package defaults if unset."""
        return self.persistence if self.persistence is not None else PersistenceConfig()

    @property
    def effective_authentication(self) -> AuthenticationConfig:
        """Authentication config, defaulting to ``type=none`` if unset."""
        return self.authentication if self.authentication is not None else AuthenticationConfig()


class ServerConfig(_Strict):
    """One FastAPI process exposing N agents under ``/agents/<slug>``."""

    name: str
    version: str = "0.1.0"
    description: str = ""

    server: ServerBindConfig = Field(default_factory=ServerBindConfig)
    identity: ServerIdentityConfig | None = None
    persistence: PersistenceConfig = Field(default_factory=PersistenceConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    card: CardConfig = Field(default_factory=CardConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    agents: list[AgentConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_agents(self) -> "ServerConfig":
        if not self.admin.enabled and not self.agents:
            raise ValueError(
                "server is inert: admin.enabled=false AND agents=[]; " "enable admin or provide at least one agent"
            )
        seen: set[str] = set()
        for agent in self.agents:
            assert agent.slug is not None  # post-validator filled it
            if agent.slug in seen:
                raise ValueError(f"duplicate agent slug: {agent.slug!r}")
            seen.add(agent.slug)
            # Inherit server-level defaults when omitted on the agent.
            if agent.persistence is None:
                agent.persistence = self.persistence
            if agent.authentication is None:
                agent.authentication = AuthenticationConfig()
        return self

    @model_validator(mode="after")
    def _compile_juicefs_with_identity(self) -> "ServerConfig":
        """Recompile each agent's ``juicefs:`` block with the server-wide identity.

        ``AgentConfig._desugar_juicefs`` already produced a baseline (it cannot
        see ``ServerConfig.identity``, so it uses the default JWT header and no
        service credential). This server-level pass replaces that compiled server
        with one that honours ``self.identity``: the configured JWT header is
        used and the static service token is read for tool discovery. The
        operation is idempotent (replace, never duplicate).
        """
        from config_a2a.juicefs.binding import (  # pylint: disable=import-outside-toplevel
            compile_juicefs,
            merge_filters,
        )

        for agent in self.agents:
            if agent.juicefs is None:
                continue
            compiled = compile_juicefs(agent.juicefs, server_identity=self.identity)
            servers = agent.tools.mcp_servers
            for index, server in enumerate(servers):
                if server.name == compiled.name:
                    servers[index] = compiled
                    break
            else:
                servers.append(compiled)
            agent.tools.filters = merge_filters(agent.tools.filters, agent.juicefs.filters)
        return self


__all__ = [
    "AdminConfig",
    "AgentConfig",
    "AggregatorSubConfig",
    "AntiLoopConfig",
    "AuthenticationConfig",
    "CardCapabilitiesConfig",
    "CardConfig",
    "CardProviderConfig",
    "ConfirmationsConfig",
    "DebatePattern",
    "DebaterConfig",
    "ExecutorSubConfig",
    "GuardrailsConfig",
    "HandoffAuth",
    "HandoffPattern",
    "HandoffTarget",
    "JudgeSubConfig",
    "JuiceFSConfig",
    "LongTermMemoryConfig",
    "McpServer",
    "McpSseServer",
    "McpStdioServer",
    "McpStreamableHttpServer",
    "MemoryConfig",
    "MemoryReadConfig",
    "MemoryStoreConfig",
    "MemoryWriteConfig",
    "ModelConfig",
    "ObservabilityConfig",
    "OrchestrateAgentRef",
    "OrchestratePattern",
    "OtelConfig",
    "PatternConfig",
    "PersistenceConfig",
    "PlanExecutePattern",
    "PlannerSubConfig",
    "PromptsConfig",
    "ReactPattern",
    "RouterSubConfig",
    "ServerBindConfig",
    "ServerConfig",
    "ServerIdentityConfig",
    "SimplePattern",
    "SkillConfig",
    "ToTPattern",
    "ToolFilters",
    "ToolsConfig",
    "WorkingMemoryConfig",
    "slugify",
]

# NOTE: the ``AgentConfig.juicefs`` forward reference is resolved by
# ``config_a2a.config.__init__`` (which imports ``JuiceFSConfig`` and calls
# ``model_rebuild``) to keep this module free of an import-time cycle.

"""Configuration package.

Resolves the ``AgentConfig.juicefs`` forward reference here, after both
``models`` and ``juicefs`` modules are imported, so neither module needs an
import-time dependency on the other (``models`` only refers to
``JuiceFSConfig`` via a string annotation).
"""

from __future__ import annotations

from config_a2a.config import models as _models
from config_a2a.config.juicefs import JuiceFSConfig

# Resolve the ``AgentConfig.juicefs`` string annotation against a namespace that
# carries every model defined in ``models`` plus ``JuiceFSConfig``.
_namespace = {**vars(_models), "JuiceFSConfig": JuiceFSConfig}
_models.AgentConfig.model_rebuild(_types_namespace=_namespace)
_models.ServerConfig.model_rebuild(_types_namespace=_namespace)

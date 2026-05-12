"""Pattern dispatch — maps `config.pattern.type` to its executor coroutine."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from config_a2a.patterns.base import ExecutionContext, PatternError
from config_a2a.patterns.plan_execute import run_plan_execute
from config_a2a.patterns.react import run_react
from config_a2a.patterns.simple import run_simple

PatternRunner = Callable[[ExecutionContext], Awaitable[None]]

_RUNNERS: dict[str, PatternRunner] = {
    "simple": run_simple,
    "react": run_react,
    "plan_execute": run_plan_execute,
}


def register(name: str, runner: PatternRunner) -> None:
    _RUNNERS[name] = runner


def get_runner(pattern_type: str) -> PatternRunner:
    if pattern_type not in _RUNNERS:
        raise PatternError(
            f"Pattern '{pattern_type}' is not implemented yet (planned in a later iteration)."
        )
    return _RUNNERS[pattern_type]


__all__ = ["ExecutionContext", "PatternError", "PatternRunner", "get_runner", "register"]

"""Pattern dispatch — maps `config.pattern.type` to its executor coroutine."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from config_a2a.patterns.base import ExecutionContext, PatternError
from config_a2a.patterns.debate import run_debate
from config_a2a.patterns.handoff import run_handoff
from config_a2a.patterns.orchestrate import run_orchestrate
from config_a2a.patterns.plan_execute import run_plan_execute
from config_a2a.patterns.react import run_react
from config_a2a.patterns.simple import run_simple
from config_a2a.patterns.tree_of_thoughts import run_tree_of_thoughts

PatternRunner = Callable[[ExecutionContext], Awaitable[None]]

_RUNNERS: dict[str, PatternRunner] = {
    "simple": run_simple,
    "react": run_react,
    "plan_execute": run_plan_execute,
    "handoff": run_handoff,
    "orchestrate": run_orchestrate,
    "debate": run_debate,
    "tree_of_thoughts": run_tree_of_thoughts,
}


def register(name: str, runner: PatternRunner) -> None:
    _RUNNERS[name] = runner


def get_runner(pattern_type: str) -> PatternRunner:
    if pattern_type not in _RUNNERS:
        raise PatternError(f"Pattern '{pattern_type}' is not implemented yet (planned in a later iteration).")
    return _RUNNERS[pattern_type]


__all__ = ["ExecutionContext", "PatternError", "PatternRunner", "get_runner", "register"]

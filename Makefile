.PHONY: help sync format lint test e2e migrate clean \
        run-simple run-react run-plan run-handoff run-orchestrate run-debate run-tot

UV ?= uv

help:
	@echo "Targets:"
	@echo "  sync          uv sync --extra dev --extra google"
	@echo "  format        black -l 120 src tests"
	@echo "  lint          pylint src"
	@echo "  test          pytest tests/unit"
	@echo "  e2e           RUN_E2E=1 pytest tests/e2e (needs OPENROUTER_API_KEY)"
	@echo "  migrate       alembic upgrade head (SQLite by default)"
	@echo "  run-<example> uv run agent --config config_examples/<example>/agent.yaml"

sync:
	$(UV) sync --extra dev

format:
	$(UV) run black -l 120 src tests

# --fail-under=9.0: this codebase does not mandate docstrings on every private
# helper/method (C0115/C0116 fire on most of it by design, not by neglect), so
# pylint's default "any message at all is a failure" exit-code behaviour would
# never let this target pass. Score-gating (as opposed to disabling those checks
# outright) keeps them visible in the report while only failing the build on a
# real, git-diff-sized regression.
lint:
	$(UV) run pylint --fail-under=9.0 src

test:
	$(UV) run pytest tests/unit

e2e:
	RUN_E2E=1 $(UV) run pytest tests/e2e

migrate:
	$(UV) run alembic upgrade head

clean:
	rm -rf state traces .pytest_cache .mypy_cache .ruff_cache

run-simple:
	$(UV) run agent --config config_examples/01-simple/agent.yaml

run-react:
	$(UV) run agent --config config_examples/02-react/agent.yaml

run-plan:
	$(UV) run agent --config config_examples/03-plan-execute/agent.yaml

run-handoff:
	$(UV) run agent --config config_examples/04-handoff/router.yaml

run-orchestrate:
	$(UV) run agent --config config_examples/05-orchestrate/agent.yaml

run-debate:
	$(UV) run agent --config config_examples/06-debate/agent.yaml

run-tot:
	$(UV) run agent --config config_examples/07-tree-of-thoughts/agent.yaml

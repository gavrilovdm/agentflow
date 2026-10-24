"""Runtime dependencies injected into every node via LangGraph's `context`.

Keeps nodes free of globals: tests and the API/worker pass the run's config here.
Context is not checkpointed, so a resumed run simply receives a fresh one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentflow.config import WorkflowConfig


@dataclass
class Deps:
    config: WorkflowConfig = field(default_factory=WorkflowConfig)

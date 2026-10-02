import pytest
from pydantic import ValidationError

from agentflow.agents import orchestrator
from agentflow.schemas import SpecDraft

BASE = dict(title="t", goal="g", constraints=[], technical_notes="", out_of_scope=[])


def test_single_string_criterion_is_rejected_but_bullets_are_rescued():
    # Seen in a live run: the model returned one sentence instead of a list.
    with pytest.raises(ValidationError):
        SpecDraft(**BASE, acceptance_criteria="Database gains get and update methods")
    ok = SpecDraft(**BASE, acceptance_criteria="- returns the user\n- raises EmailTaken on duplicates")
    assert ok.acceptance_criteria == ["returns the user", "raises EmailTaken on duplicates"]


async def test_generate_spec_retries_with_schema_hint(monkeypatch):
    seen: list[int] = []

    class Flaky:
        async def ainvoke(self, messages, config=None):
            seen.append(len(messages))
            if len(seen) == 1:
                return SpecDraft(**BASE, acceptance_criteria="only one")  # raises ValidationError
            return SpecDraft(**BASE, acceptance_criteria=["a", "b"])

    monkeypatch.setattr(orchestrator, "structured", lambda *a, **k: Flaky())
    spec = await orchestrator.generate_spec("p", "")
    assert spec.acceptance_criteria == ["a", "b"]
    assert seen[1] == seen[0] + 1  # second attempt carried the hint

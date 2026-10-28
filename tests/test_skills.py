from pathlib import Path

import pytest

from agentflow.agents import orchestrator, reviewer
from agentflow.agents.coder import CoderContext, system_prompt
from agentflow.schemas import Spec, Task
from agentflow.skills import ROLES, SKILLS_DIR, load_skills, parse_skill, role_section

SPEC = Spec(
    id="s", title="S", goal="g", constraints=[], acceptance_criteria=["c1", "c2"], technical_notes="n", out_of_scope=[]
)
TASK = Task(id="t1", title="T", description="d", target_files=["a.py"], depends_on=[], definition_of_done="done")
LANGUAGE_WORDS = ("pytest", "vitest", "python", "typescript", "jsx", "npm", "pip")


def _write(root: Path, role: str, name: str, front: str, body: str = "Body.") -> None:
    (root / role).mkdir(parents=True, exist_ok=True)
    (root / role / f"{name}.md").write_text(f"---\n{front}\n---\n{body}\n")


def test_every_role_ships_skills_that_parse():
    for role in ROLES:
        assert load_skills(role), role
    for path in SKILLS_DIR.glob("*/*.md"):
        assert path.parent.name in ROLES, path
        parse_skill(path, path.parent.name)


def test_skills_stay_language_agnostic():
    # Language and framework rules belong to the gate that runs the checks; a skill that
    # names one is noise for every run in another language.
    for role in ROLES:
        text = role_section(role).lower()
        assert not [w for w in LANGUAGE_WORDS if w in text], role


def test_each_role_prompt_carries_its_own_skills_only():
    assert "### Planning" in orchestrator.SPEC_SYSTEM
    assert "### Planning" in orchestrator.TASKS_SYSTEM
    assert "### Planning" in orchestrator.REPLAN_SYSTEM
    assert "### Review" in reviewer.REVIEW_SYSTEM and "### Planning" not in reviewer.REVIEW_SYSTEM
    coder_prompt = system_prompt(CoderContext(task=TASK, spec=SPEC, workspace=Path("."), language="python"))
    assert "### Implementation" in coder_prompt and "### Review" not in coder_prompt


def test_role_without_skills_adds_nothing(tmp_path: Path):
    assert "How you work" not in role_section("coder", tmp_path, hub="")
    assert role_section("intake", tmp_path, hub="") == ""


def test_malformed_skill_fails_loudly(tmp_path: Path):
    _write(tmp_path, "coder", "bad", "name: bad")
    with pytest.raises(ValueError, match="description"):
        load_skills("coder", tmp_path)


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="unknown role"):
        load_skills("janitor")


def test_hub_skills_reach_only_the_roles_they_name(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# router, not a skill\n")
    (tmp_path / "design.md").write_text("---\nname: Design\ndescription: d\nroles: coder, reviewer\n---\nSRP.\n")
    (tmp_path / "design-review.md").write_text("---\nname: Design review\ndescription: d\nroles: reviewer\n---\nB.\n")
    reviewer_section = role_section("reviewer", hub=str(tmp_path))
    assert reviewer_section.index("### Review") < reviewer_section.index("### Design\nSRP.")
    assert "### Design review" in reviewer_section
    coder_section = role_section("coder", hub=str(tmp_path))
    assert "### Design\nSRP." in coder_section and "Design review" not in coder_section
    assert "Design" not in role_section("tester", hub=str(tmp_path))


def test_hub_dir_without_skills_fails_loudly(tmp_path: Path):
    # a configured but unmounted hub must not silently drop the rules from every prompt
    with pytest.raises(RuntimeError, match="no skills"):
        role_section("coder", hub=str(tmp_path / "missing"))


def test_no_hub_configured_keeps_the_role_skills(monkeypatch):
    monkeypatch.delenv("HUB_ENGINEERING_DIR", raising=False)
    assert role_section("coder") == role_section("coder", hub="")


def test_english_rule_is_in_every_agent_role_prompt():
    from agentflow.agents import orchestrator, reviewer, test_generator
    from agentflow.skills import ENGLISH_RULE

    for role in ("orchestrator", "coder", "test_generator", "reviewer", "tester"):
        assert ENGLISH_RULE in role_section(role), role
    for prompt in (
        orchestrator.SPEC_SYSTEM,
        orchestrator.TASKS_SYSTEM,
        orchestrator.REPLAN_SYSTEM,
        reviewer.REVIEW_SYSTEM,
    ):
        assert ENGLISH_RULE in prompt
    assert ENGLISH_RULE in test_generator.SYSTEM + role_section("test_generator")


async def test_coder_out_of_steps_is_a_clear_failure(monkeypatch, tmp_path):
    from langgraph.errors import GraphRecursionError

    from agentflow.agents import coder

    class Boom:
        async def ainvoke(self, *a, **k):
            assert k["config"]["recursion_limit"] >= 150
            raise GraphRecursionError("Recursion limit of 75 reached")

    monkeypatch.setattr(coder, "create_agent", lambda *a, **k: Boom())
    monkeypatch.setattr(coder, "chat_model", lambda *a, **k: None)
    res = await coder.run_coder(coder.CoderContext(task=TASK, spec=SPEC, workspace=tmp_path, language="python"))
    assert not res.success and "Ran out of steps" in (res.error or "")

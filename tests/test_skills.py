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
    assert role_section("coder", tmp_path) == ""


def test_malformed_skill_fails_loudly(tmp_path: Path):
    _write(tmp_path, "coder", "bad", "name: bad")
    with pytest.raises(ValueError, match="description"):
        load_skills("coder", tmp_path)


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="unknown role"):
        load_skills("tester")

from pathlib import Path

import pytest

from agentflow.agents.coder import CoderContext, system_prompt
from agentflow.gate import ACCEPTANCE_TEST_DIR
from agentflow.schemas import Spec, Task
from agentflow.skills import ROLES, SKILLS_DIR, inline, load_skills, make_skill_tool, parse_skill, skill_index

SPEC = Spec(
    id="s", title="S", goal="g", constraints=[], acceptance_criteria=["c1", "c2"], technical_notes="n", out_of_scope=[]
)
TASK = Task(id="t1", title="T", description="d", target_files=["a.ts"], depends_on=[], definition_of_done="done")


def _write(root: Path, role: str, name: str, front: str, body: str = "Body.") -> None:
    (root / role).mkdir(parents=True, exist_ok=True)
    (root / role / f"{name}.md").write_text(f"---\n{front}\n---\n{body}\n")


def test_shipped_skills_parse_and_live_under_known_roles():
    for path in SKILLS_DIR.glob("*/*.md"):
        assert path.parent.name in ROLES, path
        parse_skill(path, path.parent.name)


def test_test_generator_gets_only_its_languages_rules():
    py = inline(load_skills("test_generator", "python"))
    ts = inline(load_skills("test_generator", "typescript"))
    assert py.startswith("- Use pytest.") and "vitest" not in py
    assert ts.startswith("- Use vitest:") and "pytest" not in ts


def test_acceptance_dir_placeholder_is_filled():
    for lang in ("python", "typescript"):
        rules = inline(load_skills("test_generator", lang))
        assert "{acceptance_dir}" not in rules
        assert f"`{ACCEPTANCE_TEST_DIR}/" in rules


def test_skill_without_languages_applies_everywhere(tmp_path: Path):
    _write(tmp_path, "coder", "any", "name: any\ndescription: d")
    _write(tmp_path, "coder", "ts", "name: ts\ndescription: d\nlanguages: typescript")
    assert [s.name for s in load_skills("coder", "python", tmp_path)] == ["any"]
    assert [s.name for s in load_skills("coder", "typescript", tmp_path)] == ["any", "ts"]


def test_malformed_skill_fails_loudly(tmp_path: Path):
    _write(tmp_path, "coder", "bad", "name: bad")
    with pytest.raises(ValueError, match="description"):
        load_skills("coder", "python", tmp_path)


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="unknown role"):
        load_skills("tester", "python")


def test_coder_sees_index_and_loads_body_on_demand(tmp_path: Path):
    _write(tmp_path, "coder", "react", "name: react\ndescription: React component conventions", "Use hooks.")
    skills = load_skills("coder", "typescript", tmp_path)
    ctx = CoderContext(
        task=TASK,
        spec=SPEC,
        workspace=tmp_path,
        language="typescript",
        skills=skills,
    )
    prompt = system_prompt(ctx)
    assert skill_index(skills) in prompt and "Use hooks." not in prompt
    tool = make_skill_tool(skills)
    assert tool.invoke({"name": "react"}) == "Use hooks."
    assert "Available: react" in tool.invoke({"name": "vue"})


def test_coder_prompt_has_no_skills_section_without_skills(tmp_path: Path):
    ctx = CoderContext(
        task=TASK,
        spec=SPEC,
        workspace=tmp_path,
        language="python",
    )
    assert "## Skills" not in system_prompt(ctx)

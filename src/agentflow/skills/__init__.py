"""Role skills: knowledge an agent needs only on some runs (a language, a test framework).

What a role *is* stays in its system prompt. A skill is what changes from run to run: the
pytest rules are noise to a TypeScript run, and every extra instruction an agent carries
dilutes the ones that matter. Each skill is a Markdown file under `skills/<role>/` with a
small front matter (`name`, `description`, optional `languages`); `load_skills` returns
only the ones that apply to this role and language.

Two ways to hand them to an agent:
- `inline` for single-shot calls (the test generator): there is no turn to load anything in.
- `skill_index` + `make_skill_tool` for tool-using agents (the coder): the prompt lists name
  and description, the body is fetched on demand, so ten skills cost ten lines until used.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from agentflow.gate.base import ACCEPTANCE_TEST_DIR

SKILLS_DIR = Path(__file__).parent
ROLES = ("orchestrator", "test_generator", "coder", "reviewer")

# Skill bodies name the acceptance test directory; keeping it a placeholder means a move of
# ACCEPTANCE_TEST_DIR cannot leave the prompts pointing the test writer at the old path.
_PLACEHOLDERS = {"{acceptance_dir}": ACCEPTANCE_TEST_DIR}


@dataclass(frozen=True)
class Skill:
    role: str
    name: str
    description: str
    languages: frozenset[str]  # empty: applies to every language
    body: str

    def applies_to(self, language: str) -> bool:
        return not self.languages or language in self.languages


def parse_skill(path: Path, role: str) -> Skill:
    text = path.read_text()
    head, sep, body = text.removeprefix("---\n").partition("\n---\n")
    if not text.startswith("---\n") or not sep:
        raise ValueError(f"{path}: missing front matter (--- ... ---)")
    meta: dict[str, str] = {}
    for line in head.splitlines():
        key, colon, value = line.partition(":")
        if not colon:
            raise ValueError(f"{path}: bad front matter line {line!r}")
        meta[key.strip()] = value.strip()
    for required in ("name", "description"):
        if not meta.get(required):
            raise ValueError(f"{path}: front matter needs {required!r}")
    for token, value in _PLACEHOLDERS.items():
        body = body.replace(token, value)
    languages = frozenset(x.strip() for x in meta.get("languages", "").split(",") if x.strip())
    return Skill(role, meta["name"], meta["description"], languages, body.strip())


def load_skills(role: str, language: str, root: Path = SKILLS_DIR) -> list[Skill]:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    role_dir = root / role
    if not role_dir.is_dir():
        return []
    skills = [parse_skill(p, role) for p in sorted(role_dir.glob("*.md"))]
    return [s for s in skills if s.applies_to(language)]


def inline(skills: list[Skill]) -> str:
    return "\n".join(s.body for s in skills)


def skill_index(skills: list[Skill]) -> str:
    return "\n".join(f"- {s.name}: {s.description}" for s in skills)


def make_skill_tool(skills: list[Skill]) -> BaseTool:
    by_name = {s.name: s for s in skills}

    @tool
    def load_skill(name: str) -> str:
        """Load the full instructions of one of the skills listed in your prompt."""
        skill = by_name.get(name)
        if skill is None:
            return f"No skill named {name!r}. Available: {', '.join(sorted(by_name))}"
        return skill.body

    return load_skill

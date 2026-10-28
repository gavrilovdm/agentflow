"""Role skills: the craft of each role, independent of the target repo's language.

A role's system prompt says what this call must produce (a spec, a plan, a test, a diff, a
verdict) and the contract around it. A skill says how a good engineer in that role works:
how to decompose, what makes a test worth having, how to review. Keeping that in Markdown
under `skills/<role>/` lets it be edited without touching the prompt code, and keeps it out
of the language-specific rules, which belong to the gate that runs the checks.

Skills are inlined, not fetched on demand: they apply to every call of the role, and a
coder given an optional search tool once never called it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).parent
ROLES = ("intake", "orchestrator", "test_generator", "coder", "reviewer", "tester")


@dataclass(frozen=True)
class Skill:
    role: str
    name: str
    description: str
    body: str


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
    return Skill(role, meta["name"], meta["description"], body.strip())


def load_skills(role: str, root: Path = SKILLS_DIR) -> list[Skill]:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    role_dir = root / role
    if not role_dir.is_dir():
        return []
    return [parse_skill(p, role) for p in sorted(role_dir.glob("*.md"))]


def role_section(role: str, root: Path = SKILLS_DIR) -> str:
    """The role's skills as a prompt section; empty when the role has none."""
    skills = load_skills(role, root)
    if not skills:
        return ""
    return "\n\n## How you work\n\n" + "\n\n".join(f"### {s.name}\n{s.body}" for s in skills)

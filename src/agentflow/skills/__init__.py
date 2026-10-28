"""Role skills: the craft of each role, independent of the target repo's language.

A role's system prompt says what this call must produce (a spec, a plan, a test, a diff, a
verdict) and the contract around it. A skill says how a good engineer in that role works:
how to decompose, what makes a test worth having, how to review. Keeping that in Markdown
under `skills/<role>/` lets it be edited without touching the prompt code, and keeps it out
of the language-specific rules, which belong to the gate that runs the checks.

Skills are inlined, not fetched on demand: they apply to every call of the role, and a
coder given an optional search tool once never called it.

Cross-project engineering rules (SOLID, OOP, clean code) live in the hub, not here, so people
and every project's agents read one copy: `HUB_ENGINEERING_DIR` points at `hub/engineering`,
whose skills name their roles in front matter (`roles: coder, reviewer`). Prompts are built at
import, so an edit there takes effect on the next worker start.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).parent
ROLES = ("intake", "orchestrator", "test_generator", "coder", "reviewer", "tester", "researcher")


@dataclass(frozen=True)
class Skill:
    role: str
    name: str
    description: str
    body: str
    roles: tuple[str, ...] = ()


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
    roles = tuple(r.strip() for r in meta.get("roles", "").split(",") if r.strip())
    return Skill(role, meta["name"], meta["description"], body.strip(), roles)


def load_skills(role: str, root: Path = SKILLS_DIR) -> list[Skill]:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    role_dir = root / role
    if not role_dir.is_dir():
        return []
    return [parse_skill(p, role) for p in sorted(role_dir.glob("*.md"))]


def hub_skills(role: str, hub: str | None = None) -> list[Skill]:
    """The hub's engineering skills that name this role. A configured directory without
    skills fails loudly: a missing mount would otherwise drop the rules from every prompt
    without a trace, as a missing whitelist mount once refused every card."""
    hub = os.environ.get("HUB_ENGINEERING_DIR") if hub is None else hub
    if not hub:
        return []
    files = [p for p in sorted(Path(hub).glob("*.md")) if p.name not in ("AGENTS.md", "README.md", "CLAUDE.md")]
    if not files:
        raise RuntimeError(f"HUB_ENGINEERING_DIR={hub} has no skills (is hub/engineering mounted?)")
    return [s for s in (parse_skill(p, role) for p in files) if role in s.roles]


# Cards are sometimes written in Russian, and a planner then wrote Russian acceptance criteria
# (one with a stray Chinese glyph). Everything agents read from each other stays English.
ENGLISH_RULE = (
    "Write all plans, task titles, acceptance criteria, tests, code comments, commit messages and "
    "reports in English, even when the card is written in another language. User-facing product "
    "strings keep the language the spec requires."
)
ENGLISH_ROLES = ("orchestrator", "coder", "test_generator", "reviewer", "tester")


def role_section(role: str, root: Path = SKILLS_DIR, hub: str | None = None) -> str:
    """The role's skills as a prompt section, the hub's after the role's own, preceded by the
    language rule for roles whose output other agents read; empty when there is nothing."""
    skills = load_skills(role, root) + hub_skills(role, hub)
    rule = f"\n\n## Language\n\n{ENGLISH_RULE}" if role in ENGLISH_ROLES else ""
    if not skills:
        return rule
    return rule + "\n\n## How you work\n\n" + "\n\n".join(f"### {s.name}\n{s.body}" for s in skills)

#!/usr/bin/env python3
"""Claude Code Stop hook: don't let a session end with code changed but docs stale.

Compares the branch against its merge-base with origin/main (committed + staged +
unstaged + untracked). If code changed and none of the docs did, it blocks the stop
once and tells Claude which docs map to the changed code. stop_hook_active prevents a
loop: on the second stop the session may end (e.g. when no doc change is warranted).
"""

from __future__ import annotations

import json
import subprocess
import sys

CODE_PREFIXES = ("src/agentflow/", "guide/src/", "evals/", "scripts/", ".github/workflows/")
CODE_EXCLUDE = ("guide/src/data/", "guide/src/lib/content.ts")
DOCS = ("README.md", "CLAUDE.md", "guide/README.md", "guide/src/lib/content.ts", "docs/")

# Which docs describe which code — so the reminder is actionable, not generic.
DOC_MAP = {
    "src/agentflow/graph/": "README.md (diagram, design notes) + guide/src/lib/content.ts (NODES) + guide routing port",
    "src/agentflow/agents/": "README.md (requirements table) + guide/src/lib/content.ts (NODES, INTERVIEW)",
    "src/agentflow/rag/": "README.md (Hybrid retrieval) + guide RAG concept text",
    "src/agentflow/gate/": "README.md (Gate per language) + guide BUGS/NODES if behaviour changed",
    "src/agentflow/api/": "README.md (As a service) + guide USE_CASES",
    "src/agentflow/schemas.py": "README.md if the task/spec contract changed + guide NODES",
    "evals/": "README.md (Evals section and numbers)",
    ".github/workflows/": "README.md (Development) + CLAUDE.md (workflow rules)",
}


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True)
    return [line for line in out.stdout.splitlines() if line.strip()] if out.returncode == 0 else []


def changed_files() -> set[str]:
    base = git("merge-base", "HEAD", "origin/main")
    files: set[str] = set()
    if base:
        files.update(git("diff", "--name-only", base[0]))
    files.update(git("diff", "--name-only"))
    files.update(git("diff", "--name-only", "--cached"))
    files.update(git("ls-files", "--others", "--exclude-standard"))
    return files


def main() -> int:
    ci = "--ci" in sys.argv  # same check as a PR status: exit 1 instead of blocking a session
    payload = {}
    if not ci:
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError:
            payload = {}
    if payload.get("stop_hook_active"):
        return 0  # already reminded once this stop cycle

    files = changed_files()
    code = sorted(f for f in files if f.startswith(CODE_PREFIXES) and not f.startswith(CODE_EXCLUDE))
    docs = [f for f in files if f.startswith(DOCS)]
    if not code or docs:
        return 0

    hints = sorted({doc for prefix, doc in DOC_MAP.items() for f in code if f.startswith(prefix)})
    reason = (
        "Code changed on this branch but no documentation did.\n"
        f"Changed code: {', '.join(code[:12])}{' …' if len(code) > 12 else ''}\n"
        "Update the docs that describe it:\n- " + "\n- ".join(hints or ["README.md"]) + "\n"
        "If the change genuinely needs no doc update (pure refactor, test-only), say so explicitly and stop again."
    )
    if ci:
        print(reason + "\n(Add the `no-docs` label to the PR to skip this check.)")
        return 1
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

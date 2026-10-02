"""End-to-end agent evaluation: real models, real gate, auto-approved checkpoints.

    ANTHROPIC_API_KEY=... DEEPSEEK_API_KEY=... uv run python -m evals.e2e_eval

Each case copies the fixture repo, runs the full graph in-place and records whether
tasks completed, how many gate failures / review cycles they needed, and wall time.
These are the numbers to watch when changing prompts, models or budgets; with
LANGSMITH_TRACING=true every run is a browsable trace tagged `eval:e2e`.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path

from git import Repo

from agentflow.config import WorkflowConfig
from agentflow.runner import open_persistence, resume_run, start_run

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sample_repo"
CASES = [
    "Add a function shop.orders.order_count(orders, user_id) that returns how many orders a user has",
    "Add shop.users.change_email(db, user_id, new_email) that normalises to lowercase and rejects duplicates",
    "Add a 10% loyalty discount in Order.total_cents when the user has more than 5 previous orders",
]


def _fixture_copy() -> Path:
    dest = Path(tempfile.mkdtemp(prefix="agentflow-eval-")) / "repo"
    shutil.copytree(FIXTURE, dest)
    (dest / "pyproject.toml").write_text(
        '[project]\nname="shop"\nversion="0.1.0"\n[build-system]\nrequires=["hatchling"]\n'
        'build-backend="hatchling.build"\n[tool.hatch.build.targets.wheel]\npackages=["shop"]\n'
    )
    (dest / ".gitignore").write_text(".venv/\n__pycache__/\n")
    repo = Repo.init(dest)
    repo.git.add(A=True)
    repo.index.commit("fixture")
    return dest


async def run_case(prompt: str) -> dict:
    path = _fixture_copy()
    started = time.monotonic()
    async with open_persistence(in_memory=True) as p:
        snap = await start_run(p, prompt, WorkflowConfig(local_path=str(path)))
        while snap.interrupt:  # auto-approve spec and plan
            snap = await resume_run(p, snap.thread_id, approved=True)
    tasks = snap.values.get("tasks", [])
    return {
        "prompt": prompt,
        "status": snap.status,
        "tasks": len(tasks),
        "completed": sum(t.status == "completed" for t in tasks),
        "gate_failures": sum(t.gate_failures for t in tasks),
        "review_cycles": sum(t.review_cycles for t in tasks),
        "seconds": round(time.monotonic() - started),
    }


async def main() -> None:
    results = [await run_case(c) for c in CASES]
    for r in results:
        print(json.dumps(r))
    total = sum(r["tasks"] for r in results) or 1
    print(f"task completion rate: {sum(r['completed'] for r in results) / total:.2f}")


if __name__ == "__main__":
    asyncio.run(main())

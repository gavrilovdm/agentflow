"""Intake on real prompts: does the card get the right type, and does a vague one get questions?

    uv run python -m evals.intake_eval

Runs the real extraction model (ORCHESTRATOR_MODEL) and the intake filter on each prompt in
datasets/intake_prompts.json, as if it were the body of a fresh card. A case passes when the
verdict is one of its expected ones and, for a ready card, the type matches. Nothing is
written to the board.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agentflow.board import Card, Thread
from agentflow.stages.intake import extract, judge

DATASET = Path(__file__).parent / "datasets" / "intake_prompts.json"
REPO = "eval/repo"


async def main() -> None:
    cases = json.loads(DATASET.read_text())
    card = Card(item_id="eval", issue_id="eval", number=0, repo=REPO, url="", title="")
    passed = 0
    for case in cases:
        draft = await extract(Thread(title=case["prompt"][:80], body=case["prompt"], author="eval"))
        v = judge(draft, card, [], {REPO})
        ok = v.kind in case["expect"] and (v.kind != "ready" or draft.type == case["type"])
        passed += ok
        detail = "; ".join(v.questions or v.reasons) or f"{len(draft.definition_of_done)} DoD"
        print(f"{'PASS' if ok else 'FAIL'}  {v.kind:6} {draft.type:12} {draft.type_confidence:.2f}  "
              f"{case['prompt'][:50]!r}  {detail[:120]}")  # fmt: skip
    print(f"\n{passed}/{len(cases)} passed")


if __name__ == "__main__":
    asyncio.run(main())

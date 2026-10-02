"""LLM-as-judge evaluation of the orchestrator's specs, logged to LangSmith.

    LANGSMITH_API_KEY=... ANTHROPIC_API_KEY=... uv run python -m evals.spec_eval

Each prompt is turned into a spec grounded in the fixture repo; a judge model scores
it against a rubric. "Make it faster" is deliberately vague: a good spec pins it down
with measurable criteria instead of echoing it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import Client, aevaluate
from pydantic import BaseModel, Field

from agentflow.agents.orchestrator import generate_spec
from agentflow.config import get_settings
from agentflow.models import structured
from agentflow.rag.embeddings import get_embeddings
from agentflow.rag.indexer import index_repo
from agentflow.rag.retriever import HybridRetriever, pack_context
from agentflow.rag.store import InMemoryStore

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "tests" / "fixtures" / "sample_repo"
DATASET = "agentflow-spec-prompts"


class Judgement(BaseModel):
    testable: int = Field(ge=1, le=5, description="Are acceptance criteria concrete and verifiable?")
    grounded: int = Field(ge=1, le=5, description="Does it reuse the repo's real modules, names and conventions?")
    scoped: int = Field(ge=1, le=5, description="Is out-of-scope explicit and the scope appropriately small?")
    reasoning: str


JUDGE = """You grade software specifications. Score each dimension 1-5 (5 = excellent).
testable: every acceptance criterion could be turned into an automated test.
grounded: the spec builds on the repository's existing modules and conventions shown in the context.
scoped: the spec states what is out of scope and does not balloon beyond the request."""


async def main() -> None:
    store, emb = InMemoryStore(), get_embeddings()
    await index_repo(REPO, "spec-eval", store, emb)
    retriever = HybridRetriever(store, emb, "spec-eval")

    client = Client()
    if not client.has_dataset(dataset_name=DATASET):
        ds = client.create_dataset(DATASET)
        prompts = json.loads((Path(__file__).parent / "datasets" / "spec_prompts.json").read_text())
        client.create_examples(dataset_id=ds.id, inputs=prompts)

    async def target(inputs: dict) -> dict:
        context = pack_context(await retriever.search(inputs["prompt"], k=8), budget_tokens=4000)
        spec = await generate_spec(inputs["prompt"], context)
        return {"spec": spec.model_dump(), "context": context}

    judge = structured(get_settings().reviewer_model, Judgement, tool_name="grade_spec")

    async def rubric(inputs: dict, outputs: dict) -> list[dict]:
        j: Judgement = await judge.ainvoke(
            [
                SystemMessage(JUDGE),
                HumanMessage(
                    f"## Request\n{inputs['prompt']}\n\n## Repository context\n{outputs['context']}\n\n"
                    f"## Spec\n```json\n{json.dumps(outputs['spec'], indent=2)}\n```"
                ),
            ]
        )
        return [
            {"key": "testable", "score": j.testable / 5, "comment": j.reasoning},
            {"key": "grounded", "score": j.grounded / 5},
            {"key": "scoped", "score": j.scoped / 5},
        ]

    def has_criteria(outputs: dict) -> dict:
        return {"key": "has_acceptance_criteria", "score": float(len(outputs["spec"]["acceptance_criteria"]) >= 2)}

    results = await aevaluate(
        target, data=DATASET, evaluators=[rubric, has_criteria], experiment_prefix="spec", max_concurrency=2
    )
    print(f"experiment: {results.experiment_name}")


if __name__ == "__main__":
    asyncio.run(main())

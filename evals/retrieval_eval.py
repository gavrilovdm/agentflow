"""Retrieval quality on a golden query → file set, with a dense / lexical / hybrid ablation.

    uv run python -m evals.retrieval_eval                 # offline (hash embeddings: dense ≈ random)
    VOYAGE_API_KEY=... uv run python -m evals.retrieval_eval --min-recall 0.9
    LANGSMITH_API_KEY=... uv run python -m evals.retrieval_eval --langsmith   # log as an experiment

Exits non-zero if hybrid recall@5 is below --min-recall, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agentflow.rag.embeddings import get_embeddings
from agentflow.rag.indexer import index_repo
from agentflow.rag.retriever import HybridRetriever, rrf_merge
from agentflow.rag.store import Hit, InMemoryStore

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "src"  # the eval corpus is agentflow's own source tree
GOLDEN = Path(__file__).parent / "datasets" / "retrieval_golden.json"
K = 5


def _files(hits: list[Hit]) -> list[str]:
    seen: list[str] = []
    for h in hits:
        if h.path not in seen:
            seen.append(h.path)
    return seen


def recall_at_k(ranked: list[str], relevant: list[str], k: int = K) -> float:
    return len(set(ranked[:k]) & set(relevant)) / len(relevant)


def reciprocal_rank(ranked: list[str], relevant: list[str]) -> float:
    return next((1 / (i + 1) for i, p in enumerate(ranked) if p in relevant), 0.0)


async def run(min_recall: float, langsmith: bool) -> int:
    store, emb = InMemoryStore(), get_embeddings()
    await index_repo(REPO, "eval", store, emb)
    retriever = HybridRetriever(store, emb, "eval")
    golden = json.loads(GOLDEN.read_text())

    async def modes(query: str) -> dict[str, list[str]]:
        vec = await emb.aembed_query(query)
        dense = await store.search_vector("eval", vec, 20, None)
        lexical = await store.search_text("eval", query, 20, None)
        return {
            "dense": _files(dense),
            "lexical": _files(lexical),
            "hybrid": _files(rrf_merge([dense, lexical], 20)),
        }

    totals: dict[str, dict[str, float]] = {m: {"recall": 0.0, "mrr": 0.0} for m in ("dense", "lexical", "hybrid")}
    for case in golden:
        for mode, ranked in (await modes(case["query"])).items():
            totals[mode]["recall"] += recall_at_k(ranked, case["relevant"])
            totals[mode]["mrr"] += reciprocal_rank(ranked, case["relevant"])

    n = len(golden)
    print(f"{'mode':<10}{'recall@' + str(K):>10}{'MRR':>8}   (n={n}, embeddings={type(emb).__name__})")
    for mode, t in totals.items():
        print(f"{mode:<10}{t['recall'] / n:>10.3f}{t['mrr'] / n:>8.3f}")

    if langsmith:
        await _log_to_langsmith(golden, retriever)

    hybrid_recall = totals["hybrid"]["recall"] / n
    if hybrid_recall < min_recall:
        print(f"FAIL: hybrid recall@{K} {hybrid_recall:.3f} < {min_recall}", file=sys.stderr)
        return 1
    return 0


async def _log_to_langsmith(golden: list[dict], retriever: HybridRetriever) -> None:
    """Upload the dataset (idempotent) and record a LangSmith experiment for comparison over time."""
    from langsmith import Client, aevaluate

    client = Client()
    name = "agentflow-retrieval-golden"
    if not client.has_dataset(dataset_name=name):
        ds = client.create_dataset(name, description="query → relevant files in agentflow/src")
        client.create_examples(
            dataset_id=ds.id,
            inputs=[{"query": c["query"]} for c in golden],
            outputs=[{"relevant": c["relevant"]} for c in golden],
        )

    async def target(inputs: dict) -> dict:
        return {"files": _files(await retriever.search(inputs["query"], k=20))}

    def recall(outputs: dict, reference_outputs: dict) -> dict:
        return {"key": f"recall@{K}", "score": recall_at_k(outputs["files"], reference_outputs["relevant"])}

    def mrr(outputs: dict, reference_outputs: dict) -> dict:
        return {"key": "mrr", "score": reciprocal_rank(outputs["files"], reference_outputs["relevant"])}

    await aevaluate(target, data=name, evaluators=[recall, mrr], experiment_prefix="hybrid-rrf")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-recall", type=float, default=0.0)
    ap.add_argument("--langsmith", action="store_true")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args.min_recall, args.langsmith)))


if __name__ == "__main__":
    main()

"""Hybrid retrieval (dense + lexical, fused with Reciprocal Rank Fusion) and
token-budgeted context packing.

Dense search finds code by meaning ("where are users persisted"); lexical search
finds exact identifiers (`resolve_in_workspace`) that embeddings blur. RRF merges
the two rankings without having to calibrate their incompatible scores.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import tiktoken
from langchain_core.embeddings import Embeddings

from agentflow.rag.store import ChunkStore, Hit

RRF_K = 60
_enc = tiktoken.get_encoding("cl100k_base")


def rrf_merge(rankings: list[list[Hit]], k: int) -> list[Hit]:
    scores: dict[str, float] = defaultdict(float)
    by_id: dict[str, Hit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking):
            scores[hit.id] += 1.0 / (RRF_K + rank + 1)
            by_id.setdefault(hit.id, hit)
    fused = sorted(by_id.values(), key=lambda h: -scores[h.id])
    for h in fused:
        h.score = scores[h.id]
    return fused[:k]


@dataclass
class HybridRetriever:
    store: ChunkStore
    embeddings: Embeddings
    repo: str
    candidates: int = 20

    async def search(self, query: str, k: int = 8, kind: str | None = None) -> list[Hit]:
        vector = await self.embeddings.aembed_query(query)
        dense = await self.store.search_vector(self.repo, vector, self.candidates, kind)
        lexical = await self.store.search_text(self.repo, query, self.candidates, kind)
        return rrf_merge([dense, lexical], k)


def count_tokens(text: str) -> int:
    return len(_enc.encode(text, disallowed_special=()))


def pack_context(hits: list[Hit], budget_tokens: int = 6000) -> str:
    """Render hits as fenced snippets, best first, until the budget is spent.
    Adjacent chunks of the same file are merged so the model sees contiguous code."""
    by_path: dict[str, list[Hit]] = defaultdict(list)
    order: list[str] = []
    for h in hits:
        if h.path not in by_path:
            order.append(h.path)
        by_path[h.path].append(h)

    out: list[str] = []
    used = 0
    for path in order:
        for h in sorted(by_path[path], key=lambda x: x.start_line):
            block = f"### {path} (lines {h.start_line}-{h.end_line})\n```\n{h.content}\n```"
            cost = count_tokens(block)
            if used + cost > budget_tokens:
                return "\n\n".join(out)
            out.append(block)
            used += cost
    return "\n\n".join(out)

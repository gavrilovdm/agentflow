"""Embedding model selection. Voyage `voyage-code-3` is trained on code and uses
separate document/query input types, which matters for asymmetric code search."""

from __future__ import annotations

from langchain_core.embeddings import DeterministicFakeEmbedding, Embeddings

from agentflow.config import get_settings


def get_embeddings() -> Embeddings:
    s = get_settings()
    if s.voyage_api_key:
        from langchain_voyageai import VoyageAIEmbeddings

        return VoyageAIEmbeddings(model=s.embedding_model, api_key=s.voyage_api_key, batch_size=64)  # type: ignore[arg-type]
    # No key: deterministic hash vectors keep the pipeline runnable offline. Dense
    # scores are meaningless then, but hybrid search still works through FTS.
    return DeterministicFakeEmbedding(size=s.embedding_dim)

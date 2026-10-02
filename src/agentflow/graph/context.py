"""Runtime dependencies injected into every node via LangGraph's `context`.

Keeps nodes free of globals: tests pass in-memory stores and fake embeddings; the
API/worker pass pgvector and Voyage. Context is not checkpointed, so a resumed run
simply receives a fresh one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.embeddings import Embeddings

from agentflow.config import WorkflowConfig
from agentflow.rag.retriever import HybridRetriever
from agentflow.rag.store import ChunkStore


@dataclass
class Deps:
    chunk_store: ChunkStore
    embeddings: Embeddings
    config: WorkflowConfig = field(default_factory=WorkflowConfig)

    def retriever(self, repo_id: str) -> HybridRetriever:
        return HybridRetriever(self.chunk_store, self.embeddings, repo_id)

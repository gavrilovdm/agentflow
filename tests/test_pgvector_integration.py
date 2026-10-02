"""Runs against a real pgvector Postgres: `docker compose up -d postgres` then
`uv run pytest -m integration`."""

import os
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from agentflow.rag.indexer import index_repo
from agentflow.rag.retriever import HybridRetriever
from agentflow.rag.store import PgVectorStore

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"
DSN = os.environ.get("DATABASE_URL", "postgresql://agentflow:agentflow@localhost:5432/agentflow")


async def test_pgvector_roundtrip():
    store = PgVectorStore(DSN, dim=32, table="code_chunks_test")
    await store.setup()
    repo = "test/shop-integration"
    await store.delete_paths(repo, list(await store.file_hashes(repo)))
    emb = DeterministicFakeEmbedding(size=32)
    stats = await index_repo(FIXTURE, repo, store, emb)
    assert stats.files_embedded >= 5
    hits = await HybridRetriever(store, emb, repo).search("hash_password login", k=3)
    assert hits[0].path == "shop/auth.py"

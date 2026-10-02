"""Incremental repo indexer: only files whose content hash changed are re-embedded.

Runs once when a workspace is prepared and again after every completed task, so a
later task can retrieve code an earlier task just wrote.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from langchain_core.embeddings import Embeddings

from agentflow.rag.chunking import Chunk, list_files, read_and_chunk
from agentflow.rag.store import ChunkStore

log = logging.getLogger(__name__)
EMBED_BATCH = 64


@dataclass
class IndexStats:
    files_seen: int = 0
    files_embedded: int = 0
    files_deleted: int = 0
    chunks_embedded: int = 0


async def index_repo(root: Path, repo: str, store: ChunkStore, embeddings: Embeddings) -> IndexStats:
    stats = IndexStats()
    stored = await store.file_hashes(repo)
    current: dict[str, tuple[str, list[Chunk]]] = {}
    for rel in list_files(root):
        result = read_and_chunk(root, rel)
        if result:
            current[rel] = result
    stats.files_seen = len(current)

    gone = [p for p in stored if p not in current]
    changed = [p for p, (digest, _) in current.items() if stored.get(p) != digest]
    # Changed files are deleted first: a file that shrank leaves stale tail chunks otherwise.
    await store.delete_paths(repo, gone + changed)
    stats.files_deleted = len(gone)

    pending = [ch for p in changed for ch in current[p][1]]
    for i in range(0, len(pending), EMBED_BATCH):
        batch = pending[i : i + EMBED_BATCH]
        vectors = await embeddings.aembed_documents([c.embedding_text() for c in batch])
        await store.upsert(repo, batch, vectors)
    stats.files_embedded = len(changed)
    stats.chunks_embedded = len(pending)
    log.info("indexed %s: %s", repo, stats)
    return stats

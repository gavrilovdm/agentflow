"""Chunk storage with two search modes: dense (pgvector cosine) and lexical (Postgres FTS).

A hand-written table rather than a generic vector-store wrapper, because hybrid
search needs the full-text index next to the embedding, and incremental indexing
needs per-file hashes — neither of which a generic wrapper exposes.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from agentflow.rag.chunking import Chunk


@dataclass
class Hit:
    path: str
    index: int
    content: str
    start_line: int
    end_line: int
    kind: str
    score: float = 0.0

    @property
    def id(self) -> str:
        return f"{self.path}#{self.index}"


class ChunkStore(Protocol):
    async def setup(self) -> None: ...
    async def file_hashes(self, repo: str) -> dict[str, str]: ...
    async def delete_paths(self, repo: str, paths: list[str]) -> None: ...
    async def upsert(self, repo: str, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...
    async def search_vector(self, repo: str, vector: list[float], k: int, kind: str | None) -> list[Hit]: ...
    async def search_text(self, repo: str, query: str, k: int, kind: str | None) -> list[Hit]: ...


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


class PgVectorStore:
    def __init__(self, dsn: str, dim: int = 1024, table: str = "code_chunks"):
        if not table.isidentifier():
            raise ValueError(f"bad table name {table!r}")
        self.dsn = dsn
        self.dim = dim
        self.table = table

    async def _conn(self) -> AsyncConnection[Any]:
        return await AsyncConnection.connect(self.dsn, autocommit=True, row_factory=dict_row)  # type: ignore[arg-type]

    async def setup(self) -> None:
        async with await self._conn() as c:
            await c.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await c.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    repo        text NOT NULL,
                    path        text NOT NULL,
                    chunk_index int  NOT NULL,
                    kind        text NOT NULL,
                    content     text NOT NULL,
                    start_line  int  NOT NULL,
                    end_line    int  NOT NULL,
                    file_hash   text NOT NULL,
                    embedding   vector({self.dim}) NOT NULL,
                    tsv tsvector GENERATED ALWAYS AS (
                        to_tsvector('english', regexp_replace(path || ' ' || content, '[_./-]', ' ', 'g'))
                    ) STORED,
                    PRIMARY KEY (repo, path, chunk_index)
                )"""
            )
            await c.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table}_hnsw ON {self.table} USING hnsw (embedding vector_cosine_ops)"
            )
            await c.execute(f"CREATE INDEX IF NOT EXISTS {self.table}_tsv ON {self.table} USING gin (tsv)")

    async def file_hashes(self, repo: str) -> dict[str, str]:
        async with await self._conn() as c:
            rows = await (
                await c.execute(f"SELECT DISTINCT path, file_hash FROM {self.table} WHERE repo = %s", (repo,))
            ).fetchall()
        return {r["path"]: r["file_hash"] for r in rows}  # type: ignore[call-overload]

    async def delete_paths(self, repo: str, paths: list[str]) -> None:
        if not paths:
            return
        async with await self._conn() as c:
            await c.execute(f"DELETE FROM {self.table} WHERE repo = %s AND path = ANY(%s)", (repo, paths))

    async def upsert(self, repo: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if not chunks:
            return
        async with await self._conn() as c, c.cursor() as cur:
            await cur.executemany(
                f"""INSERT INTO {self.table}
                   (repo, path, chunk_index, kind, content, start_line, end_line, file_hash, embedding)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                   ON CONFLICT (repo, path, chunk_index) DO UPDATE SET
                     kind=EXCLUDED.kind, content=EXCLUDED.content, start_line=EXCLUDED.start_line,
                     end_line=EXCLUDED.end_line, file_hash=EXCLUDED.file_hash, embedding=EXCLUDED.embedding""",
                [
                    (repo, ch.path, ch.index, ch.kind, ch.content, ch.start_line, ch.end_line, ch.file_hash, _vec(v))
                    for ch, v in zip(chunks, vectors, strict=True)
                ],
            )

    async def search_vector(self, repo: str, vector: list[float], k: int, kind: str | None) -> list[Hit]:
        async with await self._conn() as c:
            rows = await (
                await c.execute(
                    f"""SELECT path, chunk_index, content, start_line, end_line, kind,
                              1 - (embedding <=> %s::vector) AS score
                       FROM {self.table} WHERE repo = %s AND (%s::text IS NULL OR kind = %s)
                       ORDER BY embedding <=> %s::vector LIMIT %s""",
                    (_vec(vector), repo, kind, kind, _vec(vector), k),
                )
            ).fetchall()
        return [_hit(r) for r in rows]

    async def search_text(self, repo: str, query: str, k: int, kind: str | None) -> list[Hit]:
        terms = " | ".join(_tokens(query))
        if not terms:
            return []
        async with await self._conn() as c:
            rows = await (
                await c.execute(
                    f"""SELECT path, chunk_index, content, start_line, end_line, kind,
                              ts_rank_cd(tsv, q) AS score
                       FROM {self.table}, to_tsquery('english', %s) q
                       WHERE repo = %s AND tsv @@ q AND (%s::text IS NULL OR kind = %s)
                       ORDER BY score DESC LIMIT %s""",
                    (terms, repo, kind, kind, k),
                )
            ).fetchall()
        return [_hit(r) for r in rows]


def _hit(r: Any) -> Hit:
    return Hit(r["path"], r["chunk_index"], r["content"], r["start_line"], r["end_line"], r["kind"], float(r["score"]))


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def _tokens(text: str) -> list[str]:
    """Identifier-aware tokens: split snake_case, camelCase and paths, then stem lightly
    so "passwords hashed" meets `password_hash`."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [_stem(t.lower()) for t in re.findall(r"[A-Za-z0-9]+", text) if len(t) > 1]


class InMemoryStore:
    """Same contract without Postgres — for tests, evals and quick local runs."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, int], tuple[Chunk, list[float]]] = {}

    async def setup(self) -> None:
        return None

    async def file_hashes(self, repo: str) -> dict[str, str]:
        return {ch.path: ch.file_hash for (r, _, _), (ch, _) in self.rows.items() if r == repo}

    async def delete_paths(self, repo: str, paths: list[str]) -> None:
        drop = set(paths)
        self.rows = {key: v for key, v in self.rows.items() if not (key[0] == repo and key[1] in drop)}

    async def upsert(self, repo: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        for ch, v in zip(chunks, vectors, strict=True):
            self.rows[(repo, ch.path, ch.index)] = (ch, v)

    def _scoped(self, repo: str, kind: str | None):
        return [(ch, v) for (r, _, _), (ch, v) in self.rows.items() if r == repo and (kind is None or ch.kind == kind)]

    async def search_vector(self, repo: str, vector: list[float], k: int, kind: str | None) -> list[Hit]:
        def cos(a: list[float], b: list[float]) -> float:
            na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
            return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb or 1)

        scored = sorted(((cos(vector, v), ch) for ch, v in self._scoped(repo, kind)), key=lambda t: -t[0])
        return [_from_chunk(ch, s) for s, ch in scored[:k]]

    async def search_text(self, repo: str, query: str, k: int, kind: str | None) -> list[Hit]:
        q = set(_tokens(query))
        scored = []
        for ch, _ in self._scoped(repo, kind):
            counts = Counter(_tokens(ch.path + " " + ch.content))
            score = sum(1 + math.log(counts[t]) for t in q if counts[t])
            if score:
                scored.append((score, ch))
        scored.sort(key=lambda t: -t[0])
        return [_from_chunk(ch, s) for s, ch in scored[:k]]


def _from_chunk(ch: Chunk, score: float) -> Hit:
    return Hit(ch.path, ch.index, ch.content, ch.start_line, ch.end_line, ch.kind, score)

"""Code-aware chunking with line ranges and a contextual header per chunk.

Splitting on language boundaries (class/def/function) keeps a chunk semantically
whole; the `path` header is embedded with the body so a query like "where is auth
configured" can match on the file name even when the body never says "auth".
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 150
MAX_FILE_BYTES = 200_000

LANGUAGE_BY_EXT: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".ts": Language.TS,
    ".tsx": Language.TS,
    ".js": Language.JS,
    ".jsx": Language.JS,
    ".go": Language.GO,
    ".rs": Language.RUST,
    ".java": Language.JAVA,
    ".md": Language.MARKDOWN,
}
TEXT_EXTS = {".json", ".toml", ".yaml", ".yml", ".txt", ".cfg", ".ini", ".sql", ".sh", ".css", ".html"}
DOC_EXTS = {".md", ".rst", ".txt"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".mypy_cache", ".next"}
SKIP_FILES = {"package-lock.json", "uv.lock", "poetry.lock", "yarn.lock", "pnpm-lock.yaml"}


@dataclass
class Chunk:
    path: str
    index: int
    content: str
    start_line: int
    end_line: int
    kind: str  # "code" | "doc"
    file_hash: str
    metadata: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.path}#{self.index}"

    def embedding_text(self) -> str:
        return f"File: {self.path} (lines {self.start_line}-{self.end_line})\n\n{self.content}"


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def list_files(root: Path) -> list[str]:
    """Repo-relative paths worth indexing. Uses git when available so .gitignore holds."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        out = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
    keep = []
    for rel in out:
        p = Path(rel)
        if any(part in SKIP_DIRS for part in p.parts) or p.name in SKIP_FILES:
            continue
        if p.suffix in LANGUAGE_BY_EXT or p.suffix in TEXT_EXTS:
            keep.append(rel)
    return sorted(keep)


def _splitter(suffix: str) -> RecursiveCharacterTextSplitter:
    lang = LANGUAGE_BY_EXT.get(suffix)
    if lang is not None:
        return RecursiveCharacterTextSplitter.from_language(lang, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    return RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)


def chunk_file(path: str, text: str, digest: str) -> list[Chunk]:
    suffix = Path(path).suffix
    kind = "doc" if suffix in DOC_EXTS else "code"
    pieces = _splitter(suffix).split_text(text)
    chunks: list[Chunk] = []
    cursor = 0
    for i, piece in enumerate(pieces):
        # Locate each piece to recover its line range; overlap means it may start
        # before the previous piece ended, so search from a little behind the cursor.
        pos = text.find(piece, max(0, cursor - CHUNK_OVERLAP * 2))
        if pos == -1:
            pos = cursor
        start = text.count("\n", 0, pos) + 1
        end = start + piece.count("\n")
        cursor = pos + len(piece)
        chunks.append(Chunk(path, i, piece, start, end, kind, digest))
    return chunks


def read_and_chunk(root: Path, rel: str) -> tuple[str, list[Chunk]] | None:
    data = (root / rel).read_bytes()
    if len(data) > MAX_FILE_BYTES or b"\x00" in data[:4096]:
        return None
    digest = file_hash(data)
    return digest, chunk_file(rel, data.decode("utf-8", errors="replace"), digest)

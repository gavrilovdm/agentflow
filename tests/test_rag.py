from pathlib import Path

from langchain_core.embeddings import DeterministicFakeEmbedding

from agentflow.rag.chunking import chunk_file
from agentflow.rag.indexer import index_repo
from agentflow.rag.retriever import HybridRetriever, pack_context, rrf_merge
from agentflow.rag.store import Hit, InMemoryStore

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def test_chunk_line_ranges_cover_source():
    text = "\n".join(f"def f{i}():\n    return {i}\n" for i in range(200))
    chunks = chunk_file("m.py", text, "h")
    assert len(chunks) > 1
    assert chunks[0].start_line == 1
    for ch in chunks:
        first = text.splitlines()[ch.start_line - 1]
        assert ch.content.splitlines()[0] == first


def test_rrf_rewards_agreement():
    a = Hit("a.py", 0, "", 1, 1, "code")
    b = Hit("b.py", 0, "", 1, 1, "code")
    c = Hit("c.py", 0, "", 1, 1, "code")
    fused = rrf_merge([[a, b, c], [b, c, a]], k=3)
    assert fused[0].id == "b.py#0"


async def test_incremental_index_only_reembeds_changed(tmp_path: Path):
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")
    (tmp_path / "b.py").write_text("def beta():\n    return 2\n")
    store, emb = InMemoryStore(), DeterministicFakeEmbedding(size=32)
    first = await index_repo(tmp_path, "r", store, emb)
    assert first.files_embedded == 2
    second = await index_repo(tmp_path, "r", store, emb)
    assert second.files_embedded == 0
    (tmp_path / "b.py").write_text("def beta():\n    return 3\n")
    (tmp_path / "a.py").unlink()
    third = await index_repo(tmp_path, "r", store, emb)
    assert (third.files_embedded, third.files_deleted) == (1, 1)
    assert set(await store.file_hashes("r")) == {"b.py"}


async def test_hybrid_finds_identifier_and_packs_within_budget():
    store, emb = InMemoryStore(), DeterministicFakeEmbedding(size=32)
    await index_repo(FIXTURE, "shop", store, emb)
    retriever = HybridRetriever(store, emb, "shop")
    hits = await retriever.search("get_user_by_email", k=3, kind="code")
    assert hits[0].path == "shop/users.py"
    packed = pack_context(hits, budget_tokens=200)
    assert packed.startswith("### shop/users.py")

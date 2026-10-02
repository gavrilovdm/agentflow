"""RAG exposed as tools, so agents decide *when* and *what* to retrieve."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from agentflow.rag.retriever import HybridRetriever, pack_context


def make_search_tools(retriever: HybridRetriever) -> list[BaseTool]:
    @tool
    async def search_codebase(query: str) -> str:
        """Semantic + keyword search over the repository's source code. Use it to find
        existing functions, classes, call sites and patterns before writing code.
        Query with a description ("where are HTTP errors mapped to responses") or an
        identifier ("resolve_in_workspace")."""
        hits = await retriever.search(query, k=8, kind="code")
        return pack_context(hits, budget_tokens=4000) or "No matching code found."

    @tool
    async def search_docs(query: str) -> str:
        """Search the repository's documentation (README, conventions, ADRs, specs)."""
        hits = await retriever.search(query, k=5, kind="doc")
        return pack_context(hits, budget_tokens=3000) or "No matching documentation found."

    return [search_codebase, search_docs]

"""Web tools for the research agent: a search and a page reader.

The model's own server-side web search is not reachable here (through cursor-proxy it
answers "credit balance is too low"), so the search is DuckDuckGo's HTML endpoint, which
needs no key. A page comes back as plain text, cut to a size a prompt can hold.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from langchain_core.tools import BaseTool, tool

SEARCH_URL = "https://html.duckduckgo.com/html/"
HEADERS = {"User-Agent": "Mozilla/5.0 (agentflow research)"}
MAX_PAGE_CHARS = 6000
RESULT = re.compile(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', re.S)


def _clean(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _target(href: str) -> str:
    """DuckDuckGo wraps results in a redirect (/l/?uddg=<url>); the page itself is the source."""
    if "uddg=" in href:
        return unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
    return href if href.startswith("http") else "https:" + href


def parse_results(page: str, limit: int = 8) -> list[tuple[str, str, str]]:
    return [(_target(h), _clean(t), _clean(s)) for h, t, s in RESULT.findall(page)[:limit]]


def page_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", _clean(raw))[:MAX_PAGE_CHARS]


def make_web_tools(client: httpx.Client | None = None) -> list[BaseTool]:
    http = client or httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True)

    @tool
    def web_search(query: str) -> str:
        """Search the web. Returns up to 8 results as `url — title: snippet`, one per line."""
        try:
            resp = http.post(SEARCH_URL, data={"q": query})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return f"search failed: {exc}"
        results = parse_results(resp.text)
        return "\n".join(f"{u} — {t}: {s}" for u, t, s in results) or "no results"

    @tool
    def fetch_url(url: str) -> str:
        """Read a web page as plain text (the first few thousand characters)."""
        try:
            resp = http.get(url)
        except httpx.HTTPError as exc:
            return f"fetch failed: {exc}"
        if resp.status_code >= 400:
            return f"fetch failed: HTTP {resp.status_code}"
        return page_text(resp.text)

    return [web_search, fetch_url]

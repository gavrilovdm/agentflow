# agentflow guide

An interactive companion to agentflow: a replay of a real run, the core ideas with something
to try for each, use cases, and the bugs the first live runs found.

```bash
pnpm install
pnpm dev            # http://localhost:5173
```

**Real data.** `src/data/*.json` is captured from actual runs by
`uv run python scripts/export_guide_data.py`:

- the compiled graph;
- Postgres checkpoint histories;
- LangSmith LLM and tool calls;
- the PR from GitHub;
- retrieval rankings over this repo.

**Live mode.** If the agentflow API is running (`docker compose up`, port 8010), the RAG
playground queries the real vector index. Run `export_guide_data.py --index-self` once to
index this repository.

**Graph zoom.** The replay graph zooms with pinch or Ctrl+scroll, pans by drag, and has
+ / − / fit / fullscreen buttons in its bottom-left corner. Plain scroll still scrolls the page.

The routing simulator is a TypeScript port of `graph/routing.py`. `pnpm test` runs the same
cases as `tests/test_routing.py`.

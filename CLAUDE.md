# agentflow — working rules

## Workflow for every change
1. Branch from `main`: `feat/<topic>` or `fix/<topic>`. Never commit to `main` directly.
2. Change code **and** the docs that describe it in the same branch. A Stop hook
   (`.claude/hooks/docs_guard.py`) blocks ending a session when code changed but no docs did.
3. Before pushing, run: `uv run ruff check src tests evals scripts && uv run mypy src && uv run pytest -q`.
   If `guide/` changed: `cd guide && pnpm test && pnpm build`.
4. Open a PR with `gh pr create`. CI (lint, types, tests, pgvector integration, retrieval eval,
   helm/terraform, docker build) must be green before merge. `main` is protected.

## Where docs live
| Code | Docs to update |
|---|---|
| `src/agentflow/graph/` | README diagram + design notes; `guide/src/lib/content.ts` (NODES); `guide/src/lib/routing.ts` if routing changed (parity test) |
| `src/agentflow/agents/`, `schemas.py` | README requirements table; guide NODES / INTERVIEW |
| `src/agentflow/rag/` | README "Hybrid retrieval"; guide RAG text |
| `src/agentflow/gate/` | README "Gate per language" |
| `src/agentflow/api/`, `worker.py`, `mcp_server.py` | README "As a service"; guide USE_CASES |
| `evals/` | README "Evals" |
| Real-run data in the guide | re-run `uv run python scripts/export_guide_data.py` |

## Conventions
- Comments explain *why* (the failure a rule prevents), not what.
- Every bug fixed gets a regression test; live-run bugs also get a guide BUGS entry.

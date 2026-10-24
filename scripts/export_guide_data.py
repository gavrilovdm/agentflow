"""Capture real data for the guide app (guide/src/data/*.json).

Everything the guide shows as "real" comes from here: the graph structure compiled from
code, checkpoint histories of actual runs from Postgres, LLM/tool calls from LangSmith,
and the resulting PR from GitHub.

    docker compose up -d postgres            # checkpoints of past runs live here
    uv run python scripts/export_guide_data.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from agentflow.graph.build import build_graph
from agentflow.runner import _config, open_persistence

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "guide" / "src" / "data"
SUCCESS_RUN = "run-712dd72b01d9"
FAILED_RUNS = {
    "run-c129139ffa97": "workspace-permissions",
    "run-63d08392ff9a": "lint-wedge",
    "run-7a601399578e": "reviewer-loop",
    "run-b17b479a95ac": "crash-marked-failed",
}
SANDBOX = "gavrilovdm/agentflow-sandbox"


def dump(name: str, data: Any) -> None:
    (OUT / name).parent.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=1, default=str, ensure_ascii=False))
    print(f"wrote {name} ({(OUT / name).stat().st_size // 1024} KB)")


def jsonable(v: Any) -> Any:
    if hasattr(v, "model_dump"):
        return v.model_dump()
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [jsonable(x) for x in v]
    return v


# ─── graph ───────────────────────────────────────────────────────────────────


def export_graph() -> None:
    g = build_graph().get_graph()
    dump(
        "graph.json",
        {
            "nodes": [n for n in g.nodes],
            "edges": [{"source": e.source, "target": e.target, "conditional": e.conditional} for e in g.edges],
        },
    )


# ─── runs (checkpoint histories) ─────────────────────────────────────────────

SKIP_KEYS = {"run_config", "workspace"}


def _delta(before: dict, after: dict) -> dict:
    out = {}
    for k, v in after.items():
        if k in SKIP_KEYS or before.get(k) == v:
            continue
        out[k] = jsonable(v)
    return out


# Runs shown as scenarios in the guide's replay (all real, from Postgres checkpoints).
SCENARIO_RUNS = {
    "happy": SUCCESS_RUN,
    "escalate-skip": "run-d373c3156641",
    "escalate-replan": "run-474c62f24055",
}


def _steps(hist: list) -> list[dict]:
    """One entry per executed node: when it ran, what it changed, and — for a node that
    paused the run — the question it asked (interrupt payload, e.g. the escalation dossier)."""
    steps = []
    for prev, cur in zip(hist, hist[1:], strict=False):
        if not prev.next:
            continue
        step = {
            "node": prev.next[0],
            "started": prev.created_at,
            "ended": cur.created_at,
            "delta": _delta(prev.values or {}, cur.values or {}),
        }
        asked = [i.value for t in (prev.tasks or ()) for i in (t.interrupts or ())]
        if asked:
            step["interrupt"] = jsonable(asked[0])
        steps.append(step)
    return steps


async def export_runs() -> None:
    async with open_persistence() as p:

        async def history(thread: str) -> list:
            return list(reversed([s async for s in p.graph.aget_state_history(_config(thread))]))

        hist = await history(SUCCESS_RUN)
        final = jsonable({k: v for k, v in (hist[-1].values or {}).items() if k not in SKIP_KEYS})
        dump("run.json", {"thread_id": SUCCESS_RUN, "steps": _steps(hist), "final": final})

        for key, thread in SCENARIO_RUNS.items():
            h = await history(thread)
            fin = jsonable({k: v for k, v in (h[-1].values or {}).items() if k not in SKIP_KEYS})
            dump(
                f"runs/{key}.json", {"thread_id": thread, "steps": _steps(h), "final": fin, "calls": llm_calls(thread)}
            )

        failures = {}
        for thread, label in FAILED_RUNS.items():
            h = await history(thread)
            seq = []
            for prev, cur in zip(h, h[1:], strict=False):
                if not prev.next:
                    continue
                review = (cur.values or {}).get("review_results", {})
                tid = (cur.values or {}).get("current_task_id")
                verdict = None
                if prev.next[0] == "run_review" and tid in review and review[tid].cycles:
                    c = review[tid].cycles[-1]
                    verdict = {"verdict": c.verdict, "malfunction": c.reviewer_malfunction, "comment": c.comments[:240]}
                seq.append({"node": prev.next[0], "t": cur.created_at, "verdict": verdict})
            last = h[-1].values or {} if h else {}
            failures[label] = {
                "thread_id": thread,
                "steps": seq,
                "status": last.get("status"),
                "error": (last.get("error") or "")[:600],
                "tasks": [
                    {"id": t.id, "gate_failures": t.gate_failures, "review_cycles": t.review_cycles}
                    for t in last.get("tasks", [])
                ],
            }
        dump("failed_runs.json", failures)


# ─── LLM + tool calls (LangSmith) ────────────────────────────────────────────


def _msg(m: Any) -> dict:
    kw = m.get("kwargs", m) if isinstance(m, dict) else {}
    content = kw.get("content", "")
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return {
        "role": kw.get("type") or (m.get("id", ["", "", "", "?"])[-1] if isinstance(m, dict) else "?"),
        "content": str(content)[:1500],
        "tool_calls": [{"name": t["name"], "args": t.get("args")} for t in kw.get("tool_calls") or []],
    }


def _tool_text(out: Any) -> str:
    """Tool outputs are traced as serialized ToolMessages; keep just their content."""
    if isinstance(out, dict):
        out = out.get("content", out.get("kwargs", {}).get("content", out))
    return out if isinstance(out, str) else json.dumps(out, default=str)


def _langsmith():
    env = {k: v for k, v in dotenv_values(ROOT / ".env").items() if k.startswith("LANGSMITH") and v}
    if not env.get("LANGSMITH_API_KEY"):
        return None
    os.environ.update(env)
    from langsmith import Client

    return Client()


def llm_calls(thread: str) -> list[dict]:
    """Every model and tool call of one run, from its LangSmith traces."""
    c = _langsmith()
    if c is None:
        return []
    roots = list(
        c.list_runs(
            project_name="agentflow",
            is_root=True,
            filter=f'and(eq(metadata_key, "thread_id"), eq(metadata_value, "{thread}"))',
        )
    )
    calls = []
    for root in sorted(roots, key=lambda r: r.start_time):
        for r in sorted(c.list_runs(project_name="agentflow", trace_id=root.trace_id), key=lambda r: r.start_time):
            if r.run_type == "llm":
                gen = ((r.outputs or {}).get("generations") or [[{}]])[0][0]
                out = _msg(gen.get("message", {})) if isinstance(gen, dict) else {}
                msgs = (r.inputs or {}).get("messages") or [[]]
                calls.append(
                    {
                        "type": "llm",
                        "t": r.start_time,
                        "latency_s": round((r.end_time - r.start_time).total_seconds(), 1) if r.end_time else None,
                        "model": (r.extra or {}).get("metadata", {}).get("ls_model_name"),
                        "tags": [t for t in r.tags or [] if t != "agentflow" and not t.startswith("seq:")],
                        "tokens": r.total_tokens,
                        "input_tail": [_msg(m) for m in (msgs[0] if msgs and isinstance(msgs[0], list) else msgs)[-2:]],
                        "output": out,
                    }
                )
            elif r.run_type == "tool":
                calls.append(
                    {
                        "type": "tool",
                        "t": r.start_time,
                        "name": r.name,
                        "input": str((r.inputs or {}).get("input", r.inputs))[:400],
                        "output": _tool_text((r.outputs or {}).get("output", ""))[:800],
                    }
                )
    return calls


def export_llm_calls() -> None:
    calls = llm_calls(SUCCESS_RUN)
    dump("llm_calls.json", calls)


# ─── PR (GitHub) ─────────────────────────────────────────────────────────────


def export_pr() -> None:
    view = json.loads(
        subprocess.run(
            ["gh", "pr", "view", "1", "-R", SANDBOX, "--json", "number,title,url,body,commits,files,createdAt"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    diff = subprocess.run(["gh", "pr", "diff", "1", "-R", SANDBOX], capture_output=True, text=True, check=True).stdout
    view["diff"] = diff
    dump("pr.json", view)


async def main() -> None:
    export_graph()
    await export_runs()
    export_llm_calls()
    export_pr()


if __name__ == "__main__":
    asyncio.run(main())

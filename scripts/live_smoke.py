"""Live smoke test of the deployed service: start a run through the REST API,
auto-approve the spec and plan, and wait for the PR.

    docker compose up -d && uv run python scripts/live_smoke.py owner/sandbox-repo
"""

from __future__ import annotations

import sys
import time

import httpx

API = "http://localhost:8010"
PROMPT = (
    "Add shop.users.change_email(db, user_id, new_email): normalise to lowercase, raise EmailTaken if "
    "another user already has that email, raise UserNotFound for an unknown id. "
    "Add the Database support it needs."
)


def wait_for_api() -> None:
    for _ in range(60):
        try:
            if httpx.get(f"{API}/healthz").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    sys.exit("API did not come up")


def main(repo: str, timeout_s: int = 3600) -> int:
    wait_for_api()
    thread_id = httpx.post(f"{API}/runs", json={"prompt": PROMPT, "repo": repo}).json()["thread_id"]
    print("thread", thread_id, flush=True)
    start = time.time()
    answered: set[str] = set()  # checkpoint ids already approved
    while time.time() - start < timeout_s:
        time.sleep(10)
        resp = httpx.get(f"{API}/runs/{thread_id}")
        if resp.status_code == 404:  # queued, not started yet
            continue
        run = resp.json()
        elapsed = f"[{int(time.time() - start)}s]"
        pending = run["pending_approval"]
        if pending and (key := f"{pending['type']}:{len(answered)}") not in answered:
            if pending["type"] == "spec_approval":
                spec = pending["spec"]
                print(elapsed, f"SPEC v{spec['version']}: {spec['title']}", flush=True)
                for criterion in spec["acceptance_criteria"]:
                    print("   -", criterion, flush=True)
            else:
                print(elapsed, "PLAN:", flush=True)
                for t in pending["tasks"]:
                    print(f"   {t['id']} <- {t['depends_on']}: {t['title']}", flush=True)
            httpx.post(f"{API}/runs/{thread_id}/approval", json={"approved": True})
            answered.add(key)
            continue
        if run["status"] in ("completed", "failed"):
            print(elapsed, "FINAL status =", run["status"], flush=True)
            for t in run["tasks"]:
                print("  ", t["status"], t["id"], flush=True)
            print("failed:", run["failed_tasks"], "\nPR:", run["pull_request"], "\nerror:", run["error"], flush=True)
            return 0 if run["status"] == "completed" else 1
    print("TIMEOUT", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "gavrilovdm/agentflow-sandbox"))

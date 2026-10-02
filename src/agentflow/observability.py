"""JSON logs, Prometheus metrics and LangSmith trace metadata in one place."""

from __future__ import annotations

import json
import logging
import sys
import time

from prometheus_client import Counter, Histogram

RUNS_STARTED = Counter("agentflow_runs_started_total", "Workflow runs started", ["trigger"])
RUNS_FINISHED = Counter(
    "agentflow_runs_finished_total", "Runs reaching a terminal or paused state", ["status"]
)
APPROVALS = Counter("agentflow_approvals_total", "Human approval decisions", ["decision", "channel"])
JOB_SECONDS = Histogram(
    "agentflow_job_seconds", "Worker job wall time", ["job"], buckets=(1, 5, 30, 60, 300, 900, 1800, 3600, 7200)
)
HTTP_REQUESTS = Counter("agentflow_http_requests_total", "API requests", ["route", "status"])


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        data = getattr(record, "data", None)
        if data:
            payload["data"] = data
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO", json_logs: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

"""Telemetry of the board: one span and one durable row per stage job, tokens per LLM call.

Two sinks, because they answer different questions:

- OpenTelemetry (OTLP → the `otel` service in compose: Tempo, Prometheus, Grafana) for looking
  at a live card — which stage is slow, which LLM call inside it. Off unless
  OTEL_EXPORTER_OTLP_ENDPOINT is set.
- Postgres `stage_runs` for the measure → spec loop: the LGTM stack keeps days, not months,
  and an analysis needs every run since the start. `scripts/telemetry_report.py` reads it.

    uv run python scripts/telemetry_report.py     # the measurements table from stage_runs
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry import metrics, trace

log = logging.getLogger(__name__)

tracer = trace.get_tracer("agentflow")
meter = metrics.get_meter("agentflow")
stage_seconds = meter.create_histogram("agentflow.stage.duration", unit="s", description="One stage job")
llm_tokens = meter.create_counter("agentflow.llm.tokens", unit="{token}", description="Tokens per model call")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stage_runs (
    id            bigserial PRIMARY KEY,
    started_at    timestamptz NOT NULL,
    seconds       double precision NOT NULL,
    agent         text NOT NULL,
    repo          text NOT NULL,
    card          integer NOT NULL,
    card_type     text NOT NULL,
    column_from   text NOT NULL,
    attempts      integer NOT NULL,
    outcome       text NOT NULL,
    error         text,
    llm_calls     integer NOT NULL,
    input_tokens  integer NOT NULL,
    output_tokens integer NOT NULL,
    models        text[] NOT NULL,
    trace_id      text
)
"""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    models: set[str] = field(default_factory=set)


# The stage a model call belongs to: callbacks run deep inside graphs that know nothing of cards.
_usage: ContextVar[Usage | None] = ContextVar("agentflow_usage", default=None)
_agent: ContextVar[str] = ContextVar("agentflow_agent", default="run")


def setup_telemetry(service: str) -> None:
    """OTLP exporters when an endpoint is configured; the API's no-op otherwise."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", service)})
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tp)
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())])
    )


def _usage_of(response: Any) -> tuple[int, int, str]:
    """(input, output, model) from an LLMResult: usage_metadata on the message (Anthropic,
    OpenAI-compatible) or token_usage in llm_output (older OpenAI clients)."""
    i = o = 0
    for gens in response.generations or []:
        for g in gens:
            um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
            i += um.get("input_tokens", 0)
            o += um.get("output_tokens", 0)
    out = response.llm_output or {}
    if not (i or o):
        tu = out.get("token_usage") or out.get("usage") or {}
        i, o = (
            tu.get("prompt_tokens", tu.get("input_tokens", 0)),
            tu.get("completion_tokens", tu.get("output_tokens", 0)),
        )
    return int(i or 0), int(o or 0), str(out.get("model_name") or out.get("model") or "?")


class TokenCounter(BaseCallbackHandler):
    """Adds every model call's tokens to the running stage and to the OTel counter."""

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        i, o, model = _usage_of(response)
        agent = _agent.get()
        llm_tokens.add(i, {"model": model, "kind": "input", "agent": agent})
        llm_tokens.add(o, {"model": model, "kind": "output", "agent": agent})
        if (u := _usage.get()) is not None:
            u.calls += 1
            u.input_tokens += i
            u.output_tokens += o
            u.models.add(model)


TOKENS = TokenCounter()


@dataclass
class StageRun:
    agent: str
    repo: str
    card: int
    card_type: str
    column_from: str
    attempts: int
    outcome: str = "?"
    error: str | None = None
    usage: Usage = field(default_factory=Usage)


@contextlib.asynccontextmanager
async def stage(run: StageRun, database_url: str | None) -> AsyncIterator[StageRun]:
    """Span, metrics and a stage_runs row around one stage job. A failed write is logged,
    never raised: telemetry must not take down the board."""
    started, t0 = time.time(), time.monotonic()
    tok_u, tok_a = _usage.set(run.usage), _agent.set(run.agent)
    attrs: dict[str, str | int] = {"agent": run.agent, "repo": run.repo, "card": run.card, "card.type": run.card_type,
             "column": run.column_from, "attempts": run.attempts}  # fmt: skip
    with tracer.start_as_current_span(f"stage {run.agent}", attributes=attrs) as span:
        try:
            yield run
        except BaseException as exc:
            run.outcome, run.error = "error", f"{type(exc).__name__}: {exc}"[:2000]
            span.record_exception(exc)
            raise
        finally:
            _usage.reset(tok_u)
            _agent.reset(tok_a)
            seconds = time.monotonic() - t0
            span.set_attributes({"outcome": run.outcome, "llm.calls": run.usage.calls,
                                 "llm.input_tokens": run.usage.input_tokens,
                                 "llm.output_tokens": run.usage.output_tokens})  # fmt: skip
            stage_seconds.record(seconds, {"agent": run.agent, "outcome": run.outcome, "repo": run.repo})
            ctx = span.get_span_context()
            trace_id = f"{ctx.trace_id:032x}" if ctx.is_valid else None
            if database_url:
                try:
                    await record(database_url, run, started, seconds, trace_id)
                except Exception:
                    log.exception("stage_runs write failed")


async def record(database_url: str, run: StageRun, started: float, seconds: float, trace_id: str | None) -> None:
    import psycopg

    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as conn:
        await conn.execute(SCHEMA)
        await conn.execute(
            "INSERT INTO stage_runs (started_at, seconds, agent, repo, card, card_type, column_from, attempts,"
            " outcome, error, llm_calls, input_tokens, output_tokens, models, trace_id)"
            " VALUES (to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (started, seconds, run.agent, run.repo, run.card, run.card_type, run.column_from, run.attempts,
             run.outcome, run.error, run.usage.calls, run.usage.input_tokens, run.usage.output_tokens,
             sorted(run.usage.models), trace_id),
        )  # fmt: skip

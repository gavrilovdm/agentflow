"""Stage telemetry: tokens from model calls reach the stage they ran in, and a row lands in stage_runs."""

import uuid

import psycopg
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from agentflow import telemetry
from agentflow.config import get_settings


def _result(i, o, model="claude-x"):
    msg = AIMessage(content="ok", usage_metadata={"input_tokens": i, "output_tokens": o, "total_tokens": i + o})
    return LLMResult(generations=[[ChatGeneration(message=msg)]], llm_output={"model_name": model})


def _run(**kw):
    return telemetry.StageRun(agent="planner", repo="o/r", card=7, card_type="coding", column_from="Планируется",
                              attempts=0, **kw)  # fmt: skip


def test_usage_comes_from_the_message_or_from_llm_output():
    assert telemetry._usage_of(_result(10, 3)) == (10, 3, "claude-x")
    # OpenAI-compatible clients without usage_metadata (older cursor-proxy) report it in llm_output
    old = LLMResult(generations=[[]], llm_output={"token_usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    assert telemetry._usage_of(old) == (5, 2, "?")


async def test_tokens_add_up_inside_the_stage_and_not_outside():
    telemetry.TOKENS.on_llm_end(_result(100, 100))  # no stage running: counted by OTel only
    async with telemetry.stage(_run(), None) as run:
        telemetry.TOKENS.on_llm_end(_result(10, 3))
        telemetry.TOKENS.on_llm_end(_result(5, 1, "deepseek-y"))
    assert (run.usage.calls, run.usage.input_tokens, run.usage.output_tokens) == (2, 15, 4)
    assert run.usage.models == {"claude-x", "deepseek-y"}


async def test_a_crash_is_recorded_as_error_and_still_raised(monkeypatch):
    rows = []

    async def record(url, run, started, seconds, trace_id):
        rows.append(run)
        raise OSError("db down")  # a telemetry failure must not replace the stage's own error

    monkeypatch.setattr(telemetry, "record", record)
    with pytest.raises(ValueError, match="boom"):
        async with telemetry.stage(_run(), "postgresql://x"):
            raise ValueError("boom")
    assert rows[0].outcome == "error" and "ValueError: boom" in rows[0].error


@pytest.mark.integration
async def test_stage_run_lands_in_postgres():
    repo = f"t/{uuid.uuid4().hex[:8]}"
    async with telemetry.stage(_run(), get_settings().database_url) as run:
        run.repo = repo
        telemetry.TOKENS.on_llm_end(_result(10, 3))
        run.outcome = "ready"
    async with await psycopg.AsyncConnection.connect(get_settings().database_url) as conn:
        row = await (await conn.execute(
            "SELECT outcome, llm_calls, input_tokens, models FROM stage_runs WHERE repo = %s", (repo,)
        )).fetchone()  # fmt: skip
    assert row == ("ready", 1, 10, ["claude-x"])

"""Domain models shared by agents, graph nodes, API and MCP server."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

TaskStatus = Literal["pending", "coding", "completed", "failed"]
ReviewVerdict = Literal["approved", "changes_requested", "failed"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _split_lines(value: Any) -> Any:
    """Models occasionally return a bullet-delimited string where a list was asked for."""
    if isinstance(value, str):
        return [line.lstrip("-* ").strip() for line in value.splitlines() if line.strip()]
    return value


# ─── LLM output schemas (structured outputs) ────────────────────────────────


class SpecDraft(BaseModel):
    title: str = Field(description="Short feature/project title")
    goal: str = Field(description="What problem this solves and why")
    constraints: list[str] = Field(description="Tech stack, limitations, non-goals")
    acceptance_criteria: list[str] = Field(description="Observable, testable outcomes that define success")
    technical_notes: str = Field(description="Patterns to follow, integrations, key architectural notes")
    out_of_scope: list[str] = Field(description="What is explicitly NOT included")

    _coerce = field_validator("constraints", "acceptance_criteria", "out_of_scope", mode="before")(_split_lines)


class TaskDraft(BaseModel):
    id: str = Field(description="Unique slug, e.g. task-add-auth")
    title: str = Field(description="Short imperative title")
    description: str = Field(description="Detailed description of what needs to be built")
    target_files: list[str] = Field(description="File paths to create or modify, relative to repo root")
    definition_of_done: str = Field(description="Specific, observable definition of done")
    depends_on: list[str] = Field(default_factory=list, description="IDs of tasks that must finish first")


class TaskPlan(BaseModel):
    tasks: list[TaskDraft]


class ReviewDecision(BaseModel):
    verdict: Literal["approved", "changes_requested"]
    summary: str = Field(description="One-paragraph summary of the review")
    change_requests: list[str] = Field(
        default_factory=list, description="Specific changes required. Empty if approved."
    )

    @field_validator("change_requests", mode="before")
    @classmethod
    def _flatten(cls, value: Any) -> Any:
        # Asked for strings, models regularly answer with {file, issue} objects or a
        # JSON-ish string. A formatting quirk must not kill a run holding committed work.
        value = _split_lines(value)
        if isinstance(value, list):
            return [
                item
                if isinstance(item, str)
                else " — ".join(f"{k}: {v}" for k, v in item.items())
                if isinstance(item, dict)
                else str(item)
                for item in value
            ]
        return value


class FailureRuling(BaseModel):
    culprit: Literal["test", "code"] = Field(description="Which artifact is wrong")
    reasoning: str = Field(description="Two or three sentences; if the test is wrong, what it should expect")


class GeneratedTest(BaseModel):
    file_name: str = Field(description="File name without directory, e.g. test_task_add_auth.py")
    content: str = Field(description="Full test file content")
    coverage: list[str] = Field(description="Scenarios covered: happy path, error, edge cases")


# ─── Workflow domain objects ────────────────────────────────────────────────


class Spec(SpecDraft):
    id: str
    version: int = 1
    created_at: str = Field(default_factory=_now)


class Task(TaskDraft):
    status: TaskStatus = "pending"
    coder_fix_attempts: int = 0
    review_cycles: int = 0
    reviewer_malfunctions: int = 0
    gate_failures: int = 0


class CoderResult(BaseModel):
    task_id: str
    success: bool
    written_files: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class GateResult(BaseModel):
    passed: bool
    errors: dict[str, list[str]] = Field(default_factory=dict)  # check name → diagnostic lines
    summary: str = "All checks passed."


class ReviewCycle(BaseModel):
    verdict: ReviewVerdict
    comments: str
    change_requests: list[str] = Field(default_factory=list)
    diff: str = ""
    # The reviewer itself failed to produce a verdict; must not be charged to the task.
    reviewer_malfunction: bool = False


class ReviewResult(BaseModel):
    task_id: str
    approved: bool
    cycles: list[ReviewCycle] = Field(default_factory=list)


class PullRequest(BaseModel):
    number: int
    url: str
    title: str
    branch: str


class Stall(BaseModel):
    signature: str = ""
    repeats: int = 0


class ApprovalDecision(BaseModel):
    approved: bool
    feedback: str | None = None

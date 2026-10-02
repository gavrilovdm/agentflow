"""Acceptance-test generator — contract-first: each task's test is written before its code.

Tests written first used to deadlock: the generator had to invent names it could not see
(`addNote` where the spec said `add`). Each task now carries a binding `interface` from the
plan, so the test uses exactly the names the coder must implement. Assertions come from the
Definition of Done; the current code of the files being changed is shown so untouched
behaviour is not asserted away.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agentflow.config import get_settings
from agentflow.gate import ACCEPTANCE_TEST_DIR, Gate
from agentflow.models import structured
from agentflow.schemas import GeneratedTest, Spec, Task

log = logging.getLogger(__name__)
ATTEMPTS = 3

SYSTEM = """You are a senior test engineer writing the acceptance test for a task BEFORE it is implemented.

The test is the contract the coder will implement against. Derive every assertion from the task's
Definition of Done and the spec. Import and call ONLY the names, modules and signatures given in the
task's Interface — never invent others, because the coder will implement exactly that interface.
The test must fail now (the code does not exist yet) and pass once the task is done correctly; a test
that would already pass is worthless.

## Framework rules
{rules}"""

# Emitting a whole source file inside a JSON tool argument is the most fragile step in the
# pipeline: one bad escape makes the call unparseable. Retry with an explicit hint.
RETRY_HINT = HumanMessage(
    "Your previous response could not be parsed. Emit the tool call again and be strict about JSON "
    'escaping inside "content": backslashes as \\\\, newlines as \\n.'
)


async def generate_test(
    task: Task,
    spec: Spec,
    gate: Gate,
    workspace: Path,
    *,
    existing_files: dict[str, str] | None = None,
    previous_test: str | None = None,
    ruling: str | None = None,
    attempted_code: dict[str, str] | None = None,
) -> str:
    """Write the acceptance test into the workspace (before the code); returns its path.

    `existing_files`: current code of the files the task changes — behaviour to preserve.
    `previous_test` + `ruling`: a rewrite requested by the red check or the referee.
    `attempted_code`: what the coder wrote against the previous test (referee rewrites only);
    shown as evidence, never as the specification.
    """
    files = "\n\n".join(f"### {p}\n```\n{c}\n```" for p, c in (attempted_code or {}).items())
    body = (
        f"## Specification\n**Title:** {spec.title}\n**Goal:** {spec.goal}\n**Acceptance criteria:**\n"
        + "\n".join(f"- {c}" for c in spec.acceptance_criteria)
        + f"\n\n## Task\n**ID:** {task.id}\n**Title:** {task.title}\n**Description:** {task.description}\n"
        f"**Target files:** {', '.join(task.target_files)}\n**Definition of Done:** {task.definition_of_done}\n"
        + (f"**Interface (binding):**\n{task.interface}\n" if task.interface else "")
        + "\n"
        + (
            "## Current code of the files this task changes\n"
            "Existing behaviour shown here must stay as it is unless the task says otherwise; never assert a "
            "different return value or signature for an API the task does not change.\n\n"
            + "\n\n".join(f"### {p}\n```\n{c}\n```" for p, c in existing_files.items())
            + "\n\n"
            if existing_files
            else ""
        )
        + (
            "## Code the coder wrote against the previous test\n"
            "Evidence only — it may be wrong. Assert what the Definition of Done requires.\n\n"
            f"{files}\n\n"
            if files
            else ""
        )
        + (
            f"## Rewrite required\nThe previous test was judged incorrect:\n{ruling}\n\n"
            f"Previous test:\n```\n{previous_test}\n```\n"
            if ruling
            else ""
        )
    )
    messages = [SystemMessage(SYSTEM.format(rules=gate.test_prompt_rules())), HumanMessage(body)]
    runnable = structured(get_settings().test_generator_model, GeneratedTest)

    last_error: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            result: GeneratedTest = await runnable.ainvoke(
                messages if attempt == 1 else [*messages, RETRY_HINT],
                config={"tags": ["test-generator", f"task:{task.id}"]},
            )
            break
        except Exception as exc:  # noqa: BLE001 — parse errors surface as many types
            last_error = exc
            log.warning("test generation for %s failed (%s/%s): %s", task.id, attempt, ATTEMPTS, exc)
    else:
        raise RuntimeError(f"test generation failed for {task.id}") from last_error

    name = Path(result.file_name).name
    if not name.endswith(gate.test_file_suffix):
        name = Path(name).stem.split(".")[0] + gate.test_file_suffix
    if gate.language == "python" and not name.startswith("test_"):
        name = "test_" + name
    rel = f"{ACCEPTANCE_TEST_DIR}/{name}"
    (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
    (workspace / rel).write_text(result.content)
    return rel

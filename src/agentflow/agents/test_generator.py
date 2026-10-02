"""Acceptance-test generator.

Tests are written per task right *after* the implementation. Written first, the
generator had to invent names it could not yet see (`addNote` where the spec said
`add`), and the coder was stuck satisfying a test that contradicted its own task.
Assertions still come from the Definition of Done, not from the code — the code is
shown only so names, signatures and import paths line up.
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

SYSTEM = """You are a senior test engineer writing acceptance tests for a task that has just been implemented.

The implementation is NOT the specification. Derive every assertion from the task's Definition of
Done and the spec; use the code only for real names, signatures and import paths. A test that
restates what the code does passes by construction and proves nothing. If the implementation
contradicts the Definition of Done, write the test the Definition of Done requires and let it fail.

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
    written_files: dict[str, str],
    previous_test: str | None = None,
    ruling: str | None = None,
) -> str:
    """Write the acceptance test into the workspace; returns its repo-relative path."""
    files = "\n\n".join(f"### {p}\n```\n{c}\n```" for p, c in written_files.items())
    body = (
        f"## Specification\n**Title:** {spec.title}\n**Goal:** {spec.goal}\n**Acceptance criteria:**\n"
        + "\n".join(f"- {c}" for c in spec.acceptance_criteria)
        + f"\n\n## Task\n**ID:** {task.id}\n**Title:** {task.title}\n**Description:** {task.description}\n"
        f"**Target files:** {', '.join(task.target_files)}\n**Definition of Done:** {task.definition_of_done}\n\n"
        + (f"## Implementation just written (for names and imports only)\n\n{files}\n\n" if files else "")
        + (
            f"## Rewrite required\nThe previous test was judged incorrect:\n{ruling}\n\n"
            f"Previous test:\n```\n{previous_test}\n```\n"
            if ruling
            else ""
        )
    )
    messages = [SystemMessage(SYSTEM.format(rules=gate.test_prompt_rules())), HumanMessage(body)]
    runnable = structured(get_settings().test_generator_model, GeneratedTest, tool_name="write_test_file")

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

// Hand-written explanations. Everything factual here is checked against the code
// (links point at the real files) or against the captured data in ../data.

export type ConceptId = 'tools' | 'rag' | 'state' | 'routing' | 'memory' | 'fallbacks' | 'evals'

export interface NodeInfo {
  title: string
  who: string // which actor does the work
  what: string // one plain-language sentence
  concept?: ConceptId
  file: string
}

export const NODES: Record<string, NodeInfo> = {
  __start__: { title: 'Request arrives', who: 'You', what: 'A feature request comes in — from the CLI, the REST API, MCP, or a labelled GitHub issue.', file: 'src/agentflow/runner.py' },
  prepare_workspace: { title: 'Prepare workspace', who: 'Python', what: 'Clone the target repo into a temp folder on a new branch, then (re)index its code for search. Only files whose hash changed get re-embedded.', concept: 'rag', file: 'src/agentflow/graph/nodes.py' },
  generate_spec: { title: 'Write the spec', who: 'Opus (orchestrator)', what: 'Turn a one-line request into a precise spec with testable acceptance criteria — after searching the repo so it reuses real names.', concept: 'tools', file: 'src/agentflow/agents/orchestrator.py' },
  await_spec_approval: { title: 'Human checks the spec', who: 'You', what: 'The graph pauses and saves its state. It resumes only when someone approves or rejects with feedback — via API, Telegram or MCP.', concept: 'state', file: 'src/agentflow/graph/nodes.py' },
  generate_tasks: { title: 'Plan the tasks', who: 'Opus (orchestrator)', what: 'Split the spec into small tasks with dependencies (a DAG). Cycles and dangling dependencies get repaired automatically.', concept: 'tools', file: 'src/agentflow/agents/orchestrator.py' },
  await_task_approval: { title: 'Human checks the plan', who: 'You', what: 'Second checkpoint: approve the plan, or reject it with feedback and it gets re-planned.', concept: 'state', file: 'src/agentflow/graph/nodes.py' },
  select_next_task: { title: 'Pick next task', who: 'Python', what: 'Choose the first pending task whose dependencies are all done. None left → go open the PR.', concept: 'routing', file: 'src/agentflow/graph/routing.py' },
  run_coder: { title: 'Write the code', who: 'DeepSeek (coder agent)', what: 'A tool-using agent reads files, searches the codebase, writes files. It gets past review feedback and lessons from earlier runs.', concept: 'tools', file: 'src/agentflow/agents/coder.py' },
  generate_task_test: { title: 'Write the acceptance test', who: 'Opus (test generator)', what: 'Write a test from the task’s Definition of Done — after the code exists, so names and imports line up, but asserting what the task demands.', concept: 'tools', file: 'src/agentflow/agents/test_generator.py' },
  run_review: { title: 'Gate, then review', who: 'pytest/ruff + Opus (reviewer)', what: 'Deterministic checks first (types, tests, lint). Only if they pass does a reviewer agent judge the diff — and it can search the repo while doing so.', concept: 'routing', file: 'src/agentflow/graph/nodes.py' },
  adjudicate: { title: 'Referee', who: 'Opus (referee)', what: 'When the same failure repeats 3×, decide whether the test is wrong rather than the code. If so, rewrite the test and try again.', concept: 'routing', file: 'src/agentflow/agents/reviewer.py' },
  complete_task: { title: 'Commit + learn', who: 'Python', what: 'Commit code and test together, re-index the repo so later tasks can find the new code, and save useful review feedback as long-term lessons.', concept: 'memory', file: 'src/agentflow/graph/nodes.py' },
  handle_failure: { title: 'Contain the failure', who: 'Python', what: 'Mark the task failed and skip only the tasks that depend on it — unrelated work keeps going.', concept: 'routing', file: 'src/agentflow/graph/nodes.py' },
  create_pr: { title: 'Open the PR', who: 'Python + Opus', what: 'Run the whole test suite once more, push the branch, write a description and open the pull request.', file: 'src/agentflow/graph/nodes.py' },
  notify: { title: 'Notify', who: 'Telegram', what: 'Tell a human it is done (or what failed).', file: 'src/agentflow/integrations/telegram.py' },
  finalize: { title: 'Clean up', who: 'Python', what: 'Delete the temporary clone. The graph ends.', file: 'src/agentflow/integrations/workspace.py' },
}

export const GLOSSARY: Record<string, string> = {
  LLM: 'Large language model — the thing behind Claude or GPT. Text in, text out; it has no memory between calls unless you send the history again.',
  agent: 'An LLM in a loop with tools: it decides which tool to call, sees the result, and decides again until the job is done.',
  'tool calling': 'The model answers with a structured request like {"name": "read_file", "args": {...}} instead of prose. Your code runs it and sends back the result.',
  'structured output': 'Forcing the model to answer in a fixed JSON shape (here: Pydantic models), so code can rely on fields existing instead of parsing prose.',
  RAG: 'Retrieval-Augmented Generation: before asking the model, search your own data and paste the relevant bits into the prompt.',
  embedding: 'A list of numbers that represents the meaning of a text. Similar meaning → nearby vectors, so you can search by meaning.',
  'vector search': 'Finding the stored embeddings closest to the query’s embedding. pgvector does this inside Postgres.',
  'full-text search': 'Classic keyword search (Postgres tsvector). Great at exact identifiers like get_user_by_email.',
  RRF: 'Reciprocal Rank Fusion: merge two ranked lists by summing 1/(60+rank). Items ranked well by both float to the top.',
  LangGraph: 'A library for building agent workflows as a graph of steps (nodes) with typed shared state, saved after every step.',
  checkpoint: 'A snapshot of the graph’s state saved after every step. It is how a run can pause for a human and resume later — even after a restart.',
  interrupt: 'LangGraph’s pause button: a node calls interrupt(payload), the run stops, and continues when someone resumes it with an answer.',
  HITL: 'Human-in-the-loop: the agent stops at important points and waits for a person to approve.',
  fallback: 'If the primary model errors (outage, rate limit, no credit), retry the same request on another provider.',
  'LLM-as-judge': 'Using a model with a rubric to grade another model’s output — for qualities that are hard to test with code.',
  MCP: 'Model Context Protocol: a standard way to expose tools to AI clients like Claude Desktop, so they can call your service.',
  trace: 'A recording of every model and tool call in a run, with inputs, outputs, tokens and latency (LangSmith).',
}

export interface Bug {
  id: string
  title: string
  symptom: string
  cause: string
  fix: string
  lesson: string
  commit: string
  run?: string // key in failed_runs.json
}

export const BUGS: Bug[] = [
  {
    id: 'perms', title: 'The worker could not create its workspace', run: 'workspace-permissions',
    symptom: 'First real run died in 0.1 s: PermissionError on /tmp/agentflow.',
    cause: 'A Docker named volume is created root-owned; the app runs as a non-root user.',
    fix: 'Create the mount point with the right owner in the image, so a fresh volume inherits it.',
    lesson: 'Containers that run as non-root need their writable paths prepared at build time.',
    commit: 'b317650',
  },
  {
    id: 'lint', title: 'A task that could never pass', run: 'lint-wedge',
    symptom: 'The gate kept failing on two lint errors no matter what the coder wrote.',
    cause: 'ruff checked the whole repo — including the generated acceptance test, which the coder is not allowed to edit.',
    fix: 'Lint only the files this task wrote; exclude acceptance tests from lint and type checks.',
    lesson: 'Never fail an agent on something it is not allowed to change. That is an infinite loop with extra steps.',
    commit: 'b317650',
  },
  {
    id: 'loop', title: 'An infinite coder ↔ reviewer loop', run: 'reviewer-loop',
    symptom: '37 graph steps on one small task; the reviewer failed every time with “credit balance too low”.',
    cause: 'Reviewer crashes are deliberately not charged to the task — but nothing capped them, and every error carried a new request id, so “same failure repeating” never matched.',
    fix: 'A separate budget for reviewer malfunctions (3), plus a provider fallback for the reviewer.',
    lesson: 'Every loop needs a bound, including the ones you excluded from the main budget on purpose.',
    commit: 'b317650',
  },
  {
    id: 'thinking', title: 'Every structured DeepSeek call failed',
    symptom: 'TypeError: create() got an unexpected keyword argument “thinking”.',
    cause: 'The Python OpenAI SDK rejects unknown arguments; provider-specific flags must go in extra_body. The TypeScript SDK had accepted it.',
    fix: 'Send thinking=disabled via extra_body.',
    lesson: 'Porting between SDKs is not just syntax — the same option travels through different channels.',
    commit: '267feff',
  },
  {
    id: 'crash', title: 'Crashed runs hung forever', run: 'crash-marked-failed',
    symptom: 'A node failed after all retries; the run stayed “in progress”, pollers waited forever, nobody was told.',
    cause: 'An exception escaping the graph leaves the last checkpoint without a terminal status.',
    fix: 'Catch it, write status=failed into the checkpoint (routed through handle_failure), notify on Telegram.',
    lesson: 'An agent run must always end in a state a human can see: done, failed, or waiting for you.',
    commit: '267feff',
  },
  {
    id: 'fallback', title: 'The fallback was broken all along',
    symptom: 'Anthropic ran out of credit, and the “fallback to DeepSeek” failed too.',
    cause: 'A custom tool name passed to with_structured_output is forwarded by ChatOpenAI into create(), which rejects it. Nobody noticed because the primary had never failed before.',
    fix: 'Drop the custom name. Add live smoke tests that call every model on its own.',
    lesson: 'A fallback you have never exercised is not a fallback. Test each provider in isolation.',
    commit: '8eab931',
  },
  {
    id: 'key', title: 'The Anthropic key was invisible locally',
    symptom: 'Worked in Docker, failed with “no API key” when run from the terminal.',
    cause: 'pydantic-settings reads .env into a Settings object, but the Anthropic SDK only looks at real environment variables. Docker’s env_file happened to set those.',
    fix: 'Pass api_key from Settings explicitly.',
    lesson: 'Configuration that works in one runtime by accident is a bug waiting for the other runtime.',
    commit: '8eab931',
  },
]

BUGS.push(
  {
    id: 'one-criterion', title: 'A spec with a single acceptance criterion',
    symptom: 'The real run’s spec had 1 acceptance criterion where earlier runs had 12–14. No error anywhere — the replay above shows it.',
    cause: 'The model returned the list field as one sentence. A validator meant to rescue “- a\n- b” strings turned it into a one-item list and accepted it.',
    fix: 'Require at least two criteria; on a schema violation, ask again once with a hint about JSON arrays.',
    lesson: 'Lenient parsing hides quality regressions. Validate the shape you need, not just the shape you got.',
    commit: '565438f',
  },
  {
    id: 'pyc', title: 'The gate could test code that no longer existed',
    symptom: 'A gate test failed intermittently: the fixed code still returned the old result.',
    cause: 'Python caches bytecode keyed by file mtime (1 s resolution) and size. Rewriting a - b as a + b in the same second keeps both — so the stale .pyc ran.',
    fix: 'Every gate run uses a fresh PYTHONPYCACHEPREFIX, so cached bytecode is never reused.',
    lesson: 'Agents edit files far faster than humans; tools tuned for human editing speeds can lie to them.',
    commit: '565438f',
  },
  {
    id: 'ci-noise', title: 'A CI gate that measured noise',
    symptom: 'Adding an unrelated endpoint dropped “hybrid recall” from 0.90 to 0.78 and would have failed CI.',
    cause: 'Without an embeddings key the vector half is hash noise; any new code shuffles it.',
    fix: 'Gate on the keyword half offline, on fused search only with real embeddings.',
    lesson: 'Before gating on a metric, check that it moves only when the thing you care about moves.',
    commit: '9715bdf',
  },
)

export interface UseCase {
  id: string
  title: string
  who: string
  steps: string[]
  code: string
}

export const USE_CASES: UseCase[] = [
  {
    id: 'issue', title: 'Label a GitHub issue → get a PR', who: 'A team using GitHub',
    steps: [
      'Someone writes an issue and adds the label agentflow.',
      'GitHub sends a webhook; the API verifies its HMAC signature and only enqueues a job.',
      'The worker runs the graph; at the spec and plan it pauses and posts Approve / Reject buttons to Telegram.',
      'After approval it codes, tests, reviews and opens a PR that says “Closes #N”, then comments on the issue.',
    ],
    code: `# GitHub → Settings → Webhooks
Payload URL:  https://<your-host>/webhooks/github
Content type: application/json
Secret:       $GITHUB_WEBHOOK_SECRET
Events:       Issues`,
  },
  {
    id: 'telegram', title: 'Approve from your phone', who: 'The human in the loop',
    steps: [
      'When a run pauses, the worker sends the spec (or plan) with ✅ / ❌ buttons.',
      'A button press hits /webhooks/telegram, which enqueues a resume for exactly that checkpoint.',
      'Double-tapping is safe: the job id is derived from the checkpoint, so it resumes once.',
      'To reject with feedback, reply /reject <thread> <what to change>.',
    ],
    code: `/reject run-712dd72b01d9 use a dataclass for the result instead of a dict`,
  },
  {
    id: 'mcp', title: 'Drive it from Claude Desktop or Claude Code', who: 'You, in a chat',
    steps: [
      'Register the MCP server once.',
      'Ask the assistant to start a run; it calls start_run.',
      'Ask “what is it waiting for?” → get_run; “approve it” → review_checkpoint.',
      'search_codebase lets the assistant query the same hybrid index the agents use.',
    ],
    code: `claude mcp add agentflow -- uv --directory ~/agentflow run agentflow-mcp`,
  },
  {
    id: 'cli', title: 'Run it locally on a repo', who: 'A developer trying it out',
    steps: [
      'No Docker needed: state is kept in memory.',
      'The spec and plan are shown in the terminal; type y or your feedback.',
      'Commits land on your local branch; no PR is opened in in-place mode.',
    ],
    code: `uv run agentflow search tests/fixtures/sample_repo "where are passwords hashed"
uv run agentflow run "Add change_email that rejects duplicates" --path ../my-python-repo`,
  },
]

export const CODE_MAP: { concept: string; files: string[] }[] = [
  { concept: 'The graph (nodes + edges)', files: ['src/agentflow/graph/build.py', 'src/agentflow/graph/nodes.py'] },
  { concept: 'Routing rules & budgets', files: ['src/agentflow/graph/routing.py', 'src/agentflow/config.py'] },
  { concept: 'Shared state & reducers', files: ['src/agentflow/graph/state.py'] },
  { concept: 'Agents (coder, reviewer, referee)', files: ['src/agentflow/agents/coder.py', 'src/agentflow/agents/reviewer.py'] },
  { concept: 'Structured outputs (schemas)', files: ['src/agentflow/schemas.py', 'src/agentflow/models.py'] },
  { concept: 'RAG: chunk → embed → store → search', files: ['src/agentflow/rag/chunking.py', 'src/agentflow/rag/indexer.py', 'src/agentflow/rag/store.py', 'src/agentflow/rag/retriever.py'] },
  { concept: 'Long-term memory', files: ['src/agentflow/memory/lessons.py'] },
  { concept: 'Deterministic gate', files: ['src/agentflow/gate/python.py', 'src/agentflow/gate/typescript.py'] },
  { concept: 'Start / resume / inspect runs', files: ['src/agentflow/runner.py'] },
  { concept: 'API, webhooks, queue worker', files: ['src/agentflow/api/app.py', 'src/agentflow/worker.py'] },
  { concept: 'MCP server', files: ['src/agentflow/mcp_server.py'] },
  { concept: 'Evals', files: ['evals/retrieval_eval.py', 'evals/spec_eval.py', 'evals/e2e_eval.py'] },
  { concept: 'Tests (graph e2e with fake LLMs)', files: ['tests/test_workflow_e2e.py', 'tests/test_routing.py'] },
  { concept: 'Deploy', files: ['docker/Dockerfile', 'deploy/helm/agentflow/values.yaml', 'infra/terraform/eks.tf'] },
]

export const INTERVIEW: { q: string; a: string }[] = [
  {
    q: 'Walk me through what happens when a request comes in.',
    a: 'It becomes a LangGraph run. Opus writes a spec grounded in retrieved code, a human approves, Opus plans a task DAG, a human approves. Then per task: DeepSeek codes with tools, Opus writes an acceptance test from the definition of done, a deterministic gate runs, an Opus reviewer judges the diff, and the task is committed. Finally a full-suite check and a PR. State is checkpointed in Postgres after every step.',
  },
  {
    q: 'Why not a single agent with all the tools?',
    a: 'Control and cost. A graph makes the order of steps, the budgets and the human checkpoints explicit and testable — the routing is plain Python with unit tests. Agents are used only where judgement is needed (coding, reviewing). Using DeepSeek for coding and Opus for judgement cuts token cost substantially.',
  },
  {
    q: 'How does your RAG work, and why hybrid?',
    a: 'Code is split along language boundaries, each chunk gets a “File: path (lines)” header and is embedded into pgvector; the same table has a Postgres full-text index. A query runs both and fuses the rankings with RRF. Embeddings find code by meaning, keyword search finds exact identifiers; fusion means neither failure mode dominates. Re-indexing is incremental by file hash and happens after every task.',
  },
  {
    q: 'How do you stop an agent from looping forever?',
    a: 'Separate budgets: coder crashes, gate failures, review rejections and reviewer malfunctions are counted independently. Repeated identical failures are detected and sent once to a referee that can decide the test is wrong. A failed task only takes down its dependents. The live run taught me the last one: an uncapped malfunction counter looped when the API ran out of credit.',
  },
  {
    q: 'How do you know it works? How do you evaluate it?',
    a: 'Three layers. Unit and graph-level tests with fake LLMs for the deterministic logic. Evals: a retrieval ablation gated in CI, an LLM-as-judge rubric for specs, and end-to-end runs on a fixture repo tracked as LangSmith experiments. And live smoke tests that call each model in isolation — added after a fallback turned out to be broken.',
  },
  {
    q: 'What happens if a provider goes down?',
    a: 'Each call has client retries; structured calls have a with_fallbacks chain and agents have fallback middleware to a second provider; graph nodes have retry policies and timeouts. If a node still fails, the run is marked failed in its checkpoint and a notification goes out — no silent hangs.',
  },
  {
    q: 'Where is a human involved, and how does resuming work?',
    a: 'At the spec and plan. The node calls interrupt(), the checkpoint is saved, and the worker posts Approve/Reject to Telegram. A button press enqueues a resume job whose id is the checkpoint id, so duplicate clicks are harmless. The worker resumes the graph from that checkpoint with the decision.',
  },
  {
    q: 'What would you do next?',
    a: 'Replace the hand-written retrieval golden set with queries the agents actually issued (from traces), turn on real embeddings in CI, add cost tracking per run, and deploy the Terraform stack for a real staging environment.',
  },
]

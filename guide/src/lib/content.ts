// Hand-written explanations. Everything factual here is checked against the code
// (links point at the real files) or against the captured data in ../data.

export type ConceptId = 'tools' | 'state' | 'routing' | 'fallbacks' | 'evals'

export interface NodeInfo {
  title: string
  who: string // which actor does the work
  what: string // one plain-language sentence
  concept?: ConceptId
  file: string
}

export const NODES: Record<string, NodeInfo> = {
  __start__: { title: 'Request arrives', who: 'You', what: 'A feature request comes in — from the CLI, the REST API, MCP, or a labelled GitHub issue.', file: 'src/agentflow/runner.py' },
  prepare_workspace: { title: 'Prepare workspace', who: 'Python', what: 'Clone the target repo into a temp folder on a new branch, committing as the GitHub token’s owner. Nothing is indexed: the agents explore it later with read-only glob, grep and read tools.', concept: 'tools', file: 'src/agentflow/graph/nodes.py' },
  generate_spec: { title: 'Write the spec', who: 'Opus (orchestrator)', what: 'Turn a one-line request into a precise spec with testable acceptance criteria — after exploring the repo with glob, grep and read so it reuses real names.', concept: 'tools', file: 'src/agentflow/agents/orchestrator.py' },
  await_spec_approval: { title: 'Human checks the spec', who: 'You', what: 'The graph pauses and saves its state. It resumes only when someone approves or rejects with feedback — via API, Telegram or MCP.', concept: 'state', file: 'src/agentflow/graph/nodes.py' },
  generate_tasks: { title: 'Plan the tasks', who: 'Opus (orchestrator)', what: 'Explore the repo with read-only glob, grep and read tools, then split the spec into small tasks with dependencies (a DAG), each naming real files and a binding interface. Cycles and dangling dependencies get repaired automatically.', concept: 'tools', file: 'src/agentflow/agents/orchestrator.py' },
  await_task_approval: { title: 'Human checks the plan', who: 'You', what: 'Second checkpoint: approve the plan, or reject it with feedback and it gets re-planned.', concept: 'state', file: 'src/agentflow/graph/nodes.py' },
  select_next_task: { title: 'Pick next task', who: 'Python', what: 'Choose the first pending task whose dependencies are all done. None left → go open the PR.', concept: 'routing', file: 'src/agentflow/graph/routing.py' },
  generate_task_test: { title: 'Write the failing test', who: 'Opus (test generator)', what: 'Before any code: write the acceptance test from the task’s interface and Definition of Done, then check it fails (red). A test that already passes asserts nothing and is rewritten. Skipped when the repo has no test suite of its own: its types, build and lint are the gate.', concept: 'tools', file: 'src/agentflow/agents/test_generator.py' },
  run_coder: { title: 'Make it pass', who: 'DeepSeek (coder agent)', what: 'A tool-using agent greps and reads the codebase and writes code until the failing test can pass. It also gets the reviewer’s feedback from earlier attempts.', concept: 'tools', file: 'src/agentflow/agents/coder.py' },
  run_review: { title: 'Gate, then review', who: 'pytest/ruff + Opus (reviewer)', what: 'Deterministic checks first (types, tests, lint). Only if they pass does a reviewer agent judge the diff — and it can grep and read the repo while doing so.', concept: 'routing', file: 'src/agentflow/graph/nodes.py' },
  adjudicate: { title: 'Referee', who: 'Opus (referee)', what: 'When the same failure repeats 3×, decide whether the test is wrong rather than the code. If so, rewrite the test and try again.', concept: 'routing', file: 'src/agentflow/agents/reviewer.py' },
  complete_task: { title: 'Commit', who: 'Python', what: 'Commit code and test together, so the PR never ships code without its test.', concept: 'state', file: 'src/agentflow/graph/nodes.py' },
  escalate: { title: 'Ask a human', who: 'You', what: 'A task ran out of budget. The run pauses with a dossier — attempts, recent failures, open reviewer requests, partial work — and you choose: retry with a hint, re-plan, skip, or stop.', concept: 'routing', file: 'src/agentflow/graph/escalation.py' },
  replan: { title: 'Re-plan', who: 'Opus (orchestrator)', what: 'Rewrite only the not-yet-done tasks around the failure, using the dossier, your guidance and the same read-only repo tools as the planner. Completed tasks and their commits stay as they are.', concept: 'routing', file: 'src/agentflow/graph/nodes.py' },
  handle_failure: { title: 'Contain the failure', who: 'Python', what: 'Skip: mark the task failed and skip only the tasks that depend on it. Stop: mark everything left as not started and end the run.', concept: 'routing', file: 'src/agentflow/graph/nodes.py' },
  create_pr: { title: 'Open the PR', who: 'Python + Opus', what: 'Run the whole test suite once more, push the branch, write a description and open the pull request.', file: 'src/agentflow/graph/nodes.py' },
  notify: { title: 'Notify', who: 'Telegram', what: 'Tell a human it is done (or what failed).', file: 'src/agentflow/integrations/telegram.py' },
  __end__: { title: 'End', who: 'Python', what: 'The graph is finished: either completed (a PR is open) or stopped — by a human, or because nothing salvageable was left.', file: 'src/agentflow/graph/routing.py' },
  finalize: { title: 'Clean up', who: 'Python', what: 'Delete the temporary clone. The graph ends.', file: 'src/agentflow/integrations/workspace.py' },
}

export const GLOSSARY: Record<string, string> = {
  LLM: 'Large language model — the thing behind Claude or GPT. Text in, text out; it has no memory between calls unless you send the history again.',
  agent: 'An LLM in a loop with tools: it decides which tool to call, sees the result, and decides again until the job is done.',
  'tool calling': 'The model answers with a structured request like {"name": "read_file", "args": {...}} instead of prose. Your code runs it and sends back the result.',
  'structured output': 'Forcing the model to answer in a fixed JSON shape (here: Pydantic models), so code can rely on fields existing instead of parsing prose.',
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
    id: 'oscillation', title: 'A ping-pong the loop detector could not see',
    symptom: 'First live test-first run: one task burned 4 gate failures and 4 review rejections, alternating, then the whole plan was skipped.',
    cause: 'The test demanded a change to an API the task said not to touch; the coder made it, the reviewer (rightly) rejected it, the coder reverted, the test failed again. Failures alternated A, B, A, B — and stall detection only looked for the same failure twice in a row, so the referee was never called.',
    fix: 'Count repeats within a window of recent attempts, and show the referee every distinct recent failure so the contradiction is visible.',
    lesson: 'Loops are not only streaks. Detect cycles, not just repetition.',
    commit: 'test-first PR',
  },
  {
    id: 'blind-test', title: 'Test-first without looking at the code',
    symptom: 'The test asserted that an existing insert() returns a dict. It returns an id, and the task said to leave it alone.',
    cause: 'In test-first mode the test generator was given the interface of the new code but not the current code of the files being changed, so it guessed existing behaviour.',
    fix: 'Give the generator the current contents of the task’s target files, with a rule not to assert different behaviour for APIs the task does not change.',
    lesson: 'Writing tests first does not mean writing them blind — the contract includes what must stay the same.',
    commit: 'test-first PR',
  },
  {
    id: 'notify-crash', title: 'A notification that failed the job',
    symptom: 'During the first live escalation, the worker job that paused the run was marked failed with a ConnectError.',
    cause: 'A transient network error reaching Telegram escaped from the notification call. The run itself had paused correctly, but the job failed and the human was never told.',
    fix: 'Retry once, then log a warning; notification calls never raise.',
    lesson: 'Side-channel calls (alerts, metrics) must not be able to fail the main work.',
    commit: 'escalation PR',
  },
  {
    id: 'gate-vitest', title: 'The gate made the coder add vitest',
    symptom: 'On a Vite site with no tests, the first correct change failed the gate with “Cannot find package vitest”. The coder rewrote the gate’s config, then added vitest to the repo’s package.json — which would have shipped in the PR.',
    cause: 'The TypeScript gate assumed the target repo already had vitest: it wrote its config (importing vitest/config) into the repo root and ran npx vitest. Every failure looked like the coder’s fault, and the gate rewrote the config on each run.',
    fix: 'The gate brings its own pinned vitest, installed outside the repo (preinstalled in the image), with a config that lives outside the repo too; a repo with its own vitest still uses it. tsc errors about the gate’s vitest import are not reported to the coder.',
    lesson: 'The verifier’s own tooling is not the project’s dependency: never make the agent fix the checker.',
    commit: 'gate runner PR',
  },
  {
    id: 'gate-jsx', title: 'A component test that could never load',
    symptom: 'The next run on the same React site failed the gate four times with “Expected > but found data”; the coder rewrote vite.config.ts, the referee regenerated the test, and the task escalated.',
    cause: 'The test writer rendered <Home /> — the natural test for a UI task — but the gate always saved tests as .test.ts, where JSX does not parse. Even as .tsx it would have failed next: jsdom and Testing Library were in neither the repo nor the gate, and the gate ignored the project’s vite config, so the coder’s edits to it changed nothing.',
    fix: 'JSX tests are saved as .test.tsx; the gate’s runner ships jsdom and Testing Library (resolved against the repo’s React); its config is layered over the project’s vite config. npm ci instead of npm install stops the lockfile churn that would have reached the PR.',
    lesson: 'If the checker cannot run the kind of test the task calls for, every failure is the checker’s, not the coder’s.',
    commit: 'gate components PR',
  },
  {
    id: 'foreign-tests', title: 'Tests the repo could never run',
    symptom: 'Two live runs on a React site with no tests were spent fixing the gate’s own vitest setup, and the PR would have shipped test files the project cannot run.',
    cause: 'Every task got a target test, whether or not the project tests itself — so agentflow imposed a test runner on a repo that only builds.',
    fix: 'Target tests only for repos with a test suite (framework, real test script or test files). Otherwise the gate is the project’s own checks — types, npm run build (unless already broken at the start) and lint — and the reviewer judges against the Definition of Done.',
    lesson: 'Verify with what the project already trusts before adding machinery of your own.',
    commit: 'target tests PR',
  },
  {
    id: 'masked-token', title: 'A rejected token that looked like a missing one',
    symptom: 'A run died cloning the repo, and Telegram showed the clone URL as https://:@github.com/… — as if no token were set. The token was set; GitHub had rejected it.',
    cause: 'GitPython masks credentials in its errors as *****:*****. The error went out as Telegram Markdown, where * marks bold, so the mask vanished and left an empty-looking user and password.',
    fix: 'Error text goes into a code block, which Telegram shows exactly as written.',
    lesson: 'Raw tool output is data: never let a formatter interpret it.',
    commit: 'token PR',
  },
  {
    id: 'token-in-workspace', title: 'A token the agents could read',
    symptom: 'Found while chasing that clone failure: every workspace kept the GitHub token in .git/config, and the coder’s read_file would open .git/config like any other file.',
    cause: 'The token was put into the clone URL, and git saves the URL as the remote. Path checks only stopped paths leaving the workspace, and .git is inside it.',
    fix: 'Git gets the token from a credential helper set in the environment of each clone and push, for github.com only, so it is never written to disk. The file tools refuse anything under .git.',
    lesson: 'Whatever sits in the agent’s sandbox is in the model’s context: keep credentials out of it.',
    commit: 'token PR',
  },
  {
    id: 'commit-author', title: 'A PR that Vercel would not build',
    symptom: 'The first finished run on a Vercel site opened its PR, but the preview never deployed: Vercel blocked it because the commit author agentflow@f760d0960815 was not a valid email.',
    cause: 'The worker had no git identity, so GitPython fell back to user@hostname, and a container’s hostname is its id. No GitHub account owns that address.',
    fix: 'Commits are by the token’s owner, under their GitHub noreply address (id+login@users.noreply.github.com); GIT_AUTHOR_* / GIT_COMMITTER_* still override it.',
    lesson: 'Every commit names an author: make it one the tools downstream can recognize.',
    commit: 'commit identity PR',
  },
  {
    id: 'reviewer-call-limit', title: 'A reviewer that never got to answer',
    symptom: 'Right after that, three reviews in a row ended “reviewer finished without a structured decision” and the task escalated, although nobody had found anything wrong with the code.',
    cause: 'The reviewer agent is capped at a number of model calls. It spent all 8 on read_file / grep, and the cap ended the loop before the call that returns the verdict.',
    fix: 'Scale the cap with the diff (8 + 3 per changed file, at most 40), and when it is still hit, make one tool-free call with the diff and everything it read, forcing a verdict.',
    lesson: 'A budget on exploration must leave room for the answer.',
    commit: 'gate runner PR',
  },
  {
    id: 'silent-approve', title: 'Approved, then silence',
    symptom: 'After pressing Approve on a plan, the buttons stayed live and the chat said nothing for the whole coding phase; there was no way to tell whether the run had picked the work up.',
    cause: 'A press only answered the callback (a popup that vanishes in a second). Progress existed inside the graph but never reached Telegram until the next question or the PR.',
    fix: 'Take the buttons off the answered message and note who answered; each worker job posts one silent status card and edits it as nodes finish (throttled, never raising); /status asks on demand.',
    lesson: 'An asynchronous system owes the human an acknowledgement and a visible state, not just the final result.',
    commit: 'status card PR',
  },
  {
    id: 'reviewer-fallback', title: 'Every review failed once Opus was rate-limited',
    symptom: 'A live run escalated twice; the dossiers showed 3 reviewer malfunctions each: “Thinking mode does not support this tool_choice”.',
    cause: 'The Opus proxy returned 429, the reviewer fell back to DeepSeek as designed — but the fallback was built with DeepSeek’s thinking mode on, and the reviewer’s final structured verdict is a forced tool call, which thinking mode rejects.',
    fix: 'Build the reviewer’s primary and fallback models through one helper that disables thinking; a live test now runs the reviewer agent path on every model.',
    lesson: 'Escalation showed the evidence immediately — the dossier, not the logs, is where this was spotted.',
    commit: 'guide-review PR',
  },
  {
    id: 'zombie-runs', title: 'Runs that said “coding” for weeks',
    symptom: 'The first /runs listed four sandbox runs as speccing or coding, a month after anyone had touched them. Nothing said they had stopped, or why.',
    cause: 'Two were cancelled by a worker restart: CancelledError is a BaseException, so the crash handler (except Exception) never recorded them. Two predate the handler. In every case the run kept its last status, and the only trace of the cause was the node error LangGraph left in the checkpoint.',
    fix: 'The worker keeps a Redis heartbeat per run; unfinished, unpaused runs without one are flagged stalled, with the checkpoint’s node error as the cause. Cancellation is caught and recorded as failed before it propagates.',
    lesson: 'Status written by the process doing the work cannot report that process dying. Liveness needs a signal that expires on its own.',
    commit: 'stalled-runs PR',
  },
  {
    id: 'whitelist-mount', title: 'The containerised board refused every card',
    symptom: 'The first research card after deploying the orchestrator to docker went from Бэклог straight to Блокировка: “repo is not in the whitelist”. The same repo had passed intake from a local checkout an hour earlier.',
    cause: 'repos.toml is gitignored (it lists private repos), so the image never had it, and docker-compose did not mount it. A missing file is an empty whitelist on purpose; the api did not notice because it runs with INSECURE_DEV, the worker does not.',
    fix: 'Compose bind-mounts ./repos.toml into api and worker read-only, with create_host_path: false so a missing file stops `up` instead of Docker creating an empty directory in its place.',
    lesson: 'Every live test so far had run from a checkout. The deployment is a different runtime: test it as one.',
    commit: 'close-subtasks PR',
  },
  {
    id: 'open-subtasks', title: 'A merged card with an open sub-task',
    symptom: 'Card #10 sat in Смержено with its PR merged, while its plan’s sub-issue was still open.',
    cause: 'The developer closes a sub-issue when its task completes in the current run. The card went back once, and the fix run has its own single task, so the plan’s task id never completed in any run the developer looked at.',
    fix: 'When the releaser moves a card to Смержено, it closes every sub-issue still open.',
    lesson: 'Tie cleanup to the end state, not to the path that usually leads there.',
    commit: 'close-subtasks PR',
  },
  {
    id: 'job-id-reuse', title: 'A card the orchestrator kept handing out and nobody ran',
    symptom: 'Back in Бэклог after a fix, card #18 got Owner = intake, nothing ran, and ten minutes later the orchestrator freed it as stuck. Then it handed it out again.',
    cause: 'Stage jobs had one arq job id per card and agent. arq keeps a finished job’s result for keep_result (a day here) and returns None for an id it still holds, without an error. The first intake run had used the id that morning.',
    fix: 'A fresh job id per launch (Owner and the queued heartbeat already prevent double launches), and a refused enqueue raises.',
    lesson: 'A dedupe key outlives the thing it dedupes. Know how long your queue remembers.',
    commit: 'stage-job-ids PR',
  },
  {
    id: 'uv-app-install', title: 'A Django skeleton the gate could never install',
    symptom: 'The first card of mentor-platform spent all five attempts of its first task on "No module named mypy_django_plugin" and setuptools refusing "multiple packages", and went to Блокировка with correct code.',
    cause: 'The Python gate installed every repo with `pip install -e .`. A uv app keeps django-stubs in a dependency group, which that never installs, and an app with apps/ and config/ side by side and no build system is not a package setuptools can build.',
    fix: 'A repo with uv.lock or [dependency-groups] is installed with `uv sync --all-groups` into the gate venv; pip stays for the rest.',
    lesson: 'Install a project the way its own README does. A generic install is a guess about the build.',
    commit: 'gate-uv-project PR',
  },
  {
    id: 'budget-and-plugin-crash', title: 'Two failures that hid their own cause',
    symptom: 'Card #2 of mentor-platform used all five attempts. mypy reported only "Error constructing plugin instance of NewSemanalDjangoPlugin ... INTERNAL ERROR", and twice the coder died with "Recursion limit of 75 reached".',
    cause: 'The gate kept only mypy lines containing ": error:", so the traceback naming the real cause (Django settings raising on a missing env var) was dropped. The coder\'s graph recursion limit was 75, below what 25 model calls with tools and middleware need, so a multi-file task crashed before the model-call ceiling.',
    fix: 'A plugin crash now reports the exception lines of its traceback (bounded). The recursion limit is 150, and hitting it ends the attempt with "Ran out of steps" feedback for the next attempt.',
    lesson: 'A failure report is the next attempt\'s only input. Strip noise, never the cause.',
    commit: 'coder-budget-mypy-plugin PR',
  },
  {
    id: 'test-pins-absence', title: 'A test that forbade the next task',
    symptom: 'Card #2 of mentor-platform: the /healthz task used all five attempts. The coder disabled the database guard globally in conftest.py and wrote a list subclass with __eq__ overrides in urls.py; the reviewer rejected it and the card went to Блокировка.',
    cause: 'The skeleton task\'s generated acceptance test asserted that urlpatterns is an empty list. It pinned the absence of what the next task adds, so adding /healthz broke it, and the coder bent the code to satisfy the old assertion instead of the requirement.',
    fix: 'Test-design (and tester) skills now say: assert what the task adds, never the absence or exact emptiness of things a later task may add. The card was resent with a hint to fix the assertion.',
    lesson: 'A test is a promise about this task only. Pinning what is not there yet makes the plan\'s next step a regression.',
    commit: 'acceptance-pins-absence PR',
  },
  {
    id: 'blocked-reason-length', title: 'A stage job that crashed after the card had moved',
    symptom: 'The developer moved card #2 to Блокировка with its dossier, then the job died with "Column value must be a valid value for text column".',
    cause: 'The whole multi-line dossier went into the Blocked reason text field, which GitHub refused. The comment and the status change had already happened, so only the label and the job were lost.',
    fix: 'The field keeps the first line of the report, cut to 300 characters; the comment carries the rest.',
    lesson: 'A label is not a log. Put the short form in the field and the long form where it is read.',
    commit: 'gate-uv-project PR',
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
      'With a task board configured, the label files the issue as a card in Бэклог and the stage agents take it column by column; the steps below are the standalone run without a board.',
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
      'If a task runs out of budget, it sends the evidence with 🔁 Retry / 🗺 Re-plan / ⏭ Skip / 🛑 Stop; /hint <thread> <guidance> retries with your guidance.',
      'A button press reaches the api (webhook, or long polling on a laptop), which enqueues a resume for exactly that checkpoint.',
      'Double-tapping is safe: the job id is derived from the checkpoint, so it resumes once. The buttons disappear once pressed, and the message says who answered.',
      'Each job keeps one silent status card up to date (phase, current step, tasks done); /status <thread> (or just /status for the latest run) asks at any time. Errors arrive verbatim, in a code block.',
      '/runs lists every run in flight, whoever started it, the ones waiting on you first, then stalled ones (🧟, no worker on them) with the error that stopped them; /runs all adds finished ones.',
      'To reject with feedback, reply /reject <thread> <what to change>.',
    ],
    code: `/reject run-712dd72b01d9 use a dataclass for the result instead of a dict`,
  },
  {
    id: 'mcp', title: 'Drive it from Claude Desktop or Claude Code', who: 'You, in a chat',
    steps: [
      'Register the MCP server once.',
      'Ask the assistant to start a run; it calls start_run.',
      'Say “file a card: …” → create_task puts an issue in the board’s Бэклог; “what is on the board?” → list_board.',
      'Ask “what is it waiting for?” → get_run; “what is running?” → list_runs (every run, wherever it started); “approve it” → review_checkpoint.',
    ],
    code: `claude mcp add agentflow -- uv --directory ~/agentflow run agentflow-mcp`,
  },
  {
    id: 'chat', title: 'Ask for it in plain language', who: 'You, in Telegram',
    steps: [
      'Write to the second bot (chat/): “find a Python repo with fastapi, green CI and MIT, and add endpoint X”.',
      'It calls list_repos and find_repos: hard criteria are checked from GitHub, and topic fit is judged from the README.',
      'It files a card with create_task instead of starting a run: before that it asks “Завести карточку в X?” with buttons, unless you named the repo or only one is whitelisted.',
      'The card goes through the board: intake, planner (the plan waits for your «ок» on the issue), developer, tester, releaser; “what is on the board?” → list_board.',
    ],
    code: `cd chat && CHAT_TELEGRAM_BOT_TOKEN=… CHAT_ALLOWED_CHAT_IDS=… uv run agentflow-chat`,
  },
  {
    id: 'cli', title: 'Run it locally on a repo', who: 'A developer trying it out',
    steps: [
      'No Docker needed: state is kept in memory.',
      'The spec and plan are shown in the terminal; type y or your feedback.',
      'Commits land on your local branch; no PR is opened in in-place mode.',
    ],
    code: `uv run agentflow run "Add change_email that rejects duplicates" --path ../my-python-repo`,
  },
]

export const CODE_MAP: { concept: string; files: string[] }[] = [
  { concept: 'The graph (nodes + edges)', files: ['src/agentflow/graph/build.py', 'src/agentflow/graph/nodes.py'] },
  { concept: 'Routing rules & budgets', files: ['src/agentflow/graph/routing.py', 'src/agentflow/config.py'] },
  { concept: 'Shared state & reducers', files: ['src/agentflow/graph/state.py'] },
  { concept: 'Agents (coder, reviewer, referee)', files: ['src/agentflow/agents/coder.py', 'src/agentflow/agents/reviewer.py'] },
  { concept: 'Structured outputs (schemas)', files: ['src/agentflow/schemas.py', 'src/agentflow/models.py'] },
  { concept: 'Code navigation tools (glob, grep, read)', files: ['src/agentflow/agents/repo_tools.py'] },
  { concept: 'Deterministic gate', files: ['src/agentflow/gate/python.py', 'src/agentflow/gate/typescript.py'] },
  { concept: 'Start / resume / inspect runs', files: ['src/agentflow/runner.py'] },
  { concept: 'API, webhooks, queue worker', files: ['src/agentflow/api/app.py', 'src/agentflow/worker.py'] },
  { concept: 'MCP server', files: ['src/agentflow/mcp_server.py'] },
  { concept: 'Repo whitelist & selection facts', files: ['src/agentflow/repos.py'] },
  { concept: 'Conversational layer (Agent SDK + Telegram)', files: ['chat/src/agentflow_chat/agent.py', 'chat/src/agentflow_chat/telegram.py'] },
  { concept: 'Evals', files: ['evals/spec_eval.py', 'evals/e2e_eval.py'] },
  { concept: 'Tests (graph e2e with fake LLMs)', files: ['tests/test_workflow_e2e.py', 'tests/test_routing.py'] },
  { concept: 'Deploy', files: ['docker/Dockerfile', 'docker-compose.yml'] },
]

export const INTERVIEW: { q: string; a: string }[] = [
  {
    q: 'Walk me through what happens when a request comes in.',
    a: 'It becomes a LangGraph run. Opus explores the repo with read-only tools and writes a spec, a human approves, Opus plans a task DAG with a binding interface per task, a human approves. Then per task, test-first: Opus writes an acceptance test that must fail, DeepSeek makes it pass with tools, a deterministic gate runs, an Opus reviewer judges the diff, and the task is committed. Finally a full-suite check and a PR. State is checkpointed in Postgres after every step.',
  },
  {
    q: 'Is it test-driven? Why the tests before the code?',
    a: 'Target tests rather than TDD: one acceptance test per task, written by a different agent before the code, as the task’s stop condition — no red/green/refactor cycles. Repos without a test suite get none; their own types, build and lint are the gate. History: the first version was TDD, but the test generator had to invent names and deadlocked the coder, so it moved to writing tests after the code. That fixed the deadlock but let tests mirror the code, bugs included. Now the planner fixes each task’s interface, the test is written from it before any code and must fail first, and the coder gets an executable target on its first attempt. A referee still handles a test that contradicts its task.',
  },
  {
    q: 'Why not a single agent with all the tools?',
    a: 'Control and cost. A graph makes the order of steps, the budgets and the human checkpoints explicit and testable — the routing is plain Python with unit tests. Agents are used only where judgement is needed (coding, reviewing). Using DeepSeek for coding and Opus for judgement cuts token cost substantially.',
  },
  {
    q: 'How do the agents find the right code? Why no RAG?',
    a: 'They explore it the way a developer does: glob for files, grep for definitions and call sites, read the modules they extend. There used to be hybrid RAG (pgvector + Postgres full-text + RRF), and I removed it. Measured on this repo, the 6000-token preload put the target files in front of the coder in only 2–3 of 6 tasks; the rest was README and generated data. In a live run the coder never called the search tool at all and went straight to read_file. A plan needs exact paths and signatures, which means following references, not ranking chunks by similarity. Dropping it also dropped an embeddings provider, pgvector and a re-index after every task.',
  },
  {
    q: 'How do you stop an agent from looping forever?',
    a: 'Separate budgets: coder crashes, gate failures, review rejections and reviewer malfunctions are counted independently. A failure that comes back a third time within recent attempts — in a row or alternating — goes once to a referee that can decide the test is wrong. When a budget is spent the run escalates: it pauses with an evidence dossier and a human chooses retry-with-hint, re-plan, skip or stop. Live runs taught me both the oscillation case and the uncapped reviewer-malfunction loop.',
  },
  {
    q: 'What happens when the agent cannot finish a task?',
    a: 'It stops and asks instead of guessing or silently dropping work. The dossier shows what was tried, the distinct failures, what the reviewer still wants, what written code exists and what would be blocked. Retry resets the budget and puts the human hint at the top of the coder prompt; re-plan rewrites only the remaining tasks; skip drops the task and its dependents; stop ends the run. Unattended mode keeps the skip behaviour, and per-task escalation and re-plan caps keep it bounded.',
  },
  {
    q: 'How do you know it works? How do you evaluate it?',
    a: 'Three layers. Unit and graph-level tests with fake LLMs for the deterministic logic. Evals: an LLM-as-judge rubric for specs, and end-to-end runs on a fixture repo tracked as LangSmith experiments. And live smoke tests that call each model in isolation — added after a fallback turned out to be broken.',
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
    a: 'Run the end-to-end eval as an A/B against the last RAG version to confirm that exploring plans better than retrieving, add cost tracking per run, and put auth in front of the MCP server over HTTP now that a chat front end drives it.',
  },
]

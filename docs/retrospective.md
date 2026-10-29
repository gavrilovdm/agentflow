# Retrospective: agentflow for product development (2026-10-29)

**Decision (owner, 2026-10-29):** stop using agentflow for product development of `mentor-platform`. The product is built directly in Claude Code (Opus plans and verifies, Sonnet subagents implement, rules from hub `engineering/`, TDD, PRs, CI as the hard gate). agentflow services (worker, api, redis, MCP, chat) are stopped; Postgres (`stage_runs`) and Grafana stay up for analysis. The owner will not recommend this approach for product development. Multi-agent workflows remain an option for specific fixed processes.

This document separates what was measured from what is interpretation, and states the limits of the evidence.

## Measured facts

### The test case
Card `mentor-platform` #2: a Django skeleton, five plan tasks (bootstrap, healthz, env settings, CI, docs).

On agentflow it did not merge in about two days and six runs. Each block was an agentflow defect, not a property of the task:

1. The test generator wrote an acceptance test asserting `urlpatterns == []`; a later task broke it. Fixed in #36.
2. The gate surfaced only `mypy INTERNAL ERROR` from the django-stubs plugin, hiding that settings raised on import; the coder hit the recursion limit (75). Fixed in #38.
3. The gate passed the worker's own environment (including `DATABASE_URL` and secrets) into the target project's tests, so all tests errored against agentflow's Postgres. Fixed in #41.
4. The push token lacked the `workflow` scope, so pushing `ci.yml` was rejected after all five tasks were done and approved. The branch is pushed only at the end and retries do not resume, so the whole run was lost. Mitigated in #42 (the coder no longer writes workflow files); the push-at-the-end and no-resume design is unchanged.
5. After the fixes (#36 to #42), run 6 (started 2026-10-29 03:47) blocked again on task `env-settings` with "ran out of attempts". **The cause was not investigated.**

Other PRs merged the same day: #37 (deepseek-flash model), #39 and #40 (English text).

### Cost (from `scripts/telemetry_report.py --days 3`, read 2026-10-29)

| agent | runs | LLM calls | tokens in | tokens out |
|---|---|---|---|---|
| intake | 1 | 1 | 1.9k | 0.6k |
| planner | 2 | 11 | 76k | 11k |
| developer | 6 | 914 | 28.1M | 1.36M |

Card #2: first-to-last 723.6 min, agent work 149.1 min, 29.5M tokens in total, last outcome Blocked.

Earlier snapshot (before run 6): developer about 586 calls and about 17M input tokens over 5 runs; card 439 min first-to-last, about 103 min of agent work. The difference between the snapshots (about 328 calls, about 11M input tokens, about 46 min agent work) is attributed to run 6, but the two reports used different time windows, so treat it as approximate.

### The same card in Claude Code
One Sonnet subagent: draft PR https://github.com/gavrilovdm-dev/mentor-platform/pull/9, about 61 s, three gate runs (one intentional TDD red, one mypy stubs fix, one green), 6 tests, 15 files.

## Limits of the evidence
- n = 1 card, one project, one day. No repeats, no variance.
- Different models: DeepSeek coder on agentflow vs Sonnet in Claude Code. The comparison does not isolate the harness from the model.
- The Claude Code arm had no separate reviewer or tester stage; PR #9 is not independently reviewed yet, so "works" means "passed its own gate", not "verified correct".
- Wall time on agentflow includes waiting for human approval and re-launches, not only agent work (hence 723 vs 149 min). The ~61 s figure is a single subagent run, not comparable end to end.
- Defects 1 to 4 are fixed, so run 6 may not repeat them; the failure of run 6 is unexplained. The conclusion "agentflow would keep failing" is not demonstrated; the conclusion "it cost more than it saved on this card" is.
- Token counts are raw volumes; no price per token was applied here.

## Interpretation (not measured)
- Building an orchestrator for product development means building and maintaining a harness. Every integration seam (gate environment, token scopes, push timing, generated tests, type stubs) is a failure surface the owner now owns. The four diagnosed blocks all sat on such seams, none in the model's coding ability.
- Without resume, each late bug costs a full run. Late-binding steps (push at the end) turn small defects into total loss.
- Diagnosis needed log forensics across stage runs, which is itself expensive; the telemetry added later (#32) helped but arrived after the cost was paid.
- The model was probably not the bottleneck: the failures were environment and design, not code quality. This is an inference from the five blocks, not a controlled test.
- A mature harness (Claude Code) with high-level rules and skills and CI as the hard gate reached the same artifact faster on this card. Whether this holds across cards is unknown.

## Where custom multi-agent workflows still make sense
Fixed, repeatable, high-volume processes with clear inputs and outputs where cheap models suffice: vacancy parsing, research digests, content pipelines, AI pre-review. There the harness is built once for a narrow task and its seams are stable; the open-ended environment of a real repository is not part of the problem.

## What agentflow taught (material for the AI Engineer course module on agentic workflows)
- Deterministic gates plus an LLM reviewer work, but the gate's environment must be isolated from the worker's (allowlisted env).
- Generated tests need guardrails: a test must not pin absence or incidental state.
- Surface root causes in tool output (a wrapped `INTERNAL ERROR` hides the real failure).
- Make long runs resumable and push early; the most expensive failure is the one at the end.
- Credentials and scopes are part of the design, not an afterthought.
- Instrument first: per-stage spans and token metrics make cost and failure visible.
- Choose the unit of automation by stability of the process, not by what is technically possible.

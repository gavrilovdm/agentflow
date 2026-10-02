import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Card, Code, FileLink, Section, Term } from '../components/ui'
import { listRepos, search, type SearchResult } from '../lib/api'
import type { ConceptId } from '../lib/content'
import { calls, finalSpec, retrieval, run, type LlmCall, type ToolRun } from '../lib/data'
import { useLive } from '../lib/live'
import { afterReview, DEFAULT_BUDGETS } from '../lib/routing'

function Concept({ id, title, plain, where, children }: { id: ConceptId; title: string; plain: ReactNode; where: string[]; children: ReactNode }) {
  return (
    <article id={`concept-${id}`} className="grid gap-6 border-t border-line py-10 first:border-0 first:pt-0 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
      <div>
        <h3 className="text-xl font-semibold">{title}</h3>
        <div className="mt-2 space-y-3 text-[15px] leading-relaxed text-muted">{plain}</div>
        <div className="mt-4 flex flex-col gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">Where in the code</span>
          {where.map((w) => (
            <FileLink key={w} path={w} />
          ))}
        </div>
      </div>
      <div className="min-w-0">{children}</div>
    </article>
  )
}

export function Concepts() {
  return (
    <Section
      id="concepts"
      kicker="2 · The ideas"
      title="Seven concepts, each with something to try"
      intro="Every AI-agent buzzword in the job description shows up somewhere in this project. Here is what each one means in plain terms, where it lives, and a small interactive version of it."
    >
      <Concept
        id="tools"
        title="Tool calling & structured output"
        plain={
          <>
            <p>
              A model can’t read files or run code. With <Term t="tool calling" /> it answers with a request — “call{' '}
              <code>read_file</code> with <code>shop/db.py</code>” — your code runs it, and the result goes back in the
              next message. Loop that and you have an <Term t="agent" />.
            </p>
            <p>
              <Term t="structured output" /> is the same trick used to get data instead of actions: the “tool” is a
              Pydantic schema, so the answer is guaranteed to have <code>title</code>, <code>goal</code>,{' '}
              <code>acceptance_criteria</code>… Every planning and review step here works that way.
            </p>
          </>
        }
        where={['src/agentflow/agents/coder.py', 'src/agentflow/schemas.py', 'src/agentflow/models.py']}
      >
        <ToolsWidget />
      </Concept>

      <Concept
        id="rag"
        title="RAG: letting the model see the right code"
        plain={
          <>
            <p>
              The repo is too big to paste into every prompt. <Term t="RAG" /> means: search first, paste only what’s
              relevant. Here code is cut into chunks along function/class boundaries, each chunk becomes an{' '}
              <Term t="embedding" />, and both the vectors and a keyword index live in Postgres.
            </p>
            <p>
              A search runs <Term t="vector search" /> (meaning) and <Term t="full-text search" /> (exact words), then
              merges the two lists with <Term t="RRF" />. Agents call this as a tool whenever they need context.
            </p>
          </>
        }
        where={['src/agentflow/rag/chunking.py', 'src/agentflow/rag/store.py', 'src/agentflow/rag/retriever.py']}
      >
        <RagWidget />
      </Concept>

      <Concept
        id="state"
        title="State, checkpoints & human-in-the-loop"
        plain={
          <>
            <p>
              The workflow is a <Term t="LangGraph" /> graph: steps share one state object, and after every step a{' '}
              <Term t="checkpoint" /> is written to Postgres. That is what makes pausing cheap: at the spec and the plan
              a node calls <Term t="interrupt" />, the process can even exit, and a later approval resumes from the
              exact snapshot. The same mechanism asks a human what to do when a task runs out of budget.
            </p>
          </>
        }
        where={['src/agentflow/graph/state.py', 'src/agentflow/runner.py', 'src/agentflow/graph/nodes.py']}
      >
        <HitlWidget />
      </Concept>

      <Concept
        id="routing"
        title="Routing, budgets & recovery"
        plain={
          <>
            <p>
              After each review, plain Python decides where to go next — no LLM involved. Failures are counted in
              separate budgets, because they mean different things: a red test is objective, a reviewer’s “no” is a
              judgement, a reviewer crash says nothing about the code.
            </p>
            <p>
              If the same failure comes back a third time within the last few attempts — in a row or alternating with another one — retrying is pointless; a referee agent decides whether the{' '}
              <i>test</i> is the problem. If the budget still runs out, the run <b>escalates</b>: it pauses with the
              evidence and a human chooses retry-with-hint, re-plan, skip or stop.
            </p>
          </>
        }
        where={['src/agentflow/graph/routing.py', 'src/agentflow/config.py']}
      >
        <RoutingWidget />
      </Concept>

      <Concept
        id="memory"
        title="Memory: short-term vs long-term"
        plain={
          <>
            <p>
              Short-term memory is the run’s own state (the checkpoints above) — it ends with the run. Long-term memory
              outlives it: when a reviewer’s change request leads to an approved fix, that request is stored as a
              “lesson” for this repository and searched by meaning next time a coder starts a similar task.
            </p>
          </>
        }
        where={['src/agentflow/memory/lessons.py', 'src/agentflow/agents/coder.py']}
      >
        <MemoryWidget />
      </Concept>

      <Concept
        id="fallbacks"
        title="Retries & fallbacks"
        plain={
          <>
            <p>
              Three layers, from small to large: the HTTP client retries a flaky request; a <Term t="fallback" /> sends
              the same request to another provider if the first one errors; and graph nodes have their own retry policy
              and timeout. Flip the switches to see which layer saves the run.
            </p>
          </>
        }
        where={['src/agentflow/models.py', 'src/agentflow/graph/build.py', 'tests/test_providers_live.py']}
      >
        <FallbackWidget />
      </Concept>

      <Concept
        id="evals"
        title="Evals & tracing"
        plain={
          <>
            <p>
              Tests check code; evals check behaviour that isn’t exactly right or wrong. Retrieval quality is measured
              on a fixed set of questions with known answers. Spec quality is graded by another model with a rubric (
              <Term t="LLM-as-judge" />
              ). Every call is recorded as a <Term t="trace" /> in LangSmith — the replay above is built from one.
            </p>
          </>
        }
        where={['evals/retrieval_eval.py', 'evals/spec_eval.py', 'evals/e2e_eval.py']}
      >
        <EvalsWidget />
      </Concept>
    </Section>
  )
}

// ─── Tools ──────────────────────────────────────────────────────────────────

const SCHEMAS = new Set(['SpecDraft', 'TaskPlan', 'GeneratedTest', 'ReviewDecision', 'FailureRuling'])

function ToolsWidget() {
  // Pair each model call that requested tools with the tool runs that followed it.
  const examples = useMemo(() => {
    const out: { call: LlmCall; runs: ToolRun[]; label: string }[] = []
    calls.forEach((c, k) => {
      if (c.type !== 'llm' || c.output.tool_calls.length === 0) return
      const runs: ToolRun[] = []
      for (let j = k + 1; j < calls.length && calls[j].type === 'tool'; j++) runs.push(calls[j] as ToolRun)
      const names = c.output.tool_calls.map((t) => t.name)
      const role = c.tags.find((t) => ['coder', 'test-generator', 'orchestrator', 'spec', 'tasks'].includes(t)) ?? 'reviewer'
      out.push({ call: c, runs, label: `${role}: ${names.join(', ')}` })
    })
    return out
  }, [])
  const [k, setK] = useState(() => Math.max(0, examples.findIndex((e) => e.label.includes('search_codebase'))))
  const ex = examples[k]
  const structured = ex.call.output.tool_calls.some((t) => SCHEMAS.has(t.name))
  return (
    <Card>
      <label className="text-[12px] font-semibold uppercase tracking-wide text-muted">
        Pick a real call from the run
        <select className="mt-1 block w-full rounded-lg border border-line bg-panel-2 px-2 py-1.5 text-[13px] font-normal normal-case tracking-normal text-ink" value={k} onChange={(e) => setK(Number(e.target.value))}>
          {examples.map((e, i) => (
            <option key={i} value={i}>
              {e.label}
            </option>
          ))}
        </select>
      </label>
      <ol className="mt-4 space-y-4 text-[13px]">
        <li>
          <b>1 · The model answered</b> <span className="text-muted">({ex.call.model}, {ex.call.latency_s}s) — not prose, but:</span>
          <Code maxH="max-h-56">{JSON.stringify(ex.call.output.tool_calls, null, 2)}</Code>
        </li>
        {structured ? (
          <li>
            <b>2 · This one is a structured output.</b>{' '}
            <span className="text-muted">
              The “tool” is a Pydantic schema. The arguments above are validated into a Python object, and the graph
              continues with real typed data — no parsing of free text.
            </span>
          </li>
        ) : (
          <li>
            <b>2 · Our code ran it</b> <span className="text-muted">and sent the result back to the model:</span>
            {ex.runs.length ? (
              ex.runs.map((r, i) => (
                <div key={i} className="mt-2">
                  <code className="text-accent">{r.name}</code>
                  <Code maxH="max-h-40">{r.output || '(empty)'}</Code>
                </div>
              ))
            ) : (
              <p className="text-muted">(tool output not captured)</p>
            )}
          </li>
        )}
        <li>
          <b>3 · Repeat</b> <span className="text-muted">until the model answers without a tool call — that’s the agent loop. A call limit stops runaway loops.</span>
        </li>
      </ol>
    </Card>
  )
}

// ─── RAG ────────────────────────────────────────────────────────────────────

function RagWidget() {
  const [tab, setTab] = useState<'search' | 'chunks'>('search')
  return (
    <Card>
      <div className="mb-4 flex gap-1 rounded-lg bg-panel-2 p-1 text-[13px]">
        {(['search', 'chunks'] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`flex-1 rounded-md px-3 py-1 ${tab === t ? 'bg-panel font-medium shadow-sm' : 'text-muted'}`}>
            {t === 'search' ? 'Search playground' : 'How a file is chunked'}
          </button>
        ))}
      </div>
      {tab === 'search' ? <SearchPlayground /> : <ChunkView />}
    </Card>
  )
}

function ChunkView() {
  const { path, chunks } = retrieval.chunk_demo
  const [k, setK] = useState(0)
  return (
    <div>
      <p className="text-[13px] text-muted">
        <code>{path}</code> becomes {chunks.length} chunks, split at function boundaries. Each is stored with its line
        range and embedded together with a “File: …” header.
      </p>
      <div className="my-3 flex flex-wrap gap-1.5">
        {chunks.map((c, i) => (
          <button key={i} onClick={() => setK(i)} className={`rounded-md border px-2 py-0.5 font-mono text-[12px] ${i === k ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted'}`}>
            #{c.index} · L{c.start}–{c.end}
          </button>
        ))}
      </div>
      <Code maxH="max-h-72">{`File: ${path} (lines ${chunks[k].start}-${chunks[k].end})\n\n${chunks[k].content}`}</Code>
    </div>
  )
}

const MODES = ['dense', 'lexical', 'hybrid'] as const
const MODE_LABEL = { dense: 'Vector (meaning)', lexical: 'Keyword', hybrid: 'Fused (RRF)' }

function SearchPlayground() {
  const live = useLive()
  const [repos, setRepos] = useState<string[]>([])
  const [repo, setRepo] = useState('gavrilovdm/agentflow')
  const [q, setQ] = useState(retrieval.cases[0].query)
  const [res, setRes] = useState<SearchResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (live) listRepos().then((r) => setRepos(r.repos)).catch(() => setRepos([]))
  }, [live])

  const recorded = retrieval.cases.find((c) => c.query === q)
  const runLive = async () => {
    setBusy(true)
    setErr(null)
    try {
      setRes(await search(repo, q, 5))
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <span className={`rounded-full px-2 py-0.5 font-medium ${live ? 'bg-ok/15 text-ok' : 'bg-panel-2 text-muted'}`}>
          {live ? '● live: querying your local API' : '○ offline: recorded results'}
        </span>
        {live && (
          <select value={repo} onChange={(e) => setRepo(e.target.value)} className="rounded-md border border-line bg-panel-2 px-2 py-1">
            {(repos.length ? repos : [repo]).map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        )}
      </div>
      <div className="mt-3 flex gap-2">
        {live ? (
          <>
            <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void runLive()} className="min-w-0 flex-1 rounded-lg border border-line bg-panel-2 px-3 py-1.5 text-[13px]" placeholder="Ask about the code…" />
            <button onClick={() => void runLive()} disabled={busy} className="rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-50">
              {busy ? '…' : 'Search'}
            </button>
          </>
        ) : (
          <select value={q} onChange={(e) => setQ(e.target.value)} className="min-w-0 flex-1 rounded-lg border border-line bg-panel-2 px-2 py-1.5 text-[13px]">
            {retrieval.cases.map((c) => (
              <option key={c.query}>{c.query}</option>
            ))}
          </select>
        )}
      </div>
      {err && <p className="mt-2 text-[12px] text-bad">{err}</p>}

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {MODES.map((m) => {
          const items = live && res ? res[m].map((h) => ({ path: h.path, sub: `L${h.start_line}–${h.end_line}` })) : (recorded?.[m] ?? []).map((p) => ({ path: p, sub: '' }))
          return (
            <div key={m} className={`rounded-lg border p-3 ${m === 'hybrid' ? 'border-accent' : 'border-line'}`}>
              <div className="text-[12px] font-semibold">{MODE_LABEL[m]}</div>
              <ol className="mt-2 space-y-1 text-[12px]">
                {items.length === 0 && <li className="text-muted">{live ? 'press Search' : '—'}</li>}
                {items.map((it, i) => {
                  const hit = !live && recorded?.relevant.includes(it.path)
                  return (
                    <li key={i} className={`truncate font-mono ${hit ? 'font-semibold text-ok' : 'text-muted'}`} title={it.path}>
                      {i + 1}. {it.path.replace('agentflow/', '')} {it.sub && <span className="opacity-60">{it.sub}</span>}
                      {hit && ' ✓'}
                    </li>
                  )
                })}
              </ol>
            </div>
          )
        })}
      </div>
      <p className="mt-3 text-[12px] leading-relaxed text-muted">
        {live && res
          ? `Embeddings: ${res.embeddings}. `
          : `✓ = the file a human marked as the right answer. `}
        Embeddings here are {retrieval.embeddings === 'DeterministicFakeEmbedding' ? 'offline placeholders (no Voyage key), so the vector column is effectively random' : 'real'} — notice
        how keyword search still finds identifiers and how fusing in noise can push a good result down. With real code
        embeddings the vector column finds things keywords miss, like “where do we stop runaway loops”.
      </p>
    </div>
  )
}

// ─── HITL ───────────────────────────────────────────────────────────────────

function HitlWidget() {
  const [phase, setPhase] = useState<'waiting' | 'approved' | 'rejected'>('waiting')
  const [feedback, setFeedback] = useState('Return a dataclass instead of a dict from Database.update')
  const checkpoints = run.steps.length + 1
  const state =
    phase === 'waiting'
      ? { spec_approved: false, spec_feedback: null, status: 'spec_review', next: ['await_spec_approval'] }
      : phase === 'approved'
        ? { spec_approved: true, spec_feedback: null, status: 'planning', next: ['generate_tasks'] }
        : { spec_approved: false, spec_feedback: feedback, status: 'spec_review', next: ['generate_spec'] }
  return (
    <Card>
      <div className="text-[13px] text-muted">The real spec from the run is waiting for you:</div>
      <div className="mt-2 rounded-lg border border-line bg-panel-2 p-3 text-[13px]">
        <b>{finalSpec.title}</b> <span className="text-muted">v{finalSpec.version}</span>
        <div className="mt-1 text-muted">
          {finalSpec.acceptance_criteria.length} acceptance criterion · {finalSpec.out_of_scope.length} out-of-scope item —{' '}
          <a href="#what-broke" className="text-accent hover:underline">a human should have rejected this one (bug #8)</a>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={() => setPhase('approved')} className="rounded-lg bg-ok px-3 py-1.5 text-[13px] font-medium text-white">✅ Approve</button>
        <button onClick={() => setPhase('rejected')} className="rounded-lg bg-bad px-3 py-1.5 text-[13px] font-medium text-white">❌ Reject with feedback</button>
        <button onClick={() => setPhase('waiting')} className="rounded-lg border border-line px-3 py-1.5 text-[13px]">reset</button>
      </div>
      <input value={feedback} onChange={(e) => setFeedback(e.target.value)} className="mt-2 w-full rounded-lg border border-line bg-panel-2 px-3 py-1.5 text-[13px]" aria-label="Rejection feedback" />
      <div className="mt-4 text-[12px] font-semibold uppercase tracking-wide text-muted">What the resumed graph sees (simulated)</div>
      <Code maxH="max-h-48">{JSON.stringify(state, null, 2)}</Code>
      <p className="mt-2 text-[13px] text-muted">
        {phase === 'waiting' && 'Paused. Nothing runs and nothing is held in memory — just a row in Postgres.'}
        {phase === 'approved' && '→ routes to generate_tasks. This is exactly what happened in the real run.'}
        {phase === 'rejected' && '→ routes back to generate_spec; the feedback and the previous spec go into the prompt, and v2 comes back for approval.'}
      </p>
      <p className="mt-3 text-[12px] text-muted">
        Real numbers: the run above saved <b className="text-ink">{checkpoints}</b> checkpoints. The REST API, the Telegram
        button and the MCP tool all resume the same way, and a resume is keyed by checkpoint id, so a double-click can’t
        resume twice.
      </p>
    </Card>
  )
}

// ─── Routing simulator ──────────────────────────────────────────────────────

function Slider({ label, value, max, onChange }: { label: string; value: number; max: number; onChange: (v: number) => void }) {
  return (
    <label className="block text-[13px]">
      <span className="flex justify-between">
        <span>{label}</span>
        <b className="tabular-nums">
          {value}
          <span className="font-normal text-muted"> / {max}</span>
        </b>
      </span>
      <input type="range" min={0} max={max} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
    </label>
  )
}

const NEXT_COLOR: Record<string, string> = { complete_task: 'var(--ok)', run_coder: 'var(--coder)', adjudicate: 'var(--opus)', escalate: 'var(--human)' }

function RoutingWidget() {
  const b = DEFAULT_BUDGETS
  const [approved, setApproved] = useState(false)
  const [gate, setGate] = useState(1)
  const [rev, setRev] = useState(0)
  const [mal, setMal] = useState(0)
  const [rep, setRep] = useState(0)
  const [adj, setAdj] = useState(false)
  const d = afterReview(
    { coderFixAttempts: 0, gateFailures: gate, reviewCycles: rev, reviewerMalfunctions: mal },
    { approved, stallRepeats: rep, adjudicated: adj },
    b,
  )
  return (
    <Card>
      <p className="text-[13px] text-muted">
        A task just came back from <code>run_review</code>. Set its history and see which edge the graph takes — this is
        a line-for-line port of <code>routing.py</code>, tested against the same cases.
      </p>
      <label className="mt-3 flex items-center gap-2 text-[13px]">
        <input type="checkbox" checked={approved} onChange={(e) => setApproved(e.target.checked)} /> reviewer approved this attempt
      </label>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Slider label="gate failures (tests/lint red)" value={gate} max={b.maxGateFailures} onChange={setGate} />
        <Slider label="review rejections" value={rev} max={b.maxReviewCycles} onChange={setRev} />
        <Slider label="reviewer malfunctions" value={mal} max={b.maxReviewerMalfunctions} onChange={setMal} />
        <Slider label="same failure seen before (recent attempts)" value={rep} max={3} onChange={setRep} />
      </div>
      <label className="mt-2 flex items-center gap-2 text-[13px]">
        <input type="checkbox" checked={adj} onChange={(e) => setAdj(e.target.checked)} /> the referee already ruled on this task
      </label>
      <div className="mt-4 rounded-lg border-2 p-3" style={{ borderColor: NEXT_COLOR[d.next] }}>
        <div className="text-[12px] uppercase tracking-wide text-muted">next node</div>
        <div className="font-mono text-lg font-semibold" style={{ color: NEXT_COLOR[d.next] }}>
          {d.next}
        </div>
        <div className="text-[13px]">{d.reason}</div>
      </div>
    </Card>
  )
}

// ─── Memory ─────────────────────────────────────────────────────────────────

function MemoryWidget() {
  const [step, setStep] = useState(0)
  const lesson = 'calc/ops.py: public functions need a docstring'
  const stages = [
    { t: 'Run 1 · reviewer rejects', body: `{\n  "verdict": "changes_requested",\n  "change_requests": ["${lesson}"]\n}` },
    { t: 'Run 1 · coder fixes it, reviewer approves → saved', body: `store.put(("lessons", "<repo>"), key, {\n  "text": "${lesson}",\n  "task": "add()"\n})` },
    { t: 'Run 2 · a new task starts → recalled by meaning', body: `## Lessons from past reviews of this repository\n- ${lesson} (from task: add())` },
  ]
  return (
    <Card>
      <div className="flex gap-1">
        {stages.map((_, i) => (
          <button key={i} onClick={() => setStep(i)} className={`flex-1 rounded-md border px-2 py-1 text-[12px] ${i === step ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted'}`}>
            {i + 1}
          </button>
        ))}
      </div>
      <div className="mt-3 text-[13px] font-medium">{stages[step].t}</div>
      <Code maxH="max-h-48">{stages[step].body}</Code>
      <p className="mt-3 text-[12px] text-muted">
        This exact flow is an end-to-end test (<code>test_review_feedback_becomes_lesson</code>). In the real run above
        nothing was rejected, so no lesson was stored. Only feedback that was <i>followed by an approval</i> is kept —
        that filters out reviewer noise.
      </p>
    </Card>
  )
}

// ─── Fallbacks ──────────────────────────────────────────────────────────────

function FallbackWidget() {
  const [flaky, setFlaky] = useState(false)
  const [primaryDown, setPrimaryDown] = useState(false)
  const [secondaryDown, setSecondaryDown] = useState(false)
  const layers = [
    { name: 'HTTP client retry (×3, backoff)', ok: !primaryDown, note: flaky && !primaryDown ? 'a 529/timeout is retried and succeeds' : primaryDown ? 'provider keeps failing — retries can’t help' : 'request succeeds first time' },
    { name: 'Fallback model (other provider)', ok: !primaryDown || !secondaryDown, used: primaryDown, note: primaryDown ? (secondaryDown ? 'also down' : 'the same request goes to the configured fallback model (in this setup: DeepSeek)') : 'not needed' },
    { name: 'Node RetryPolicy + timeout', ok: !primaryDown || !secondaryDown, used: primaryDown && secondaryDown, note: primaryDown && secondaryDown ? 'retries the whole step a few times, then gives up' : 'not needed' },
  ]
  const outcome = !primaryDown ? 'run continues' : !secondaryDown ? 'run continues on the fallback model' : 'run marked failed + Telegram alert (no silent hang)'
  return (
    <Card>
      <div className="flex flex-wrap gap-4 text-[13px]">
        <label className="flex items-center gap-2"><input type="checkbox" checked={flaky} onChange={(e) => setFlaky(e.target.checked)} /> flaky network</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={primaryDown} onChange={(e) => setPrimaryDown(e.target.checked)} /> primary down (e.g. out of credit)</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={secondaryDown} onChange={(e) => setSecondaryDown(e.target.checked)} /> fallback down too</label>
      </div>
      <ol className="mt-4 space-y-2">
        {layers.map((l) => (
          <li key={l.name} className="rounded-lg border border-line p-3 text-[13px]">
            <div className="font-medium">{l.name}</div>
            <div className="text-muted">{l.note}</div>
          </li>
        ))}
      </ol>
      <div className={`mt-3 rounded-lg p-3 text-[13px] font-medium ${primaryDown && secondaryDown ? 'bg-bad/10 text-bad' : 'bg-ok/10 text-ok'}`}>→ {outcome}</div>
      <p className="mt-3 text-[12px] text-muted">
        Not hypothetical: the real Anthropic account ran out of credit mid-test. That is how we found the fallback had
        been broken all along — see “What broke”, below.
      </p>
    </Card>
  )
}

// ─── Evals ──────────────────────────────────────────────────────────────────

function EvalsWidget() {
  const s = retrieval.summary
  return (
    <Card>
      <div className="text-[13px] font-medium">Retrieval eval — {retrieval.cases.length} questions about this codebase, recall@{retrieval.k}</div>
      <div className="mt-3 space-y-2">
        {MODES.map((m) => (
          <div key={m} className="text-[13px]">
            <div className="flex justify-between">
              <span>{MODE_LABEL[m]}</span>
              <span className="tabular-nums text-muted">recall {s[m].recall.toFixed(2)} · MRR {s[m].mrr.toFixed(2)}</span>
            </div>
            <div className="mt-1 h-2 rounded-full bg-panel-2">
              <div className="h-2 rounded-full bg-accent" style={{ width: `${s[m].recall * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[12px] text-muted">
        Recall@5: “is the right file in the top 5?”. MRR: “how high up, on average?”. With placeholder embeddings the
        vector half is noise, which is why CI gates on the keyword half offline and on fused search once real embeddings
        are configured. Gating on the noisy number made CI flip with unrelated code changes — a real bug fixed while
        building this guide.
      </p>
      <ul className="mt-3 space-y-1 text-[13px]">
        <li>• <b>Spec eval</b>: an LLM judge scores specs 1–5 for testable / grounded / scoped.</li>
        <li>• <b>End-to-end eval</b>: real models on a fixture repo; tracks completion rate, gate failures, review cycles.</li>
        <li>• <b>Live smoke test</b>: one structured call per model, each provider on its own.</li>
      </ul>
    </Card>
  )
}

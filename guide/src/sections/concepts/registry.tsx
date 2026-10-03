// The concepts section as data: adding a concept means adding an entry here (and its
// widget file), not editing the section component.
import type { ComponentType, ReactNode } from 'react'
import { Term } from '../../components/ui'
import type { ConceptId } from '../../lib/content'
import { ToolsWidget } from './ToolsWidget'
import { RagWidget } from './RagWidget'
import { HitlWidget } from './HitlWidget'
import { RoutingWidget } from './RoutingWidget'
import { MemoryWidget } from './MemoryWidget'
import { FallbackWidget } from './FallbackWidget'
import { EvalsWidget } from './EvalsWidget'

export interface ConceptEntry {
  id: ConceptId
  title: string
  plain: ReactNode
  where: string[]
  Widget: ComponentType
}

export const CONCEPTS: ConceptEntry[] = [
  {
    id: 'tools',
    title: 'Tool calling & structured output',
    where: ['src/agentflow/agents/coder.py', 'src/agentflow/schemas.py', 'src/agentflow/models.py'],
    Widget: ToolsWidget,
    plain: (
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
    ),
  },
  {
    id: 'rag',
    title: 'RAG: letting the model see the right code',
    where: ['src/agentflow/rag/chunking.py', 'src/agentflow/rag/store.py', 'src/agentflow/rag/retriever.py'],
    Widget: RagWidget,
    plain: (
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
    ),
  },
  {
    id: 'state',
    title: 'State, checkpoints & human-in-the-loop',
    where: ['src/agentflow/graph/state.py', 'src/agentflow/runner.py', 'src/agentflow/graph/nodes.py'],
    Widget: HitlWidget,
    plain: (
<>
            <p>
              The workflow is a <Term t="LangGraph" /> graph: steps share one state object, and after every step a{' '}
              <Term t="checkpoint" /> is written to Postgres. That is what makes pausing cheap: at the spec and the plan
              a node calls <Term t="interrupt" />, the process can even exit, and a later approval resumes from the
              exact snapshot. The same mechanism asks a human what to do when a task runs out of budget.
            </p>
          </>
    ),
  },
  {
    id: 'routing',
    title: 'Routing, budgets & recovery',
    where: ['src/agentflow/graph/routing.py', 'src/agentflow/config.py'],
    Widget: RoutingWidget,
    plain: (
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
    ),
  },
  {
    id: 'memory',
    title: 'Memory: short-term vs long-term',
    where: ['src/agentflow/memory/lessons.py', 'src/agentflow/agents/coder.py'],
    Widget: MemoryWidget,
    plain: (
<>
            <p>
              Short-term memory is the run’s own state (the checkpoints above) — it ends with the run. Long-term memory
              outlives it: when a reviewer’s change request leads to an approved fix, that request is stored as a
              “lesson” for this repository and searched by meaning next time a coder starts a similar task.
            </p>
          </>
    ),
  },
  {
    id: 'fallbacks',
    title: 'Retries & fallbacks',
    where: ['src/agentflow/models.py', 'src/agentflow/graph/build.py', 'tests/test_providers_live.py'],
    Widget: FallbackWidget,
    plain: (
<>
            <p>
              Three layers, from small to large: the HTTP client retries a flaky request; a <Term t="fallback" /> sends
              the same request to another provider if the first one errors; and graph nodes have their own retry policy
              and timeout. Flip the switches to see which layer saves the run.
            </p>
          </>
    ),
  },
  {
    id: 'evals',
    title: 'Evals & tracing',
    where: ['evals/retrieval_eval.py', 'evals/spec_eval.py', 'evals/e2e_eval.py'],
    Widget: EvalsWidget,
    plain: (
<>
            <p>
              Tests check code; evals check behaviour that isn’t exactly right or wrong. Retrieval quality is measured
              on a fixed set of questions with known answers. Spec quality is graded by another model with a rubric (
              <Term t="LLM-as-judge" />
              ). Every call is recorded as a <Term t="trace" /> in LangSmith — the replay above is built from one.
            </p>
          </>
    ),
  },
]

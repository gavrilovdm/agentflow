import { Code } from '../../components/ui'
import type { Call } from '../../lib/data'

const modelColor = (m: string | null) => (m?.startsWith('deepseek') ? 'var(--coder)' : 'var(--opus)')

/** The model and tool calls made while one graph step ran, from the LangSmith trace. */
export function CallList({ calls }: { calls: Call[] }) {
  if (calls.length === 0) return null
  return (
    <div className="mt-5 border-t border-line pt-4">
      <div className="text-[12px] font-semibold uppercase tracking-wide text-muted">
        Model & tool calls in this step ({calls.length}, from the trace)
      </div>
      <ul className="mt-2 max-h-96 space-y-1.5 overflow-auto">
        {calls.map((c) =>
          c.type === 'llm' ? (
            <li key={`${c.t}-llm`}>
              <details className="rounded-lg border border-line px-3 py-1.5 text-[13px]">
                <summary className="cursor-pointer">
                  <b style={{ color: modelColor(c.model) }}>{c.model}</b>{' '}
                  <span className="text-muted">
                    {c.latency_s}s · {c.tokens} tok →{' '}
                  </span>
                  {c.output.tool_calls.length ? (
                    c.output.tool_calls.map((t, i) => (
                      <code key={`${t.name}-${i}`} className="mr-1">
                        {t.name}()
                      </code>
                    ))
                  ) : (
                    <span className="text-muted">text answer</span>
                  )}
                </summary>
                <Code maxH="max-h-60">
                  {c.output.tool_calls.length ? JSON.stringify(c.output.tool_calls, null, 2) : c.output.content || '(empty)'}
                </Code>
              </details>
            </li>
          ) : (
            <li key={`${c.t}-tool-${c.name}`} className="pl-3 text-[13px] text-muted">
              ↳ ran <code className="text-ink">{c.name}</code> → {c.output.slice(0, 90)}
              {c.output.length > 90 ? '…' : ''}
            </li>
          ),
        )}
      </ul>
    </div>
  )
}

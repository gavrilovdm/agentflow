import { useEffect, useState } from 'react'
import { Card, Code } from '../../components/ui'
import { listRepos, search, type SearchResult } from '../../lib/api'
import { retrieval } from '../../lib/data'
import { useLive } from '../../lib/live'
import { MODE_LABEL, MODES } from './modes'

export function RagWidget() {
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

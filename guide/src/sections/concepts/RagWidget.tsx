import { useEffect, useState } from 'react'
import { Card, Code } from '../../components/ui'
import { listRepos, search } from '../../lib/api'
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


type Rows = Record<(typeof MODES)[number], { path: string; sub: string }[]>

const toRows = <H,>(r: Record<(typeof MODES)[number], H[]>, lines: (h: H) => [number, number], path: (h: H) => string): Rows =>
  Object.fromEntries(MODES.map((m) => [m, r[m].map((h) => ({ path: path(h), sub: `L${lines(h)[0]}–${lines(h)[1]}` }))])) as Rows

function SearchPlayground() {
  const live = useLive()
  const [repos, setRepos] = useState<string[]>([])
  const [repo, setRepo] = useState('gavrilovdm/agentflow')
  const [q, setQ] = useState(retrieval.cases[0].query)
  const [rows, setRows] = useState<Rows | null>(null)
  const [embeddings, setEmbeddings] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [download, setDownload] = useState<string | null>(null)

  useEffect(() => {
    if (live) listRepos().then((r) => setRepos(r.repos)).catch(() => setRepos([]))
  }, [live])

  const golden = retrieval.cases.find((c) => c.query === q.trim())
  const run = async (query = q) => {
    if (!query.trim()) return
    setBusy(true)
    setErr(null)
    try {
      if (live) {
        const res = await search(repo, query, 5)
        setRows(toRows(res, (h) => [h.start_line, h.end_line], (h) => h.path))
        setEmbeddings(res.embeddings)
      } else {
        const { browserSearch, loadBrowserRag } = await import('../../lib/browserRag')
        const { meta } = await loadBrowserRag((got, total) => setDownload(`${got.toFixed(1)} / ${total.toFixed(1)} MB`))
        setDownload(null)
        setRows(toRows(await browserSearch(query, 5), (h) => [h.start, h.end], (h) => h.path))
        setEmbeddings(meta.model.split('/')[1])
      }
    } catch (e) {
      setErr(live ? String(e) : `Could not load the in-browser model (${String(e)}). It downloads from huggingface.co once.`)
    } finally {
      setBusy(false)
      setDownload(null)
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <span className={`rounded-full px-2 py-0.5 font-medium ${live ? 'bg-ok/15 text-ok' : 'bg-accent-soft text-accent'}`}>
          {live ? '● live: querying your local API' : '● in your browser: small embedding model, no server'}
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
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void run()}
          className="min-w-0 flex-1 rounded-lg border border-line bg-panel-2 px-3 py-1.5 text-[13px]"
          placeholder="Ask about the code…"
        />
        <button onClick={() => void run()} disabled={busy} className="rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-50">
          {busy ? '…' : 'Search'}
        </button>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {retrieval.cases.slice(0, 6).map((c) => (
          <button
            key={c.query}
            type="button"
            onClick={() => {
              setQ(c.query)
              void run(c.query)
            }}
            className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted hover:text-ink"
          >
            {c.query}
          </button>
        ))}
      </div>
      {download && <p className="mt-2 text-[12px] text-muted">Downloading the model, once: {download}</p>}
      {err && <p className="mt-2 text-[12px] text-bad">{err}</p>}

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {MODES.map((m) => {
          const items = rows?.[m] ?? []
          return (
            <div key={m} className={`rounded-lg border p-3 ${m === 'hybrid' ? 'border-accent' : 'border-line'}`}>
              <div className="text-[12px] font-semibold">{MODE_LABEL[m]}</div>
              <ol className="mt-2 space-y-1 text-[12px]">
                {items.length === 0 && <li className="text-muted">{busy ? 'searching…' : 'press Search'}</li>}
                {items.map((it, i) => {
                  const hit = golden?.relevant.includes(it.path)
                  return (
                    <li key={i} className={`truncate font-mono ${hit ? 'font-semibold text-ok' : 'text-muted'}`} title={it.path}>
                      {i + 1}. {it.path.replace('agentflow/', '')} <span className="opacity-60">{it.sub}</span>
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
        {embeddings && <>Embeddings: {embeddings}. </>}
        {golden && '✓ = the file a human marked as the right answer. '}
        {live
          ? 'Your local API searches the Postgres index with the configured embeddings (voyage-code-3 in production).'
          : 'Search runs in this tab: the query is embedded by a 34 MB model (gte-small), compared with prebuilt vectors of this repo’s 166 chunks, and fused with keyword search by RRF — the same algorithm as the server. Production uses voyage-code-3, a much larger model trained on code.'}{' '}
        Notice how keyword search nails identifiers while the vector column finds things by meaning, like “where do we stop runaway loops”.
      </p>
    </div>
  )
}

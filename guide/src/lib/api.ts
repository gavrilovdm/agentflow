// Live mode: when the local agentflow API is running, parts of the guide query it for real.
export const API_URL = (import.meta.env.VITE_AGENTFLOW_API as string | undefined) ?? 'http://localhost:8010'

export interface SearchHit {
  path: string
  start_line: number
  end_line: number
  kind: string
  score: number
  snippet: string
}

export interface SearchResult {
  repo: string
  query: string
  embeddings: string
  dense: SearchHit[]
  lexical: SearchHit[]
  hybrid: SearchHit[]
}

async function get<T>(path: string, timeoutMs = 4000): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetch(`${API_URL}${path}`, { signal: ctrl.signal })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return (await res.json()) as T
  } finally {
    clearTimeout(timer)
  }
}

export async function isLive(): Promise<boolean> {
  try {
    const h = await get<{ status: string }>('/healthz', 1500)
    return h.status === 'ok'
  } catch {
    return false
  }
}

export const listRepos = () => get<{ repos: string[] }>('/repos')
export const search = (repo: string, q: string, k = 5) =>
  get<SearchResult>(`/search?repo=${encodeURIComponent(repo)}&q=${encodeURIComponent(q)}&k=${k}`, 15000)
export const getRun = (threadId: string) => get<Record<string, unknown>>(`/runs/${encodeURIComponent(threadId)}`)

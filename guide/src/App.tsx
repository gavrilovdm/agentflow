import { Concepts } from './sections/concepts/Concepts'
import { Hero } from './sections/Hero'
import { Replay } from './sections/replay/Replay'
import { CodeMap } from './sections/CodeMap'
import { Failures } from './sections/Failures'
import { Interview } from './sections/Interview'
import { UseCases } from './sections/UseCases'
import { API_URL } from './lib/api'
import { LiveProvider, useLive } from './lib/live'

const NAV = [
  ['replay', 'Replay'],
  ['concepts', 'Concepts'],
  ['use-cases', 'Use cases'],
  ['what-broke', 'What broke'],
  ['code-map', 'Code map'],
  ['interview', 'Interview'],
] as const

function Nav() {
  const live = useLive()
  return (
    <nav className="sticky top-0 z-20 border-b border-line bg-bg/85 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center gap-4 overflow-x-auto px-4 py-3 text-[13px] sm:px-6">
        <a href="#top" className="shrink-0 font-bold">agentflow</a>
        {NAV.map(([id, label]) => (
          <a key={id} href={`#${id}`} className="shrink-0 text-muted hover:text-ink">
            {label}
          </a>
        ))}
        <span
          title={live ? `Connected to ${API_URL}` : `Start the API (docker compose up) to enable live mode at ${API_URL}`}
          className={`ml-auto shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${live ? 'bg-ok/15 text-ok' : 'bg-panel-2 text-muted'}`}
        >
          {live ? '● live API' : '○ recorded data'}
        </span>
      </div>
    </nav>
  )
}

export default function App() {
  return (
    <LiveProvider>
      <div id="top" />
      <Nav />
      <main>
        <Hero />
        <Replay />
        <Concepts />
        <UseCases />
        <Failures />
        <CodeMap />
        <Interview />
      </main>
      <footer className="border-t border-line py-8 text-center text-[12px] text-muted">
        Data captured from real runs by <code>scripts/export_guide_data.py</code> ·{' '}
        <a className="hover:text-accent" href="https://github.com/gavrilovdm/agentflow" target="_blank" rel="noreferrer">
          github.com/gavrilovdm/agentflow
        </a>
      </footer>
    </LiveProvider>
  )
}

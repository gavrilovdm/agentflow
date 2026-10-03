import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { GITHUB } from '../lib/data'
import { GLOSSARY } from '../lib/content'

export function Section({ id, kicker, title, intro, children }: { id: string; kicker: string; title: string; intro?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} className="mx-auto max-w-6xl px-4 py-14 sm:px-6">
      <p className="text-xs font-semibold uppercase tracking-widest text-accent">{kicker}</p>
      <h2 className="mt-1 text-2xl font-bold tracking-tight sm:text-3xl">{title}</h2>
      {intro && <div className="mt-3 max-w-3xl text-[15px] leading-relaxed text-muted">{intro}</div>}
      <div className="mt-8">{children}</div>
    </section>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`min-w-0 rounded-xl border border-line bg-panel p-5 ${className}`}>{children}</div>
}

/** A glossary term: dotted underline, definition on hover / focus / tap. */
const TOOLTIP_HALF_WIDTH = 140 // half of w-64 (256px) + margin

export function Term({ t, children }: { t: keyof typeof GLOSSARY | string; children?: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [align, setAlign] = useState<'center' | 'left' | 'right'>('center')
  const ref = useRef<HTMLButtonElement>(null)
  // Keep the tooltip on screen: near a viewport edge, anchor it to that edge instead of centring.
  useLayoutEffect(() => {
    if (!open || !ref.current) return
    const r = ref.current.getBoundingClientRect()
    const mid = r.left + r.width / 2
    setAlign(mid < TOOLTIP_HALF_WIDTH ? 'left' : window.innerWidth - mid < TOOLTIP_HALF_WIDTH ? 'right' : 'center')
  }, [open])
  const def = GLOSSARY[t as string]
  if (!def) return <>{children ?? t}</>
  const position = { center: 'left-1/2 -translate-x-1/2', left: 'left-0', right: 'right-0' }[align]
  return (
    <span className="relative inline-block">
      <button
        ref={ref}
        type="button"
        className="cursor-help border-b border-dotted border-muted text-inherit"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        {children ?? t}
      </button>
      {open && (
        <span role="tooltip" className={`absolute bottom-full z-30 mb-2 w-64 max-w-[calc(100vw-2rem)] rounded-lg border border-line bg-panel p-3 text-left text-[13px] font-normal leading-snug text-ink shadow-lg ${position}`}>
          <b className="block text-accent">{t}</b>
          {def}
        </span>
      )}
    </span>
  )
}

export function FileLink({ path, label }: { path: string; label?: string }) {
  return (
    <a href={GITHUB + path} target="_blank" rel="noreferrer" className="font-mono text-[12px] text-accent hover:underline">
      {label ?? path}
    </a>
  )
}

export function Code({ children, maxH = 'max-h-80', lang }: { children: string; maxH?: string; lang?: string }) {
  return (
    <div className="relative">
      {lang && <span className="absolute right-2 top-1.5 text-[10px] uppercase tracking-wide text-muted">{lang}</span>}
      <pre className={`${maxH} overflow-auto rounded-lg border border-line bg-panel-2 p-3 text-[12px] leading-relaxed`}>{children}</pre>
    </div>
  )
}

export function CopyCode({ children }: { children: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="relative">
      <pre className="overflow-auto rounded-lg border border-line bg-panel-2 p-3 pr-16 text-[12px] leading-relaxed">{children}</pre>
      <button
        type="button"
        onClick={() => {
          void navigator.clipboard.writeText(children)
          setCopied(true)
          setTimeout(() => setCopied(false), 1200)
        }}
        className="absolute right-2 top-2 rounded-md border border-line bg-panel px-2 py-0.5 text-[11px] text-muted hover:text-ink"
      >
        {copied ? 'copied' : 'copy'}
      </button>
    </div>
  )
}

export const ACTOR_COLOR: Record<string, string> = {
  You: 'var(--human)',
  Telegram: 'var(--human)',
  Python: 'var(--py)',
  'Python + Opus': 'var(--opus)',
}
export function actorColor(who: string): string {
  if (ACTOR_COLOR[who]) return ACTOR_COLOR[who]
  if (who.includes('DeepSeek')) return 'var(--coder)'
  if (who.includes('Opus')) return 'var(--opus)'
  return 'var(--py)'
}

export function Pill({ children, color }: { children: ReactNode; color?: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium"
      style={{ borderColor: color ?? 'var(--line)', color: color ?? 'var(--muted)' }}
    >
      {children}
    </span>
  )
}

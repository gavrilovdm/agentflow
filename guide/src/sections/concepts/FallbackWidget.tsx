import { useState } from 'react'
import { Card } from '../../components/ui'

export function FallbackWidget() {
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

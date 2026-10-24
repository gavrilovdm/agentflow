import { Card } from '../../components/ui'

export function EvalsWidget() {
  return (
    <Card>
      <div className="text-[13px] font-medium">What is measured</div>
      <ul className="mt-3 space-y-1 text-[13px]">
        <li>• <b>Spec eval</b>: an LLM judge scores specs 1–5 for testable / grounded / scoped.</li>
        <li>• <b>End-to-end eval</b>: real models on a fixture repo; tracks completion rate, gate failures, review cycles.</li>
        <li>• <b>Live smoke test</b>: one structured call per model, each provider on its own.</li>
      </ul>
    </Card>
  )
}

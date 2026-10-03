import { FileLink, Section } from '../components/ui'
import { CODE_MAP } from '../lib/content'

export function CodeMap() {
  return (
    <Section id="code-map" kicker="5 · Find your way" title="Code map" intro="Where each idea lives. All links open the file on GitHub.">
      <div className="overflow-hidden rounded-xl border border-line">
        <table className="w-full text-left text-[14px]">
          <tbody>
            {CODE_MAP.map((r) => (
              <tr key={r.concept} className="border-b border-line last:border-0 odd:bg-panel even:bg-panel-2/50">
                <td className="w-2/5 px-4 py-2.5 align-top font-medium">{r.concept}</td>
                <td className="px-4 py-2.5">
                  <div className="flex flex-col gap-0.5">
                    {r.files.map((f) => (
                      <FileLink key={f} path={f} />
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  )
}

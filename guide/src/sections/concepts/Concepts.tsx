import { Section } from '../../components/ui'
import { Concept } from './Concept'
import { CONCEPTS } from './registry'

export function Concepts() {
  return (
    <Section
      id="concepts"
      kicker="2 · The ideas"
      title="Seven concepts, each with something to try"
      intro="Every AI-agent buzzword in the job description shows up somewhere in this project. Here is what each one means in plain terms, where it lives, and a small interactive version of it."
    >
      {CONCEPTS.map(({ Widget, ...c }) => (
        <Concept key={c.id} {...c}>
          <Widget />
        </Concept>
      ))}
    </Section>
  )
}

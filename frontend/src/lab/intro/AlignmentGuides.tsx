import type { CSSProperties } from 'react'
import type { IntroPhase } from './useIntroFlow'
import { INTRO_COPY as C } from './copy'

/** Real debug switch: these guides ship with the demo, not just the screenshot harness. */
export function AlignmentGuides({ phase }: { phase: IntroPhase }) {
  const lines = ['50%']
  if (phase === 'home') lines.push('var(--home-left)','var(--home-send-right)')
  if (phase === 'key') lines.push('calc(50% - min(210px,45vw) + 25px)', 'calc(50% + min(210px,45vw) - 25px)')
  if (['unlock','platforms','clearing','reveal','gather'].includes(phase)) lines.push('var(--platform-ruler-left)','var(--platform-list-left)','var(--platform-text-left)','var(--platform-actions-left)')
  return <div className="intro-alignment" aria-hidden>
    {lines.map((left,i)=><i key={left} className="intro-align-v" style={{left} as CSSProperties}>{i===0?<span>{C.alignCenter}</span>:null}</i>)}
    <i className="intro-align-mid" />
    {phase!=='home' && <i className="intro-align-h" />}
  </div>
}

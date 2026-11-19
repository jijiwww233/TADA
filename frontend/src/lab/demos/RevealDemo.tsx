import { useState } from 'react'
import { Replay } from '../Demo'
import { RevealHeadline, type Lead } from '../title'

/** The round-4 半调 entrance, then the near-focus patch under the pointer. 中文主 / 英文主 switch. */
export default function RevealDemo() {
  const [k, setK] = useState(0)
  const [lead, setLead] = useState<Lead>('zh')
  return (
    <div className="relative flex h-[420px] items-center bg-[var(--page)] px-16">
      <Replay onClick={() => setK(k + 1)} />
      <div className="absolute bottom-4 right-5 flex gap-1.5">
        <button type="button" className={`lab-btn ${lead === 'zh' ? 'is-on' : ''}`} onClick={() => { setLead('zh'); setK(k + 1) }}>中文主</button>
        <button type="button" className={`lab-btn ${lead === 'en' ? 'is-on' : ''}`} onClick={() => { setLead('en'); setK(k + 1) }}>英文主</button>
      </div>
      <RevealHeadline reveal="halftone" lead={lead} size={112} replay={k} />
    </div>
  )
}

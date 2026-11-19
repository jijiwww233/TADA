import { useState } from 'react'
import { Chars, Fog, HalftoneText } from '../reveal'

/** Layout from the user's two title PNGs. Entrance components are imported unchanged from ui-intro. */
export function HomeHeadline({ english, reduced, onDone }: { english: boolean; reduced: boolean; onDone: () => void }) {
  const run = english ? 'en' : 'zh'
  const [done, setDone] = useState('')
  const fog = done === run && !reduced
  return (
    <HalftoneText key={run} off={reduced} ms={1700} className="home-reveal" onDone={() => { setDone(run); onDone() }}>
      {/* 大标题「TADA! 图然想起」：保留原来的阶梯排版——小字 / 大字 / 大字 / 小字，一处加粗 */}
      <h2 className={`home-stair ${english ? 'home-stair-en' : 'home-stair-zh'}`} aria-label="TADA! 图然想起">
        {english ? <>
          <span className="home-stair-note home-stair-first-note"><Chars text="图" /><strong><Chars text="然" /></strong></span>
          <span className="home-stair-main home-stair-first"><Fog on={fog}><Chars text="TADA!" /></Fog></span>
          <span className="home-stair-main home-stair-second"><Fog on={fog}><Chars text="it's back" /></Fog></span>
          <span className="home-stair-note home-stair-last-note"><Chars text="想起" /></span>
        </> : <>
          <span className="home-stair-note home-stair-first-note"><Chars text="TADA!" /></span>
          <span className="home-stair-main home-stair-first"><Fog on={fog}><Chars text="图" /><strong><Chars text="然" /></strong></Fog></span>
          <span className="home-stair-main home-stair-second"><Fog on={fog}><Chars text="想起" /></Fog></span>
          <span className="home-stair-note home-stair-last-note"><Chars text="it's back" /></span>
        </>}
      </h2>
    </HalftoneText>
  )
}

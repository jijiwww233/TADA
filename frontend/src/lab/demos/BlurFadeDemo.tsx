import { useState } from 'react'
import { BlurFade } from '../vendor/magicui/blur-fade'
import { Replay } from '../Demo'
import { take, useImages } from '../data'

// Round 3: 1.3x slower than round 2 (duration 0.5 → 0.65s, stagger 70 → 91ms, panel at 0.65 → 0.85s).
const SLOW = 1.3

/** Blur Fade for things that appear: result cards one after another, then a floating paper panel. */
export default function BlurFadeDemo() {
  const images = take(useImages().cards, 6)
  const [k, setK] = useState(0)
  return (
    <div key={k} className="relative flex h-[380px] items-center justify-center gap-10 overflow-hidden bg-[var(--page)] px-10">
      <Replay onClick={() => setK(k + 1)} />
      <div className="flex gap-6">
        {images.map((img, i) => (
          <BlurFade key={i} delay={(0.1 + i * 0.07) * SLOW} duration={0.5 * SLOW} blur="8px" offset={10} inView>
            <img src={img.src} alt="" className="block w-[112px] rounded-[10px] shadow-[0_2px_2px_oklch(0.3_0.03_60/.05),0_10px_24px_-8px_oklch(0.3_0.04_60/.16)]" style={{ aspectRatio: '3 / 4' }} />
          </BlurFade>
        ))}
      </div>
      <BlurFade delay={0.65 * SLOW} duration={0.5 * SLOW} blur="8px" offset={10} inView>
        <div className="capsule-panel w-[220px] p-4 text-[13px]">
          <p className="font-medium">参考它哪里</p>
          <p className="mt-1.5 text-[var(--ink-2)]">排版：大字压角，小字成列。配色：纸白 + 铁锈红。</p>
        </div>
      </BlurFade>
    </div>
  )
}

import { useState, type CSSProperties } from 'react'
import { Fog, HalftoneText, NearBlurText } from './reveal'

/**
 * Round 4 headline rules (learned from the Anthropic film titles, without their brand pieces):
 * the serif title is the hero — very large, Light, lines packed so tight they nearly touch; 明明 alone in Bold.
 * Chinese 思源宋体, English Newsreader (Light at display size, optical size follows). The other language sits
 * one clear step smaller under it. `lead` swaps which language is the headline.
 */
export type Lead = 'zh' | 'en'
type Seg = { t: string; b?: boolean }
const ZH: Seg[][] = [[{ t: '我' }, { t: '明明', b: true }], [{ t: '收藏过。' }]]
const EN: Seg[][] = [[{ t: 'I swear' }], [{ t: 'I saved it.' }]]
export const ZH_TEXT = '我明明收藏过。'
export const EN_TEXT = 'I swear I saved it.'

/** Each character in its own inline-block (data-ch) so the near-blur reveal can reach single letters. */
function Line({ segs, split }: { segs: Seg[]; split: boolean }) {
  return (
    <span className="block whitespace-nowrap">
      {segs.map((s, i) => (
        <span key={i} className={s.b ? 'hl-b' : undefined}>
          {split ? Array.from(s.t).map((ch, j) => <span key={j} data-ch className="inline-block whitespace-pre">{ch}</span>) : s.t}
        </span>
      ))}
    </span>
  )
}

/**
 * The headline block: big title (two lines) + the small title. `size` = big title font size (px, or any CSS length).
 * `fog`: the near-focus patch under the pointer, on the big title only.
 */
export function Headline({ lead = 'zh', size = 120, split = false, fog = false, className = '', style }:
  { lead?: Lead; size?: number | string; split?: boolean; fog?: boolean; className?: string; style?: CSSProperties }) {
  const big = lead === 'zh' ? ZH : EN
  const small = lead === 'zh' ? EN : ZH
  return (
    <div className={`hl hl--${lead} ${className}`} style={{ ...style, '--hl': typeof size === 'number' ? `${size}px` : size } as CSSProperties}>
      <h2 className="hl-big" aria-label={lead === 'zh' ? ZH_TEXT : EN_TEXT}>
        <Fog on={fog}>{big.map((l, i) => <Line key={i} segs={l} split={split} />)}</Fog>
      </h2>
      <p className="hl-small" aria-label={lead === 'zh' ? EN_TEXT : ZH_TEXT}>
        <Line segs={small.flatMap((l, i) => (i ? [{ t: lead === 'zh' ? ' ' : '' }, ...l] : l))} split={split} />
      </p>
    </div>
  )
}

export type RevealKind = 'halftone' | 'near'

/**
 * The headline with one of the two round-4 entrances (the big title first, the small one follows in the same pass).
 * 半调: once this entrance (this language, this replay) is done, the near-focus patch follows the pointer.
 */
export function RevealHeadline({ reveal, lead = 'zh', size, replay = 0, delay = 0, className = '' }:
  { reveal: RevealKind; lead?: Lead; size?: number | string; replay?: number; delay?: number; className?: string }) {
  const sz = { '--hl': typeof size === 'number' ? `${size}px` : size } as CSSProperties
  const run = `${lead}:${replay}`
  const [done, setDone] = useState('')
  return reveal === 'halftone'
    ? <HalftoneText key={lead} replay={replay} delay={delay} className={className} style={{ ...sz, fontSize: 'var(--hl)' }} onDone={() => setDone(run)}><Headline lead={lead} size={size} fog={done === run} /></HalftoneText>
    : <NearBlurText key={lead} replay={replay} delay={delay} className={className}><Headline lead={lead} size={size} split /></NearBlurText>
}

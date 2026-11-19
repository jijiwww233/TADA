import { useState } from 'react'
import { Capsule } from '../Capsule'
import { take, useImages } from '../data'
import { SUBTITLE } from '../Stage'
import { Headline, type Lead } from '../title'

// Illustrative values, not real data (see the report).
const META = [['小红书', '2024.03 收藏'], ['Pinterest', '2023.11 收藏'], ['站酷', '2025.06 收藏'], ['花瓣', '2022.08 收藏']]
type MetaStyle = 'off' | 'caps' | 'mono'

/**
 * Font set 1 with the round-4 headline rules: 思源宋体 + Newsreader, very large and Light, lines packed tight,
 * 明明 Bold; the other language one step smaller. Warm black ink. Buttons, input and body stay sans (思源黑体 + Inter).
 */
export default function FontsDemo() {
  const images = take(useImages().cards.slice(3), 4)
  const [lead, setLead] = useState<Lead>('zh')
  const [meta, setMeta] = useState<MetaStyle>('caps')
  return (
    <div className="relative bg-[var(--page)] px-12 pb-10 pt-14">
      <div className="absolute right-5 top-4 flex flex-wrap items-center justify-end gap-1.5 text-[12px] text-[var(--ink-2)]">
        <span className="mr-1">主标题</span>
        <button type="button" className={`lab-btn ${lead === 'zh' ? 'is-on' : ''}`} onClick={() => setLead('zh')}>中文</button>
        <button type="button" className={`lab-btn ${lead === 'en' ? 'is-on' : ''}`} onClick={() => setLead('en')}>英文</button>
        <span className="mx-2 h-4 w-px bg-[var(--line)]" />
        <span className="mr-1">卡片小字</span>
        {(['off', 'caps', 'mono'] as const).map(m => (
          <button key={m} type="button" className={`lab-btn ${meta === m ? 'is-on' : ''}`} onClick={() => setMeta(m)}>{{ off: '关', caps: '无衬线大写', mono: '等宽' }[m]}</button>
        ))}
      </div>

      <div className="[display:grid] grid-cols-[minmax(0,1fr)_auto] items-end gap-10">
        <div>
          <Headline lead={lead} size={96} />
          <p className="mt-6 max-w-[420px] text-[15px] text-[var(--ink-2)]">{SUBTITLE}</p>
          <div className="mt-8 max-w-[600px]"><Capsule /></div>
        </div>
        <div className="flex gap-[18px] pb-2">
          {images.map((img, i) => (
            <figure key={i} className="m-0 w-[92px]" style={{ marginTop: i % 2 ? 28 : 0 }}>
              <img src={img.src} alt="" className="block w-full rounded-[8px] shadow-[0_2px_2px_oklch(0.3_0.03_60/.05),0_10px_24px_-8px_oklch(0.3_0.04_60/.16)]" style={{ aspectRatio: '3 / 4' }} />
              {meta !== 'off' && (
                <figcaption className={`mt-2 flex flex-col gap-[3px] whitespace-nowrap text-[var(--ink-2)] ${meta === 'caps' ? 'meta-caps' : 'meta-mono'}`}>
                  <span>{META[i][0]}</span><span className="text-[var(--ink-3)]">{META[i][1]}</span>
                </figcaption>
              )}
            </figure>
          ))}
        </div>
      </div>
    </div>
  )
}

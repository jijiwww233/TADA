import { Fragment } from 'react'
import { ENTER } from './motion'

const segmenter = typeof Intl !== 'undefined' && 'Segmenter' in Intl
  ? new Intl.Segmenter('zh', { granularity: 'word' })
  : null

/**
 * 原有那套“文字出现”：主页大标题的逐词进场（.m-word + m-word-in：blur 10px；第五轮统一到出场那档：上移 16px、0.6s、每词错开 40ms，最多错开 8 个）。
 * 这里只是把它包成能复用的组件，动效本身沿用 main.css 里那一条，不另写。
 * 换 key 重新挂载就会再播一次（详情页滚到下一张时用）。
 */
export function RevealWords({ text, className }: { text: string; className?: string }) {
  const parts = segmenter ? [...segmenter.segment(text)].map(s => s.segment) : text.split(/(\s+)/)
  let wi = 0
  return (
    <span className={`m-rt${className ? ` ${className}` : ''}`}>
      {parts.map((part, i) => /^\s+$/.test(part)
        ? <Fragment key={i}>{part}</Fragment>
        : <span key={i} className="m-word" style={{ '--wi': Math.min(wi++, ENTER.maxSteps - 1) } as React.CSSProperties}>{part}</span>)}
    </span>
  )
}

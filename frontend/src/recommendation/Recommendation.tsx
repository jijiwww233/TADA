import { useState, type ReactNode } from 'react'
import type { ImageEntity } from '../search/contract'
import { ImagePreview } from '../search/Search'
import type { RecommendationColumn } from './contract'
import type { RecommendationState } from './store'

export function CreativeBrief({ loading, submit }: { loading: boolean; submit: (brief: string, previous?: string) => void }) {
  const [brief, setBrief] = useState(''); const [previous, setPrevious] = useState('')
  return <form onSubmit={e => { e.preventDefault(); if (!loading && brief.trim()) submit(brief, previous.trim() ? previous : undefined) }}>
    <label>当前创作要求<textarea value={brief} onChange={e => setBrief(e.target.value)} /></label>
    <label>上一版 brief（可选）<textarea value={previous} onChange={e => setPrevious(e.target.value)} /></label>
    <small>追加条件或否定条件原样传给后端，前端不自行拼接查询。</small>
    <button type="submit" disabled={loading || !brief.trim()}>获取推荐</button>
  </form>
}

export function RecommendationFeedback({ state, retry }: { state: RecommendationState; retry: () => void }) {
  if (state.status === 'loading') return <p role="status">推荐中…</p>
  if (state.status === 'error') return <div role="alert">{state.error} <button type="button" onClick={retry}>重试</button></div>
  if (state.status === 'index_empty') return <p role="status">还没有可推荐的分析结果，分析完成后会自动建立索引。</p>
  if (state.status !== 'ok') return null
  return <div>
    <p className="recommendation-query">实际检索：{state.effectiveQuery}</p>
    {state.columns.every(c => c.items.length === 0) && <p>三栏都没有经视觉复看确认有用的参考。</p>}
    {state.notices.length > 0 && <ul aria-label="推荐提示">{state.notices.map((n, i) => <li key={i}>{n}</li>)}</ul>}
  </div>
}

const usageText = (c: RecommendationColumn) => `复看 ${c.reviewed_ids.length} 张 · ${c.usage.requests} 次请求` +
  (c.usage.prompt_tokens != null ? ` · ${c.usage.prompt_tokens}+${c.usage.completion_tokens ?? 0} tokens` : '') +
  ` · ${(c.usage.latency_ms / 1000).toFixed(1)}s`

/** Three read-only columns. Each card has rank, reference point and an optional Day7 pin action. */
export function RecommendationColumns({ columns, imagesById, onOpen, renderPinAction }: {
  columns: RecommendationColumn[]; imagesById: Record<number, ImageEntity>; onOpen: (id: number) => void
  renderPinAction?: (item: RecommendationColumn['items'][number]) => ReactNode
}) {
  if (!columns.length) return null
  return <div className="recommendation-columns">{columns.map(column => <section key={column.purpose} aria-label={`${column.label}参考`}>
    <h3>{column.label}参考</h3>
    <p className="recommendation-meta">召回：{column.recall_source} · {usageText(column)}</p>
    {column.status === 'review_failed' && <p role="alert">这一栏视觉复看失败{column.error ? '：' + column.error : ''}；以下为召回前 5 张，均未复看。</p>}
    {column.status === 'no_candidates' && <p>这一栏没有候选。</p>}
    {column.status === 'ok' && column.items.length === 0 && <p>复看后没有真正有用的参考，不凑数。</p>}
    {column.items.length > 0 && <ol className="workspace-cards recommendation-cards" aria-label={`${column.label}推荐`}>
      {column.items.map(item => <li key={item.image_id} data-image-id={item.image_id}>
        <ImagePreview image={imagesById[item.image_id]} id={item.image_id} onOpen={onOpen} />
        {item.media_scope === 'cover_and_description' && <p>仅分析封面与简介</p>}
        <p><strong>参考它的哪里：</strong>{item.reference_point}</p>
        {item.unreviewed && <p role="status">未复看（该栏视觉复看失败，退回召回结果）</p>}
        <small>召回第 {item.recall_rank} 名 · 复看第 {item.rank} 名</small>
        {renderPinAction?.(item)}
      </li>)}
    </ol>}
  </section>)}</div>
}

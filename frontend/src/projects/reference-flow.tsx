import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { ImageEntity } from '../search/contract'
import { mergeHits } from '../search/store'
import type { RecommendationApi } from '../recommendation/contract'
import { RecommendationColumns, RecommendationFeedback } from '../recommendation/Recommendation'
import { RecommendationStore } from '../recommendation/store'
import type { ProjectStore } from './store'

const sectionStyle = { border: '1px solid #ddd', borderRadius: 8, padding: '12px 16px', margin: '12px 0', background: '#fafafa' }
const usageStyle = { display: 'flex', flexWrap: 'wrap' as const, gap: 10, alignItems: 'baseline', marginTop: 8, padding: '8px 12px',
  background: '#fff', border: '1px solid #eee', borderRadius: 6, fontSize: 13 }

/**
 * After a project is created with a brief, reuse the existing three-column recommendation
 * (RecommendationStore + POST /api/recommendations/columns) and let the user pin one card
 * with one click into the current project. Never recommends without a brief and never pins
 * automatically.
 */
export function ProjectReferenceFlow({ store, recommendationApi, onOpen, onPip }: {
  store: ProjectStore; recommendationApi: RecommendationApi; onOpen: (id: number) => void; onPip: () => void
}) {
  const [imagesById, setImagesById] = useState<Record<number, ImageEntity>>({})
  const [recommender] = useState(() => new RecommendationStore(recommendationApi,
    images => setImagesById(previous => mergeHits(previous, images))))
  const state = useSyncExternalStore(store.subscribe, store.snapshot)
  const recommendationState = useSyncExternalStore(recommender.subscribe, recommender.snapshot)
  const created = state.lastCreated
  const lastTriggered = useRef<number | null>(null)

  // Fire exactly once per creation (seq increments even for a repeated brief). The user click
  // on "重试" is the only path that sends the request again.
  useEffect(() => {
    if (!created || !created.brief.trim() || lastTriggered.current === created.seq) return
    lastTriggered.current = created.seq
    recommender.configure({ project_id: created.id })
    void recommender.recommend(created.brief)
  }, [created, recommender])

  if (!created || !created.brief.trim()) return null

  // The flow belongs to the project it created: pins and the PiP source must stay that
  // project even if the user later selects another one.
  const activeId = state.activeProjectId
  const createdId = created.id
  const pins = state.pinsByProject[createdId] ?? []
  const pinnedIds = new Set(pins.map(item => item.image_id))
  const canPin = activeId === createdId && state.detailState.status === 'ok'
  const usage = recommendationState.usage
  return <section aria-label="项目参考推荐" style={sectionStyle}>
    <h2 style={{ marginTop: 0 }}>要参考（{created.name}）</h2>
    <p>项目 brief：{created.brief}</p>
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
      <button type="button" onClick={onPip} disabled={pins.length === 0}>打开参考小窗</button>
      <small>{activeId !== createdId ? '已切换到其他项目；切回刚创建的项目才能钉选并打开小窗。'
        : pins.length === 0 ? '先钉一张图，才能打开参考小窗。' : '小窗使用当前项目已钉选的图片。'}</small>
    </div>
    <RecommendationFeedback state={recommendationState} retry={() => void recommender.recommend(created.brief)} />
    {usage && <div aria-label="本次推荐用量" style={usageStyle}>
      <strong>本次推荐用量</strong>
      <span>{usage.requests === null ? '请求计量不完整' : `${usage.requests} 次请求`}</span>
      <span>输入 {usage.prompt_tokens ?? '未知'} tokens</span>
      <span>输出 {usage.completion_tokens ?? '未知'} tokens</span>
      <span>耗时 {(usage.latency_ms / 1000).toFixed(2)} 秒</span>
      <p style={{ margin: '6px 0 0', width: '100%' }}>本次推荐调用了视觉复看，产生费用。</p>
    </div>}
    {recommendationState.status === 'ok' && <RecommendationColumns columns={recommendationState.columns}
      imagesById={imagesById} onOpen={onOpen}
      renderPinAction={item => {
        const pinned = pinnedIds.has(item.image_id)
        const busy = state.busyPins.includes(`${createdId}:${item.image_id}`)
        return <button type="button" disabled={pinned || busy || !canPin}
          onClick={() => void store.pinImage(item.image_id, [item.purpose])}>
          {pinned ? '已钉进项目小窗' : busy ? '钉选中…' : '钉进项目小窗'}
        </button>
      }} />}
    {state.pinError && <p role="alert" style={{ color: '#b00' }}>{state.pinError}</p>}
  </section>
}

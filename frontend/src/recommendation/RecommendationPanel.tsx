import ColorTargets from './ColorTargets'
import LegacyRecommendation from './LegacyRecommendation'
import { useRef, useState, useEffect, useSyncExternalStore, type ReactNode } from 'react'
import type { ImageEntity } from '../search/contract'
import { CreativeBrief, RecommendationColumns, RecommendationFeedback } from './Recommendation'
import type { RecommendationStore } from './store'
import './recommendation.css'

export default function RecommendationPanel({ store, imagesById, onOpen, renderPinAction, projectId }: {
  projectId?:number|null; store: RecommendationStore; imagesById: Record<number, ImageEntity>; onOpen: (id: number) => void
  renderPinAction?: (item: import('./contract').RecommendationItem) => ReactNode
}) {
  useEffect(()=>store.configure({project_id:projectId??undefined}),[store,projectId])
  const [includeNonvisual,setIncludeNonvisual] = useState(false)
  const state = useSyncExternalStore(store.subscribe, store.snapshot)
  const last = useRef<{ brief: string; previous?: string }>({ brief: '' })
  return <section aria-label="创作建议" className="recommendation-panel"><h2>创作推荐</h2>
    <label><input type="checkbox" checked={includeNonvisual} onChange={e=>setIncludeNonvisual(e.target.checked)} />包含文字卡片 / 生活类</label>
    <ColorTargets onChange={values=>store.configure({color_need:values.length?values:undefined})}/>
    <CreativeBrief loading={state.status === 'loading'}
      submit={(brief, previous) => { last.current = { brief, previous }; void store.recommend(brief, previous, includeNonvisual) }} />
    <RecommendationFeedback state={state} retry={() => void store.recommend(last.current.brief, last.current.previous, includeNonvisual)} />
    {state.usage && <details><summary>本次推荐用量</summary><p>{state.usage.requests===null?'请求计量不完整':`${state.usage.requests} 次请求`} · {(state.usage.latency_ms/1000).toFixed(2)} 秒{state.usage.prompt_tokens!==null && ` · 输入 ${state.usage.prompt_tokens} / 输出 ${state.usage.completion_tokens ?? '未知'} tokens`}</p></details>}
    <details><summary>旧规则版对照开关</summary><LegacyRecommendation onOpen={onOpen} /></details>
    <RecommendationColumns columns={state.columns} imagesById={imagesById} onOpen={onOpen}
      renderPinAction={item=><>{renderPinAction?.(item)}<div><button type="button" disabled={state.status==='loading'||Boolean(state.busyColumn)} onClick={()=>void store.moreLike(item.image_id,item.purpose)}>更多类似</button><button type="button" disabled={state.status==='loading'} onClick={()=>store.exclude(item.image_id)}>不要这种</button><button type="button" disabled={!projectId} onClick={()=>void store.rate(item.image_id,item.purpose,true)}>能用</button><button type="button" disabled={!projectId} onClick={()=>void store.rate(item.image_id,item.purpose,false)}>不能用</button></div></>} />
    {state.busyColumn&&<p role="status">正在复看这栏的相似参考…</p>}
    {state.feedbackMessage&&<p role="status">{state.feedbackMessage}</p>}
    {state.actionError&&<p role="alert">{state.actionError}</p>}
  </section>
}

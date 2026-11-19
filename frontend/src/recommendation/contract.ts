import type { SearchHit } from '../search/contract'

export type Purpose = 'layout' | 'palette' | 'style'
/** One card: picked by the vision review of the real image, with "参考它的哪里". */
export interface RecommendationItem {
  image_id: number; rank: number; recall_rank: number
  media_scope: 'image' | 'cover_and_description'; post_ids: number[]
  reference_point: string; purpose: Purpose; unreviewed?: boolean; semantic_score?:number
}
export interface ColumnUsage { requests: number; prompt_tokens: number | null; completion_tokens: number | null; latency_ms: number }
export interface RecommendationColumn {
  purpose: Purpose; label: string; status: 'ok' | 'no_candidates' | 'review_failed'; error?: string | null
  recall_source: string; reviewed_ids: number[]; items: RecommendationItem[]; usage: ColumnUsage
}
export interface RecommendationUsage { requests:number|null; reported_requests?:number; prompt_tokens:number|null;completion_tokens:number|null;latency_ms:number;complete?:boolean }
export type RecommendationResponse = {
  usage?: RecommendationUsage;
  status: 'ok' | 'index_empty'; effective_query: string; columns: RecommendationColumn[]
  images: SearchHit[] // presentation fields for picked images only
  notices: string[]
}
export type RecommendationApi = (brief: string, previousBrief?: string, includeNonvisual?: boolean) => Promise<RecommendationResponse>

export const PURPOSE_LABELS: Record<Purpose, string> = { layout: '排版', palette: '配色', style: '风格' }

export class RecommendationError extends Error {
  constructor(message = '推荐服务暂不可用，请重试') { super(message) }
}

/** Real adapter for the original-plan three-column backend; no retries. */
export const getRecommendations: RecommendationApi = async (brief, previousBrief, includeNonvisual = false) => {
  const response = await fetch('/api/recommendations/columns', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({brief, ...(previousBrief ? {previous_brief:previousBrief}:{}), ...(includeNonvisual ? {include_nonvisual:true}:{})}),
  })
  if (response.status === 422) {
    // Only our own fixed message for exclusions-only briefs; never raw bodies.
    const body = await response.json().catch(() => null)
    if (body?.detail?.code === 'only_exclusions') throw new RecommendationError('请描述想要的参考，而不只是排除条件')
  }
  if (!response.ok) throw new RecommendationError()
  return response.json()
}

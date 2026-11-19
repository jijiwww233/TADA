import type { ImageItem } from '../api'

export interface Source {
  post_id: number; platform: string; source_id: string | null; canonical_url: string | null
  title: string | null; position: number; is_video: boolean; image_count?: number
}
/** Backend relevance line for a hit: strong is a real match, weak is an approximate one. */
export type Relevance = 'strong' | 'weak'
export interface SearchHit {
  image_id: number; score: number; url: string; width: number | null; height: number | null
  original_name: string | null; created_at: string; sources: Source[]; is_video: boolean
  /** Optional: older backends omit it and the UI treats the hit as strong. */
  relevance?: Relevance
}
export interface ImageEntity extends ImageItem { sources?: Source[]; is_video?: boolean }
// degraded means the paid text route was unreachable and only local channels answered.
export type SearchResponse =
  | { status: 'ok'; items: SearchHit[]; degraded?: boolean }
  | { status: 'index_empty'; items: [] }
export type SearchApi = (query: string) => Promise<SearchResponse>

export class SearchError extends Error {
  constructor() { super('网络或服务出错，请稍后重试') }
}

/** Real Day5 adapter. Raw query is unchanged; no retries or second search. */
export const searchImages: SearchApi = async query => {
  const response = await fetch('/api/search', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query }),
  })
  if (!response.ok) throw new SearchError() // never expose raw provider/HTTP bodies
  return response.json()
}

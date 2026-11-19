import type { SearchResponse } from './contract'

export type SimilarQuery = number | File
export type SimilarDimension = 'combined' | 'palette' | 'layout' | 'subject' | 'style'
export type SimilarResponse = SearchResponse & { available_dimensions?: SimilarDimension[]; match_basis?: 'palette' }
export type SimilarApi = (query: SimilarQuery, dimension?: SimilarDimension) => Promise<SimilarResponse>
export const findSimilar: SimilarApi = async (query, dimension) => {
  const form = new FormData()
  if (query instanceof File) form.append('file', query)
  const mode = dimension && dimension !== 'combined' ? `?dimension=${dimension}` : ''
  const response = await fetch(typeof query === 'number' ? `/api/images/${query}/similar${mode}` : '/api/search/image',
    typeof query === 'number' ? undefined : { method: 'POST', body: form })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : '本地找相似暂不可用，请重试。')
  }
  return response.json()
}

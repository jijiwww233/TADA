import type { ImageItem } from '../api'
import { findSimilar, type SimilarApi, type SimilarDimension, type SimilarQuery } from './similar'
import type { ImageEntity, SearchApi, SearchHit } from './contract'

export type RequestState = { status: 'idle' | 'loading' | 'ok' | 'error' | 'index_empty'; error?: string
  /** ok only: the paid text route was down, so this result came from local matching. */
  degraded?: boolean }
export interface SearchState {
  imagesById: Record<number, ImageEntity>; libraryIds: number[]; searchIds: number[]
  searchMode?: 'text' | 'image'; searchLabel?: string;
  similarityImageId?: number; similarityDimension?: SimilarDimension; similarityDimensions?: SimilarDimension[]
  searchScores: Record<number, number>; searchState: RequestState; libraryState: RequestState
}

/** Merge hydrated hits into the single entity map (search and Day6 recommendation cards). */
export function mergeHits(imagesById: Record<number, ImageEntity>, items: Omit<SearchHit, 'score'>[]) {
  const entities = { ...imagesById }
  for (const image of items) {
    const old = entities[image.image_id]; const first = image.sources[0]
    entities[image.image_id] = { ...old, id: image.image_id, url: image.url, width: image.width, height: image.height,
      original_name: image.original_name, created_at: image.created_at, sources: image.sources, is_video: image.is_video, ...(first?.image_count!==undefined?{image_count:first.image_count}:{}),
      post_id: first?.post_id ?? old?.post_id ?? 0, position: first?.position ?? old?.position ?? 0,
      platform: first && ['local','xhs','pinterest','zcool','huaban'].includes(first.platform) ? first.platform as ImageEntity['platform'] : old?.platform ?? 'local' }
  }
  return entities
}

/** Only Day5 state. Results store IDs and scores, not another copy of image entities. */
export class SearchStore {
  private state: SearchState = { imagesById: {}, libraryIds: [], searchIds: [], searchScores: {},
    searchState: { status: 'idle' }, libraryState: { status: 'idle' } }
  private listeners = new Set<() => void>()
  private ticket = 0
  private libraryTicket = 0
  private pending: string | null = null
  private lastImage: SimilarQuery | null = null
  private lastDimension?: SimilarDimension
  private similarApi: SimilarApi
  private searchApi: SearchApi
  private listImages: () => Promise<ImageItem[]>
  constructor(searchApi: SearchApi, listImages: () => Promise<ImageItem[]>, similarApi: SimilarApi = findSimilar) { this.searchApi = searchApi; this.listImages = listImages; this.similarApi = similarApi }
  snapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private patch(update: Partial<SearchState>) { this.state = { ...this.state, ...update }; this.listeners.forEach(fn => fn()) }
  refresh = async () => {
    const ticket = ++this.libraryTicket
    this.patch({ libraryState: { status: 'loading' } })
    try {
      const images = await this.listImages()
      if (ticket !== this.libraryTicket) return
      const entities = { ...this.state.imagesById }
      for (const image of images) entities[image.id] = { ...entities[image.id], ...image }
      this.patch({ imagesById: entities, libraryIds: images.map(i => i.id), libraryState: { status: 'ok' } })
    } catch { if (ticket === this.libraryTicket) this.patch({ libraryState: { status: 'error', error: '读取图库失败' } }) }
  }
  /** Day6 cards hydrate the same entity map; no ids or scores of the search surface change. */
  mergeImages = (items: Omit<SearchHit, 'score'>[]) => {
    if (items.length) this.patch({ imagesById: mergeHits(this.state.imagesById, items) })
  }
  /** Back to the library surface. Bumping the ticket drops any in-flight response. */
  clear = () => {
    ++this.ticket; this.pending = null; this.lastImage = null
    this.patch({ searchState: { status: 'idle' }, searchIds: [], searchScores: {} })
  }
  search = async (query: string) => {
    if (!query.trim() || this.state.searchState.status === 'loading' && this.pending === query) return
    const ticket = ++this.ticket; this.pending = query
    this.patch({ searchMode: 'text', searchLabel: query, searchState: { status: 'loading' }, searchIds: [], searchScores: {} })
    try {
      const result = await this.searchApi(query)
      if (ticket !== this.ticket) return
      this.patch({ imagesById: mergeHits(this.state.imagesById, result.items), searchIds: result.items.map(i => i.image_id),
        searchScores: Object.fromEntries(result.items.map(i => [i.image_id, i.score])),
        searchState: result.status === 'ok'
          ? { status: result.status, ...(result.degraded ? { degraded: true } : {}) }
          : { status: result.status } })
    } catch { if (ticket === this.ticket) this.patch({ searchState: { status: 'error', error: '搜索暂时不可用，请重试' } }) }
  }
  similar = async (query: SimilarQuery, dimension?: SimilarDimension) => {
    const imageId = typeof query === 'number' ? query : undefined
    const sameImage = this.state.searchMode === 'image' && this.state.similarityImageId === imageId
    const ticket = ++this.ticket; this.pending = null; this.lastImage = query
    this.lastDimension = dimension
    this.patch({ searchMode: 'image', searchLabel: typeof query === 'number' ? `图片 #${query}` : query.name,
      similarityImageId: imageId, similarityDimension: dimension ?? 'combined',
      similarityDimensions: sameImage ? this.state.similarityDimensions : undefined,
      searchState: { status: 'loading' }, searchIds: [], searchScores: {} })
    try {
      const result = dimension && dimension !== 'combined' ? await this.similarApi(query, dimension) : await this.similarApi(query)
      if (ticket !== this.ticket) return
      this.patch({ similarityDimensions: result.available_dimensions,
        imagesById: mergeHits(this.state.imagesById, result.items), searchIds: result.items.map(i => i.image_id),
        searchScores: Object.fromEntries(result.items.map(i => [i.image_id, i.score])), searchState: { status: result.status } })
    } catch (error) {
      if (ticket === this.ticket) this.patch({ searchState: { status: 'error', error: error instanceof Error ? error.message : '本地找相似暂不可用' } })
    }
  }
  retrySimilar = () => { if (this.lastImage !== null) void this.similar(this.lastImage, this.lastDimension) }

}

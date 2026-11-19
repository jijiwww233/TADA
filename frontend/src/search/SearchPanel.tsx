import { useRef, type ReactNode } from 'react'
import { RequestFeedback, SearchBar, SearchResults } from './Search'
import type { SearchState, SearchStore } from './store'
import './search.css'

export default function SearchPanel({ store, state, onOpen, visibleIds, similarityControls }: { visibleIds?: Set<number>; store: SearchStore; state: SearchState; onOpen: (id: number) => void; similarityControls?: ReactNode }) {
  const lastQuery = useRef('')
  return <section aria-label="语义搜索" className="search-panel"><h2>找回收藏</h2>
    <SearchBar loading={state.searchState.status === 'loading'} active={state.searchState.status !== 'idle'}
      submit={query => { lastQuery.current = query; void store.search(query) }} clear={store.clear} image={file => void store.similar(file)} />
    {state.searchMode === 'image' && state.searchState.status !== 'idle' && <p role="status">找相似：{state.searchLabel}（本地计算）</p>}
    {state.searchMode === 'image' && state.searchState.status !== 'idle' && similarityControls}
    {state.searchMode === 'image' && state.searchState.status === 'index_empty' ?
      <p role="status">暂无可比较的图片向量，请等待本地索引完成；模型未安装时请按说明配置。</p> : <RequestFeedback state={state.searchState} retry={() => state.searchMode === 'image' ? store.retrySimilar() : void store.search(lastQuery.current)} />}
    <SearchResults ids={visibleIds ? state.searchIds.filter(id=>visibleIds.has(id)) : state.searchIds} imagesById={state.imagesById} state={state.searchState} onOpen={onOpen} />
  </section>
}

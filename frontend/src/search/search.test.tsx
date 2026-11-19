import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../App'
import { fakeApi } from '../projects/__tests__/fixtures'
import { searchImages, type SearchHit, type SearchResponse } from './contract'
import { SearchStore } from './store'

// Mature library controls retain their implementation; no polling/real services in tests.
vi.mock('../SyncBar', () => ({ default: () => null }))
vi.mock('../AutoSyncControl', () => ({ default: () => null }))
vi.mock('../AnalysisBar', () => ({ default: () => null }))
vi.mock('../DropZone', () => ({ default: () => null }))
vi.mock('../PipButton', () => ({ default: () => null }))
afterEach(() => vi.unstubAllGlobals())

function hit(id: number, score = .61): SearchHit {
  return { image_id: id, score, url: `/images/${id}.png`, width: 1080, height: 1440, original_name: `参考图 ${id}`,
    created_at: '', is_video: false, sources: [{ post_id: 5, position: 129, platform: 'xhs', source_id: 'note',
      canonical_url: 'https://example.org/note', title: '展板', is_video: false }] }
}
const result = (...ids: number[]): SearchResponse => ({ status: 'ok', items: ids.map(id => hit(id)) })
function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
async function renderSearch(api: (query: string) => Promise<SearchResponse>, detailLoader?: Parameters<typeof App>[0]['detailLoader']) {
  vi.stubGlobal('fetch', vi.fn(() => { throw new Error('unexpected real HTTP') }))
  const user = userEvent.setup()
  render(<App projectApi={fakeApi()} searchApi={api} libraryApi={async () => []} detailLoader={detailLoader} />)
  return user
}

it('real adapter posts unchanged query once and returns contract without rescaling score', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => result(42) })
  vi.stubGlobal('fetch', fetch)
  expect(await searchImages('  米白展板\n')).toEqual(result(42))
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/search', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: '  米白展板\n' }),
  })
})

it('real adapter hides upstream error body and does not retry', async () => {
  const text = vi.fn(async () => 'secret raw provider body')
  const fetch = vi.fn().mockResolvedValue({ ok: false, status: 503, text })
  vi.stubGlobal('fetch', fetch)
  await expect(searchImages('a')).rejects.toThrow('搜索暂时不可用')
  expect(text).not.toHaveBeenCalled(); expect(fetch).toHaveBeenCalledTimes(1)
})

it('Enter submits once, whitespace-only is disabled, raw nonblank query is preserved', async () => {
  const api = vi.fn(async () => result(42)); const user = await renderSearch(api)
  const input = screen.getByRole('textbox', { name: '搜索收藏' })
  await user.type(input, '   ')
  expect(screen.getByRole('button', { name: '搜索' })).toBeDisabled()
  await user.clear(input); await user.type(input, '  米白展板  {Enter}')
  expect(api).toHaveBeenCalledExactlyOnceWith('  米白展板  ')
})

it('renders Top20 in API order, never as percentage, no recommendation cards before a brief', async () => {
  const items = Array.from({ length: 20 }, (_, i) => hit(80 - i, i % 2 ? .9 : .2))
  const user = await renderSearch(async () => ({ status: 'ok', items }))
  await user.type(screen.getByRole('textbox', { name: '搜索收藏' }), '展板{Enter}')
  const list = screen.getByRole('list', { name: '搜索结果' })
  expect(within(list).getAllByRole('listitem').map(li => li.getAttribute('data-image-id'))).toEqual(items.map(i => String(i.image_id)))
  expect(list.textContent).not.toContain('%')
  expect(screen.queryByRole('list', { name: '创作推荐' })).not.toBeInTheDocument() // Day6 panel exists; search never fills it
  expect(screen.getByRole('combobox', { name: '当前项目' })).toBeInTheDocument()
})

it('search result opens the existing ImageDetail with the specific image ID', async () => {
  const loader = vi.fn(async id => ({ id, url: '/images/42.png', width: 1080, height: 1440,
    original_name: '多栏参考图', created_at: '',
    posts: [{ id: 5, platform: 'xhs', url: 'https://example.org/note', title: '展板', position: 129 }], colors: [], analysis: null }))
  const user = await renderSearch(async () => result(42), loader)
  await user.type(screen.getByRole('textbox', { name: '搜索收藏' }), '展板{Enter}')
  await user.click(screen.getByRole('button', { name: '查看图片 42' }))
  expect(await screen.findByRole('heading', { name: '多栏参考图' })).toBeInTheDocument()
  expect(loader).toHaveBeenCalledWith(42)
  expect(screen.getByText(/第 130 \/ 130 张/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '关闭' }))
  expect(screen.queryByRole('heading', { name: '多栏参考图' })).not.toBeInTheDocument()
})

it('index_empty and valid zero results have different messages', async () => {
  const api = vi.fn().mockResolvedValueOnce({ status: 'index_empty', items: [] }).mockResolvedValueOnce(result())
  const user = await renderSearch(api)
  const input = screen.getByRole('textbox', { name: '搜索收藏' })
  await user.type(input, 'a{Enter}')
  expect(screen.getByRole('status')).toHaveTextContent('还没有可搜索的分析结果')
  await user.clear(input); await user.type(input, 'b{Enter}')
  expect(screen.getByText('没有搜索结果，试试其他描述。')).toBeInTheDocument()
})

it('loading prevents duplicate Enter and error offers retry with last submitted query', async () => {
  const gate = deferred<SearchResponse>()
  const api = vi.fn().mockReturnValueOnce(gate.promise).mockResolvedValueOnce(result(42))
  const user = await renderSearch(api)
  const input = screen.getByRole('textbox', { name: '搜索收藏' })
  await user.type(input, 'old{Enter}{Enter}')
  expect(api).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('button', { name: '搜索' })).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent('读取中')
  await user.type(input, ' not submitted') // edit (not clear): clearing would cancel the search
  await act(async () => gate.reject(new Error('raw secret')))
  expect(screen.getByRole('alert')).not.toHaveTextContent('secret')
  await user.click(screen.getByRole('button', { name: '重试' }))
  expect(api).toHaveBeenLastCalledWith('old')
  expect(screen.getByRole('button', { name: '查看图片 42' })).toBeInTheDocument()
})

it('older success cannot replace a newer search or merge old entities', async () => {
  const gate = deferred<SearchResponse>()
  const api = vi.fn().mockReturnValueOnce(gate.promise).mockResolvedValueOnce(result(2))
  const store = new SearchStore(api, async () => [])
  const first = store.search('old'); await store.search('new'); gate.resolve(result(1)); await first
  expect(store.snapshot().searchIds).toEqual([2]); expect(store.snapshot().imagesById[1]).toBeUndefined()
})

it('older failure cannot overwrite newer success', async () => {
  const gate = deferred<SearchResponse>()
  const api = vi.fn().mockReturnValueOnce(gate.promise).mockResolvedValueOnce(result(2))
  const store = new SearchStore(api, async () => [])
  const first = store.search('old'); await store.search('new'); gate.reject(new Error('old failure')); await first
  expect(store.snapshot().searchState.status).toBe('ok'); expect(store.snapshot().searchIds).toEqual([2])
})

it('same request in flight is suppressed without preventing a newer query', async () => {
  const gate = deferred<SearchResponse>(); const api = vi.fn().mockReturnValueOnce(gate.promise).mockResolvedValueOnce(result(2))
  const store = new SearchStore(api, async () => []); const first = store.search('old')
  await store.search('old'); await store.search('new'); gate.resolve(result(1)); await first
  expect(api).toHaveBeenCalledTimes(2)
})

it('one normalized map keeps library membership and raw sources/position', async () => {
  const library = { id: 42, url: '/images/old.png', width: 1, height: 1, original_name: 'old', created_at: '',
    post_id: 1, position: 0, platform: 'local' as const }
  const store = new SearchStore(async () => result(42, 43), async () => [library]); await store.refresh(); await store.search('a')
  const state = store.snapshot()
  expect(state.libraryIds).toEqual([42]); expect(state.searchIds).toEqual([42, 43])
  expect(Object.keys(state.imagesById)).toHaveLength(2)
  expect(state.imagesById[42].sources![0].position).toBe(129)
  expect(state.imagesById[42].sources![0].canonical_url).toBe('https://example.org/note')
  expect(state.imagesById[42].url).toBe('/images/42.png')
})

// Surface selection: library grid only when not searching; a search never appends the library.
const lib = (id: number) => ({ id, url: `/images/lib-${id}.png`, width: 1, height: 1, original_name: `图库 ${id}`,
  created_at: '', post_id: 1, position: 0, platform: 'local' as const })
async function renderWithLibrary(api: (query: string) => Promise<SearchResponse>, ids = [1, 2, 3, 4]) {
  vi.stubGlobal('fetch', vi.fn(() => { throw new Error('unexpected real HTTP') }))
  const user = userEvent.setup()
  const view = render(<App projectApi={fakeApi()} searchApi={api} libraryApi={async () => ids.map(lib)} />)
  await screen.findByText(`已收录 ${ids.length} 张`)
  const grid = () => view.container.querySelector('.grid')
  const imgs = () => [...view.container.querySelectorAll('img')].map(img => img.getAttribute('src'))
  const resultIds = () => within(screen.getByRole('list', { name: '搜索结果' })).queryAllByRole('listitem')
    .map(li => Number(li.getAttribute('data-image-id')))
  return { user, grid, imgs, resultIds, input: screen.getByRole('textbox', { name: '搜索收藏' }) }
}

it('no search shows the library grid in libraryIds order', async () => {
  const { grid, imgs } = await renderWithLibrary(async () => result())
  expect(grid()).not.toBeNull()
  expect(imgs()).toEqual(['/images/lib-1.png', '/images/lib-2.png', '/images/lib-3.png', '/images/lib-4.png'])
})

it('search success renders only searchIds in API order, without the library and without duplicates', async () => {
  const { user, grid, imgs, resultIds, input } = await renderWithLibrary(async () => result(3, 9, 2))
  await user.type(input, '展板{Enter}')
  expect(resultIds()).toEqual([3, 9, 2])
  expect(grid()).toBeNull()
  expect(imgs()).toEqual(['/images/3.png', '/images/9.png', '/images/2.png'])
})

it('clear button and emptying the input both restore libraryIds', async () => {
  const { user, grid, imgs, input } = await renderWithLibrary(async () => result(3))
  await user.type(input, '展板{Enter}')
  await user.click(screen.getByRole('button', { name: '返回全部收藏' }))
  expect(input).toHaveValue('')
  expect(grid()).not.toBeNull(); expect(screen.queryByRole('list', { name: '搜索结果' })).toBeNull()
  expect(imgs()).toHaveLength(4)
  expect(screen.queryByRole('button', { name: '返回全部收藏' })).toBeNull()
  await user.type(input, '展板{Enter}'); expect(grid()).toBeNull()
  await user.clear(input)
  expect(grid()).not.toBeNull(); expect(imgs()).toHaveLength(4)
})

it('loading, error, index_empty and zero results never fall back to the library grid', async () => {
  const gate = deferred<SearchResponse>()
  const api = vi.fn().mockReturnValueOnce(gate.promise)
    .mockResolvedValueOnce({ status: 'index_empty', items: [] }).mockResolvedValueOnce(result())
  const { user, grid, imgs, input } = await renderWithLibrary(api)
  await user.type(input, 'a{Enter}')
  expect(screen.getByRole('status')).toHaveTextContent('读取中'); expect(grid()).toBeNull()
  await act(async () => gate.reject(new Error('down')))
  expect(screen.getByRole('alert')).toHaveTextContent('搜索暂时不可用')
  expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  expect(grid()).toBeNull(); expect(imgs()).toEqual([])
  await user.clear(input); await user.type(input, 'b{Enter}')
  expect(screen.getByRole('status')).toHaveTextContent('还没有可搜索的分析结果'); expect(grid()).toBeNull()
  await user.clear(input); await user.type(input, 'c{Enter}')
  expect(screen.getByText('没有搜索结果，试试其他描述。')).toBeInTheDocument()
  expect(grid()).toBeNull(); expect(imgs()).toEqual([])
})

it('a response arriving after clear is dropped and the library stays', async () => {
  const gate = deferred<SearchResponse>()
  const { user, grid, imgs, input } = await renderWithLibrary(vi.fn().mockReturnValueOnce(gate.promise))
  await user.type(input, 'a{Enter}')
  await user.click(screen.getByRole('button', { name: '返回全部收藏' }))
  await act(async () => gate.resolve(result(7)))
  expect(grid()).not.toBeNull(); expect(imgs()).toHaveLength(4)
  expect(screen.queryByRole('list', { name: '搜索结果' })).toBeNull()
})

it('store.clear drops in-flight success/failure and keeps imagesById shared', async () => {
  const ok = deferred<SearchResponse>(); const bad = deferred<SearchResponse>()
  const store = new SearchStore(vi.fn().mockReturnValueOnce(ok.promise).mockReturnValueOnce(bad.promise), async () => [lib(1)])
  await store.refresh()
  const first = store.search('a'); store.clear(); ok.resolve(result(1, 5)); await first
  expect(store.snapshot().searchState.status).toBe('idle'); expect(store.snapshot().searchIds).toEqual([])
  expect(store.snapshot().imagesById[5]).toBeUndefined()
  const second = store.search('a'); store.clear(); bad.reject(new Error('x')); await second
  expect(store.snapshot().searchState.status).toBe('idle')
  expect(store.snapshot().libraryIds).toEqual([1])
})
it('real adapter keeps the degraded flag from a local-only backend response', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok', items: [hit(7)], degraded: true }) })
  vi.stubGlobal('fetch', fetch)
  expect(await searchImages('海报')).toEqual({ status: 'ok', items: [hit(7)], degraded: true })
})

it('marks a local-only result as degraded and clears the notice on the next search', async () => {
  const api = vi.fn()
    .mockResolvedValueOnce({ status: 'ok', items: [hit(1)], degraded: true })
    .mockResolvedValueOnce(result(2))
  const store = new SearchStore(api, async () => [])
  await store.search('a')
  expect(store.snapshot().searchState).toEqual({ status: 'ok', degraded: true })
  await store.search('b')
  expect(store.snapshot().searchState).toEqual({ status: 'ok' })
})

it('degraded local result shows one small notice, results, and no error alert', async () => {
  const { user, resultIds, input } = await renderWithLibrary(async () => ({ status: 'ok', items: [hit(3)], degraded: true }))
  await user.type(input, '海报{Enter}')
  expect(screen.getByRole('status')).toHaveTextContent('AI 搜索没连上，这次结果来自本地匹配')
  expect(resultIds()).toEqual([3])
  expect(screen.queryByRole('alert')).toBeNull()
})

it('degraded search with no local match says 没找到 instead of a service error', async () => {
  const { user, input } = await renderWithLibrary(async () => ({ status: 'ok', items: [], degraded: true }))
  await user.type(input, '不存在{Enter}')
  expect(screen.getByRole('status')).toHaveTextContent('AI 搜索没连上')
  expect(screen.getByText('没有搜索结果，试试其他描述。')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
})


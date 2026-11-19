import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../App'
import type { SearchHit } from '../search/contract'
import type { ProjectApi } from '../projects/contract'
import { fakeApi as projectFakeApi } from '../projects/__tests__/fixtures'
import { getRecommendations, RecommendationError, type Purpose, type RecommendationApi, type RecommendationColumn,
  type RecommendationResponse } from './contract'

vi.mock('../SyncBar', () => ({ default: () => null }))
vi.mock('../AutoSyncControl', () => ({ default: () => null }))
vi.mock('../watch/WatchFolderPanel', () => ({ default: () => null }))
vi.mock('../AnalysisBar', () => ({ default: () => null }))
vi.mock('../DropZone', () => ({ default: () => null }))
vi.mock('../ImportPanel', () => ({ default: () => null }))
vi.mock('../PipButton', () => ({ default: () => null }))
afterEach(() => vi.unstubAllGlobals())

function image(id: number, video = false): SearchHit {
  return { image_id: id, score: 0, url: `/images/${id}.png`, width: 1080, height: 1440, original_name: `参考图 ${id}`,
    created_at: '', is_video: video, sources: [{ post_id: id, position: 0, platform: 'xhs', source_id: `n${id}`,
      canonical_url: null, title: '帖子', is_video: video }] }
}
const LABELS: Record<Purpose, string> = { layout: '排版', palette: '配色', style: '风格' }
function column(purpose: Purpose, ids: number[], status: RecommendationColumn['status'] = 'ok'): RecommendationColumn {
  return { purpose, label: LABELS[purpose], status, recall_source: `${purpose}_v1`, reviewed_ids: [...ids, 99],
    usage: { requests: 1, prompt_tokens: 5000, completion_tokens: 120, latency_ms: 4200 },
    items: ids.map((id, i) => ({ image_id: id, rank: i + 1, recall_rank: 8 - i, post_ids: [id],
      media_scope: id === 7 ? 'cover_and_description' as const : 'image' as const,
      reference_point: `参考${purpose}的${id}`, purpose })) }
}
function response(layout: number[] = [], palette: number[] = [], style: number[] = []): RecommendationResponse {
  const ids = [...new Set([...layout, ...palette, ...style])]
  return { status: 'ok', effective_query: '建筑展板，现代极简', notices: [], images: ids.map(id => image(id, id === 7)),
    columns: [column('layout', layout), column('palette', palette), column('style', style)] }
}
function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
async function renderApp(api: RecommendationApi, projectApi: ProjectApi = projectFakeApi()) {
  const fetch = vi.fn(() => { throw new Error('unexpected real HTTP') })
  vi.stubGlobal('fetch', fetch)
  const user = userEvent.setup()
  render(<App recommendationApi={api} projectApi={projectApi}
    searchApi={async () => ({ status: 'index_empty', items: [] })} libraryApi={async () => []} />)
  return { user, fetch }
}
const briefBox = () => screen.getByRole('textbox', { name: '当前创作要求' })
const previousBox = () => screen.getByRole('textbox', { name: '上一版 brief（可选）' })
const submit = () => screen.getByRole('button', { name: '获取推荐' })
const cardIds = (name: string) => {
  const list = screen.getByRole('list', { name })
  return within(list).getAllByRole('listitem').map(li => li.getAttribute('data-image-id'))
}

it('real adapter posts current and previous separately and unchanged, once', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => response([10]) })
  vi.stubGlobal('fetch', fetch)
  expect(await getRecommendations('不要复古，改现代极简', '复古建筑展板')).toEqual(response([10]))
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/recommendations/columns', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ brief: '不要复古，改现代极简', previous_brief: '复古建筑展板' }) })
  await getRecommendations('现代建筑展板')
  expect(fetch.mock.calls[1][1].body).toBe(JSON.stringify({ brief: '现代建筑展板' }))
})

it('real adapter hides raw error bodies and maps only-exclusions 422', async () => {
  const text = vi.fn(async () => 'secret provider body')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503, text }))
  await expect(getRecommendations('a')).rejects.toThrow('网络或服务出错')
  expect(text).not.toHaveBeenCalled()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 422,
    json: async () => ({ detail: { code: 'only_exclusions', message: 'x' } }) }))
  await expect(getRecommendations('不要复古')).rejects.toThrow('请描述想要的参考，而不只是排除条件')
})

it('renders three columns in review order with reference points, video scope; nothing pinned', async () => {
  const api = vi.fn(async () => response([10, 7], [3], [5, 10]))
  const { user, fetch } = await renderApp(api)
  await user.type(briefBox(), '不要复古，改现代极简'); await user.type(previousBox(), '复古建筑展板')
  await user.click(submit())
  expect(api).toHaveBeenCalledExactlyOnceWith('不要复古，改现代极简', '复古建筑展板')
  await screen.findByRole('list', { name: '排版推荐' })
  expect(cardIds('排版推荐')).toEqual(['10', '7'])
  expect(cardIds('配色推荐')).toEqual(['3'])
  expect(cardIds('风格推荐')).toEqual(['5', '10'])  // one image may serve two columns
  const layout = screen.getByRole('region', { name: '排版参考' })
  expect(within(layout).getByText('参考layout的10')).toBeInTheDocument()
  expect(within(layout).getByText(/召回第 8 名 · 复看第 1 名/)).toBeInTheDocument()
  expect(within(layout).getByText(/复看 3 张 · 1 次请求 · 5000\+120 tokens · 4\.2s/)).toBeInTheDocument()
  expect(within(layout).getAllByText('仅分析封面与简介')).toHaveLength(1)
  expect(screen.getByText('实际检索：建筑展板，现代极简')).toBeInTheDocument()
  expect(within(layout).getAllByRole('group', { name: /钉选/ }).length).toBeGreaterThan(0)
  for (const call of fetch.mock.calls) expect(call).toEqual(['/api/reference-labels'])
})

it('a failed column falls back to recall top5 and says 未复看', async () => {
  const body = response([10], [], [])
  body.columns[2] = column('style', [4], 'review_failed')
  body.columns[2].error = 'invalid: x'
  const { user } = await renderApp(async () => body)
  await user.type(briefBox(), '海报'); await user.click(submit())
  const style = await screen.findByRole('region', { name: '风格参考' })
  expect(within(style).getByRole('alert')).toHaveTextContent('视觉复看失败：invalid: x')
  expect(within(style).getByRole('list')).toBeInTheDocument()
  expect(within(style).getByText(/未复看/)).toBeInTheDocument()
  expect(within(screen.getByRole('region', { name: '配色参考' })).getByText(/不凑数/)).toBeInTheDocument()
})

it('service failure is an error with retry, distinct from no useful reference', async () => {
  const api = vi.fn<RecommendationApi>().mockRejectedValueOnce(new RecommendationError()).mockResolvedValueOnce(response())
  const { user } = await renderApp(api)
  await user.type(briefBox(), '海洋插画'); await user.click(submit())
  expect(await screen.findByRole('alert')).toHaveTextContent('网络或服务出错，请稍后重试')
  expect(screen.queryByText('三栏都没有经视觉复看确认有用的参考。')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '重试' }))
  expect(await screen.findByText('三栏都没有经视觉复看确认有用的参考。')).toBeInTheDocument()
  expect(api).toHaveBeenNthCalledWith(2, '海洋插画', undefined)
})

it('index_empty is its own state', async () => {
  const { user } = await renderApp(async () => ({ ...response(), status: 'index_empty', columns: [] }))
  await user.type(briefBox(), '建筑展板'); await user.click(submit())
  expect(await screen.findByText(/还没有可推荐的分析结果/)).toBeInTheDocument()
})

it('loading locks the form; the result then renders once', async () => {
  const pending = deferred<RecommendationResponse>()
  const api = vi.fn<RecommendationApi>().mockReturnValue(pending.promise)
  const { user } = await renderApp(api)
  await user.type(briefBox(), '建筑展板'); await user.click(submit())
  expect(submit()).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent('推荐中…')
  pending.resolve(response([10]))
  expect(await screen.findByRole('list', { name: '排版推荐' })).toBeInTheDocument()
  expect(api).toHaveBeenCalledTimes(1)
})

it('store: older success or failure after a newer brief is dropped', async () => {
  const { RecommendationStore } = await import('./store')
  const first = deferred<RecommendationResponse>(); const second = deferred<RecommendationResponse>()
  const api = vi.fn<RecommendationApi>().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
  const merge = vi.fn()
  const store = new RecommendationStore(api, merge)
  const layoutIds = () => store.snapshot().columns[0].items.map(i => i.image_id)
  const old = store.recommend('旧需求'); const current = store.recommend('新需求')
  second.resolve(response([20])); await current
  first.resolve(response([10])); await old
  expect(layoutIds()).toEqual([20])
  expect(merge).toHaveBeenCalledTimes(1)
  const third = deferred<RecommendationResponse>(); const fourth = deferred<RecommendationResponse>()
  api.mockReturnValueOnce(third.promise).mockReturnValueOnce(fourth.promise)
  const a = store.recommend('甲'); const b = store.recommend('乙')
  fourth.resolve(response([30])); await b
  third.reject(new RecommendationError()); await a
  expect(store.snapshot().status).toBe('ok')
  expect(layoutIds()).toEqual([30])
})

it('store: identical in-flight brief is not resubmitted', async () => {
  const { RecommendationStore } = await import('./store')
  const pending = deferred<RecommendationResponse>()
  const api = vi.fn<RecommendationApi>().mockReturnValue(pending.promise)
  const store = new RecommendationStore(api, () => {})
  void store.recommend('建筑', '旧'); void store.recommend('建筑', '旧')
  expect(api).toHaveBeenCalledTimes(1)
  pending.resolve(response()); await pending.promise
})

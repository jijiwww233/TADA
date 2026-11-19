import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import type { RecommendationColumn, RecommendationItem, RecommendationResponse } from '../recommendation/contract'
import type { SearchHit } from '../search/contract'
import { ProjectReferenceFlow } from './reference-flow'
import type { ProjectStore } from './store'
import { detail, fakeApi, harness, pin } from './__tests__/fixtures'

const hit = (imageId: number): SearchHit => ({ image_id: imageId, score: 0.42, url: `/images/${imageId}.png`,
  width: 1080, height: 1440, original_name: `图 ${imageId}`, created_at: '2026-11-05', is_video: false,
  sources: [{ post_id: 5, position: 1, platform: 'xhs', source_id: 'work', canonical_url: null, title: '展板', is_video: false }] })

const item = (purpose: 'layout' | 'palette' | 'style', imageId: number): RecommendationItem => ({
  image_id: imageId, rank: 1, recall_rank: 1, media_scope: 'image', post_ids: [5],
  reference_point: `参考它的${purpose}`, purpose })

const column = (purpose: 'layout' | 'palette' | 'style', label: string, imageId: number): RecommendationColumn => ({
  purpose, label, status: 'ok', error: null, recall_source: `${purpose}_v1`, reviewed_ids: [imageId],
  items: [item(purpose, imageId)],
  usage: { requests: 1, prompt_tokens: 300, completion_tokens: 40, latency_ms: 800 } })

const response = (): RecommendationResponse => ({
  status: 'ok', effective_query: '复古建筑展板', notices: [],
  usage: { requests: 3, reported_requests: 3, prompt_tokens: 900, completion_tokens: 120, latency_ms: 2500, complete: true },
  columns: [column('layout', '排版', 11), column('palette', '配色', 12), column('style', '风格', 13)],
  images: [hit(11), hit(12), hit(13)],
})

function setup(api = fakeApi(), recommend = vi.fn(async (_brief: string, _previous?: string) => response())) {
  const h = harness(api); const onOpen = vi.fn(); const onPip = vi.fn()
  render(<ProjectReferenceFlow store={h.store} recommendationApi={recommend} onOpen={onOpen} onPip={onPip} />)
  return { ...h, onOpen, onPip, recommend }
}
async function create(store: ProjectStore, name = '新项目', brief = '复古建筑展板') {
  await act(async () => { await store.createProject(name, brief) })
}

it('recommends exactly once after a project with a brief is created and shows usage', async () => {
  const h = setup()
  await create(h.store)
  expect(await screen.findByRole('heading', { name: '要参考（新项目）' })).toBeInTheDocument()
  expect(screen.getByText('项目 brief：复古建筑展板')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '打开参考小窗' })).toBeDisabled()
  await waitFor(() => expect(h.recommend).toHaveBeenCalledTimes(1))
  expect(h.recommend).toHaveBeenCalledWith('复古建筑展板', undefined)
  expect(await screen.findByRole('heading', { name: '排版参考' })).toBeInTheDocument()
  const usage = screen.getByLabelText('本次推荐用量')
  expect(usage).toHaveTextContent('3 次请求')
  expect(usage).toHaveTextContent('输入 900 tokens')
  expect(usage).toHaveTextContent('输出 120 tokens')
  expect(usage).toHaveTextContent('耗时 2.50 秒')
  expect(screen.getByText('本次推荐调用了视觉复看，产生费用。')).toBeInTheDocument()
  await act(async () => { await h.store.selectProject(3) })
  expect(h.recommend).toHaveBeenCalledTimes(1)
})

it('skips the recommendation when the new project has no brief', async () => {
  const h = setup()
  await create(h.store, '空项目', '   ')
  expect(h.api.createProject).toHaveBeenCalledWith({ name: '空项目', brief: '   ' })
  await waitFor(() => expect(h.store.snapshot().lastCreated?.seq).toBe(1))
  expect(h.recommend).not.toHaveBeenCalled()
  expect(screen.queryByText(/要参考/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '打开参考小窗' })).not.toBeInTheDocument()
})

it('pins a card with the recommendation purpose in one click, with no purpose dialog and no auto pin', async () => {
  const api = fakeApi(); const user = userEvent.setup(); const h = setup(api)
  vi.mocked(api.pinImage).mockResolvedValue(pin(3, 11, ['layout']))
  await create(h.store)
  const buttons = await screen.findAllByRole('button', { name: '钉进项目小窗' })
  expect(buttons).toHaveLength(3)
  expect(api.pinImage).not.toHaveBeenCalled()
  expect(screen.queryByRole('group', { name: /确认用途/ })).not.toBeInTheDocument()
  await user.click(buttons[0])
  await waitFor(() => expect(api.pinImage).toHaveBeenCalledExactlyOnceWith(3, 11, ['layout'], undefined))
  expect(await screen.findAllByRole('button', { name: '已钉进项目小窗' })).toHaveLength(1)
  expect(screen.queryByRole('group', { name: /确认用途/ })).not.toBeInTheDocument()
})

it('opens the reference PiP after the current project has a pin', async () => {
  const api = fakeApi(); const user = userEvent.setup()
  vi.mocked(api.getProject).mockResolvedValue(detail(3, [pin(3, 11, ['layout'])]))
  const h = setup(api)
  await create(h.store)
  const open = await screen.findByRole('button', { name: '打开参考小窗' })
  expect(open).toBeEnabled()
  await user.click(open)
  expect(h.onPip).toHaveBeenCalledTimes(1)
})

it('shows a fixed recommendation error and retries only after the user clicks', async () => {
  const api = fakeApi(); const user = userEvent.setup(); const h = setup(api)
  h.recommend.mockRejectedValueOnce(new Error('provider body sk-secret'))
  await create(h.store)
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('推荐服务暂不可用，请重试')
  expect(alert).not.toHaveTextContent('sk-secret')
  expect(h.recommend).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole('heading', { name: '排版参考' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '重试' }))
  expect(await screen.findByRole('heading', { name: '排版参考' })).toBeInTheDocument()
  expect(h.recommend).toHaveBeenCalledTimes(2)
  expect(h.recommend).toHaveBeenLastCalledWith('复古建筑展板', undefined)
})

it('fires again for a second new project with the same brief (seq increments)', async () => {
  const h = setup()
  await create(h.store, '项目甲', '复古建筑展板')
  await waitFor(() => expect(h.recommend).toHaveBeenCalledTimes(1))
  expect(await screen.findByRole('heading', { name: '要参考（项目甲）' })).toBeInTheDocument()
  await create(h.store, '项目乙', '复古建筑展板')
  await waitFor(() => expect(h.recommend).toHaveBeenCalledTimes(2))
  expect(await screen.findByRole('heading', { name: '要参考（项目乙）' })).toBeInTheDocument()
})

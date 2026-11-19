import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../App'
import { fakeApi, image, deferred } from '../projects/__tests__/fixtures'
import type { SearchResponse } from './contract'
import { SearchStore } from './store'
import { SearchBar } from './Search'
import { findSimilar } from './similar'

vi.mock('../SyncBar', () => ({ default: () => null }))
vi.mock('../AutoSyncControl', () => ({ default: () => null }))
vi.mock('../AnalysisBar', () => ({ default: () => null }))
vi.mock('../DropZone', () => ({ default: () => null }))
vi.mock('../PipButton', () => ({ default: () => null }))
afterEach(() => vi.unstubAllGlobals())
const result = (id: number): SearchResponse => ({ status: 'ok', items: [{ ...image(id), score: .9 }] })

it('detail find-similar opens search results, keeps API order and never text-searches', async () => {
  const user = userEvent.setup(), text = vi.fn(), similar = vi.fn(async () => result(8))
  render(<App projectApi={fakeApi()} searchApi={text} similarApi={similar}
    libraryApi={async () => [{ id: 5, url: '/5.png', post_id: 1, position: 0, platform: 'local', width: 1, height: 1, original_name: '图', created_at: '' }]}
    detailLoader={async () => ({ id: 5, url: '/5.png', width: 1, height: 1, original_name: '图', created_at: '', posts: [], colors: [], analysis: null })} />)
  await waitFor(() => expect(screen.getByText('已收录 1 张')).toBeTruthy())
  await user.click(screen.getByRole('img', { name: '图' }))
  await user.click(await screen.findByRole('button', { name: /^找相似$/ }))
  expect(await screen.findByRole('list', { name: '搜索结果' })).toBeTruthy()
  expect(similar).toHaveBeenCalledExactlyOnceWith(5); expect(text).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: /^关闭$/ })).toBeNull()
  expect(screen.getAllByRole('listitem').some(x => x.getAttribute('data-image-id') === '8')).toBe(true)
})

it('search-box drop accepts one image and exposes keyboard file-picker alternative', () => {
  const imageApi = vi.fn(), text = vi.fn()
  render(<SearchBar loading={false} active={false} submit={text} clear={vi.fn()} image={imageApi} />)
  const input = screen.getByRole('textbox', { name: '搜索收藏' }), file = new File(['image'], 'q.png', { type: 'image/png' })
  fireEvent.drop(input, { dataTransfer: { files: [file] } })
  expect(imageApi).toHaveBeenCalledExactlyOnceWith(file);expect(text).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: '选择图片找相似' })).toBeTruthy()
  fireEvent.drop(input, { dataTransfer: { files: [new File(['bad'], 'a.txt', { type: 'text/plain' })] } })
  expect(screen.getByRole('alert').textContent).toContain('一张图片')
})

it('latest text/image request wins; clear cancels both; retry preserves image query', async () => {
  const t = deferred<SearchResponse>(), i = deferred<SearchResponse>(), file = new File(['x'], 'x.png')
  const similar = vi.fn(() => i.promise), store = new SearchStore(() => t.promise, async () => [], similar)
  const a = store.search('原文'), b = store.similar(file)
  t.resolve(result(1)); await a; expect(store.snapshot().searchIds).toEqual([])
  i.resolve(result(2)); await b; expect(store.snapshot().searchIds).toEqual([2])
  store.retrySimilar();expect(similar).toHaveBeenLastCalledWith(file)
  store.clear();expect(store.snapshot().searchState.status).toBe('idle')
  const another = deferred<SearchResponse>(), second = new SearchStore(async () => result(3), async () => [], () => another.promise)
  const pending = second.similar(2);await second.search('文字');another.resolve(result(4));await pending
  expect(second.snapshot().searchIds).toEqual([3]);expect(second.snapshot().searchMode).toBe('text')
})

it('real similar adapter sends uploads only to search, never to import', async () => {
  const fetch = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, json: async () => result(9) }))
  vi.stubGlobal('fetch', fetch)
  const file = new File(['x'], 'q.png', { type: 'image/png' })
  await findSimilar(file); expect(fetch.mock.calls[0][0]).toBe('/api/search/image')
  await findSimilar(5); expect(fetch.mock.calls[1][0]).toBe('/api/images/5/similar')
})

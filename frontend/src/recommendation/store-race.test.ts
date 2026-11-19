import { beforeEach, expect, it, vi } from 'vitest'
import type { RecommendationApi, RecommendationColumn, RecommendationResponse } from './contract'
import { RecommendationStore } from './store'

const interactive = vi.hoisted(() => ({ moreLike: vi.fn(), saveFeedback: vi.fn() }))
vi.mock('./interactive', () => ({ moreLike: interactive.moreLike, saveFeedback: interactive.saveFeedback }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function column(purpose: RecommendationColumn['purpose'], ids: number[]): RecommendationColumn {
  return {
    purpose, label: purpose, status: 'ok', recall_source: 'test', reviewed_ids: ids,
    items: ids.map((imageId, index) => ({
      image_id: imageId, rank: index + 1, recall_rank: index + 1, media_scope: 'image',
      post_ids: [], reference_point: '参考它的哪里', purpose,
    })),
    usage: { requests: 1, prompt_tokens: 10, completion_tokens: 5, latency_ms: 20 },
  }
}

function response(ids: number[]): RecommendationResponse {
  return { status: 'ok', effective_query: 'q', columns: [column('layout', ids)], images: [], notices: [] }
}

beforeEach(() => {
  interactive.moreLike.mockReset()
  interactive.saveFeedback.mockReset()
})

// Static review item 17: an exclusion clicked while "更多类似" is in flight must win.
it('more-like result cannot bring back an image excluded while it was in flight', async () => {
  const pending = deferred<RecommendationResponse>()
  interactive.moreLike.mockReturnValue(pending.promise)
  const api = vi.fn<RecommendationApi>().mockResolvedValue(response([10]))
  const store = new RecommendationStore(api, vi.fn())
  await store.recommend('建筑')
  const run = store.moreLike(10, 'layout')
  store.exclude(20)
  pending.resolve(response([10, 20]))
  await run
  expect(store.snapshot().columns[0].items.map(item => item.image_id)).toEqual([10])
  expect(store.snapshot().busyColumn).toBeUndefined()
})

// Static review item 18: a result belongs to the project that asked for it.
it('switching project ignores the in-flight result of the previous project', async () => {
  const pending = deferred<RecommendationResponse>()
  const api = vi.fn<RecommendationApi>().mockReturnValue(pending.promise)
  const merge = vi.fn()
  const store = new RecommendationStore(api, merge)
  store.configure({ project_id: 1 })
  const run = store.recommend('建筑展板')
  store.configure({ project_id: 2 })
  expect(store.snapshot().status).toBe('idle')
  expect(store.snapshot().columns).toEqual([])
  pending.resolve(response([10]))
  await run
  expect(store.snapshot().columns).toEqual([])
  expect(store.snapshot().status).toBe('idle')
  expect(merge).not.toHaveBeenCalled()
})

it('feedback on a column left over from the previous project is refused', async () => {
  const api = vi.fn<RecommendationApi>().mockResolvedValue(response([10]))
  interactive.saveFeedback.mockResolvedValue(undefined)
  const store = new RecommendationStore(api, vi.fn())
  store.configure({ project_id: 1 })
  await store.recommend('建筑')
  store.configure({ project_id: 2 })
  // The old column stays visible (its image id may still be pinned into project 2), but
  // feedback is a write into the current project and must wait for its own result.
  await store.rate(10, 'layout', true)
  expect(interactive.saveFeedback).not.toHaveBeenCalled()
  expect(store.snapshot().actionError).toContain('上一个项目')
  api.mockResolvedValue(response([20]))
  await store.recommend('建筑')
  await store.rate(20, 'layout', true)
  expect(interactive.saveFeedback).toHaveBeenCalledWith(2, 20, 'layout', true)
})

it('the same brief after a project switch is a new request, not a duplicate', async () => {
  const first = deferred<RecommendationResponse>()
  const second = deferred<RecommendationResponse>()
  const api = vi.fn<RecommendationApi>().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
  const store = new RecommendationStore(api, vi.fn())
  store.configure({ project_id: 1 })
  const a = store.recommend('建筑')
  store.configure({ project_id: 2 })
  const b = store.recommend('建筑')
  expect(api).toHaveBeenCalledTimes(2)
  second.resolve(response([20]))
  await b
  first.resolve(response([10]))
  await a
  expect(store.snapshot().columns[0].items.map(item => item.image_id)).toEqual([20])
})

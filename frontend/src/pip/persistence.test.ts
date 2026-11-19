import { describe, expect, it, vi } from 'vitest'
import { createPipSceneApi, PipSceneConflict, PipSceneError } from './persistence'
import { emptyScene, type PipScene } from './scene'
import { createTaskQueue } from './queue'
import { hasImageDragType, readDraggedImageId, startImageDrag } from './dragData'

const scene: PipScene = { ...emptyScene(), items: [{
  image_id: 7, x: 0, y: 0, width: 100, height: 50, rotation: 0, flip_x: false, grayscale: false, opacity: 1, crop: null,
}] }
const response = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body })

describe('pip scene api', () => {
  it('reads an empty scene with revision zero', async () => {
    const fetcher = vi.fn(async () => response(200, { revision: 0, updated_at: null, scene: null }))
    const api = createPipSceneApi(fetcher as unknown as typeof fetch)
    await expect(api.get(3)).resolves.toEqual({ revision: 0, scene: null })
    expect(fetcher).toHaveBeenCalledWith('/api/projects/3/pip-scene', undefined)
  })
  it('saves with the current revision and returns the new one', async () => {
    const fetcher = vi.fn(async () => response(200, { revision: 5, updated_at: '2026-11-05', scene }))
    const api = createPipSceneApi(fetcher as unknown as typeof fetch)
    await expect(api.save(3, 4, scene)).resolves.toMatchObject({ revision: 5 })
    const init = (fetcher.mock.calls[0] as unknown[])[1] as RequestInit
    expect(init.method).toBe('PUT')
    expect(JSON.parse(String(init.body))).toMatchObject({ revision: 4, scene: { version: 1 } })
  })
  it('surfaces a revision conflict with the server scene instead of overwriting', async () => {
    const fetcher = vi.fn(async () => response(409, { detail: {
      code: 'revision_conflict', current_revision: 9, current_scene: scene,
    } }))
    const api = createPipSceneApi(fetcher as unknown as typeof fetch)
    const error = await api.save(3, 4, scene).catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(PipSceneConflict)
    expect(error).toMatchObject({ currentRevision: 9, status: 409, code: 'revision_conflict' })
    expect((error as PipSceneConflict).currentScene).toEqual(scene)
  })
  it('maps unknown errors without leaking raw bodies', async () => {
    const fetcher = vi.fn(async () => response(404, { detail: { code: 'project_not_found' } }))
    const api = createPipSceneApi(fetcher as unknown as typeof fetch)
    const error = await api.get(3).catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(PipSceneError)
    expect(error).toMatchObject({ code: 'project_not_found', status: 404 })
  })
  it('rejects invalid scene data before sending', async () => {
    const fetcher = vi.fn()
    const api = createPipSceneApi(fetcher as unknown as typeof fetch)
    const bad = { ...scene, items: [{ ...scene.items[0], image_id: 0 }] }
    const error = await api.save(1, 0, bad).catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(PipSceneError)
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('treats network failures as unknown state', async () => {
    const api = createPipSceneApi(vi.fn(async () => { throw new Error('offline') }) as unknown as typeof fetch)
    await expect(api.get(1)).rejects.toMatchObject({ code: 'network' })
  })
})

describe('drag data', () => {
  const transfer = (values: Record<string, string>) => {
    const types = Object.keys(values)
    return {
      types,
      setData: vi.fn((type: string, value: string) => { values[type] = value; types.push(type) }),
      getData: (type: string) => values[type] ?? '',
    }
  }
  it('writes the local image MIME and reads only that MIME', () => {
    const data = transfer({})
    expect(startImageDrag({ dataTransfer: data } as never, 42)).toBe(true)
    expect(data.setData).toHaveBeenCalledWith('application/x-wominmin-image', '42')
    expect(readDraggedImageId(data)).toBe(42)
    expect(hasImageDragType(data)).toBe(true)
  })
  it('rejects external URLs, text, floats and negatives', () => {
    const data = transfer({
      'application/x-wominmin-image': 'https://xhscdn.example/a.jpg',
      'text/uri-list': 'https://example.org/a.jpg',
    })
    expect(readDraggedImageId(data)).toBeNull()
    expect(readDraggedImageId(null)).toBeNull()
    const another = transfer({ 'application/x-wominmin-image': '-3' })
    expect(readDraggedImageId(another)).toBeNull()
    const float = transfer({ 'application/x-wominmin-image': '3.5' })
    expect(readDraggedImageId(float)).toBeNull()
  })
  it('does not start a drag for invalid ids', () => {
    const data = transfer({})
    expect(startImageDrag({ dataTransfer: data } as never, 0)).toBe(false)
    expect(data.setData).not.toHaveBeenCalled()
  })
})

describe('task queue', () => {
  it('never exceeds the concurrency limit', async () => {
    const queue = createTaskQueue(2)
    let active = 0
    let peak = 0
    const gate = () => new Promise<void>(resolve => { setTimeout(resolve, 5) })
    await Promise.all(Array.from({ length: 6 }, () => queue.run(async () => {
      active += 1; peak = Math.max(peak, active)
      await gate()
      active -= 1
    })))
    expect(peak).toBe(2)
    expect(queue.pending()).toBe(0)
    expect(queue.active()).toBe(0)
  })
  it('propagates task failures and keeps draining', async () => {
    const queue = createTaskQueue(1)
    const failed = queue.run(async () => { throw new Error('decode failed') })
    const ok = queue.run(async () => 7)
    await expect(failed).rejects.toThrow('decode failed')
    await expect(ok).resolves.toBe(7)
    expect(queue.active()).toBe(0)
  })
})

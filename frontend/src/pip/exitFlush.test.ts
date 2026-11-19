import { describe, expect, it, vi } from 'vitest'
import { flushSceneOnExit, KEEPALIVE_LIMIT_BYTES, utf8ByteLength } from './exitFlush'
import { emptyScene, type PipScene } from './scene'

const scene: PipScene = {
  ...emptyScene(),
  items: [{
    image_id: 7, x: 0, y: 0, width: 100, height: 50, rotation: 0,
    flip_x: false, grayscale: true, opacity: 1, crop: null,
  }],
}

const fakeFetch = () => vi.fn(async () => ({ ok: true, status: 200 }))
const asFetcher = (mock: ReturnType<typeof fakeFetch>) => mock as unknown as typeof fetch

describe('exit flush', () => {
  it('sends the latest scene as a keepalive PUT with the current revision', () => {
    const fetcher = fakeFetch()
    const result = flushSceneOnExit({ projectId: 3, revision: 4, scene, fetcher: asFetcher(fetcher) })
    expect(result).toMatchObject({ sent: true, keepalive: true })
    expect(result.bytes).toBeGreaterThan(0)
    expect(result.bytes).toBeLessThanOrEqual(KEEPALIVE_LIMIT_BYTES)
    expect(fetcher).toHaveBeenCalledTimes(1)
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/projects/3/pip-scene')
    expect(init.method).toBe('PUT')
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(init.keepalive).toBe(true)
    expect(JSON.parse(String(init.body))).toMatchObject({
      revision: 4,
      scene: { version: 1, items: [{ image_id: 7, grayscale: true }], notes: [] },
    })
  })

  it('falls back to a plain fetch when the body exceeds the keepalive budget', () => {
    const fetcher = fakeFetch()
    const result = flushSceneOnExit({ projectId: 3, revision: 0, scene, fetcher: asFetcher(fetcher), limit: 16 })
    expect(result.keepalive).toBe(false)
    expect(result.bytes).toBeGreaterThan(16)
    const init = (fetcher.mock.calls[0] as unknown as [string, RequestInit])[1]
    expect(init.method).toBe('PUT')
    expect('keepalive' in init).toBe(false)
  })

  it('counts UTF-8 bytes, not UTF-16 code units', () => {
    expect(utf8ByteLength('ab')).toBe(2)
    expect(utf8ByteLength('\u4e2d')).toBe(3)
  })

  it('does nothing without a saved project or with an invalid revision', () => {
    const fetcher = fakeFetch()
    expect(flushSceneOnExit({ projectId: null, revision: 0, scene, fetcher: asFetcher(fetcher) })).toEqual({
      sent: false, keepalive: false, bytes: 0,
    })
    expect(flushSceneOnExit({ projectId: 3, revision: -1, scene, fetcher: asFetcher(fetcher) })).toMatchObject({ sent: false })
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('swallows a rejected fetch instead of producing an unhandled rejection', async () => {
    const fetcher = vi.fn(async () => { throw new Error('offline') })
    const result = flushSceneOnExit({ projectId: 3, revision: 1, scene, fetcher: fetcher as unknown as typeof fetch })
    expect(result.sent).toBe(true)
    await new Promise(resolve => setTimeout(resolve, 10))
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('swallows a synchronous fetch throw', () => {
    const fetcher = vi.fn(() => { throw new Error('no network') })
    const result = flushSceneOnExit({ projectId: 3, revision: 1, scene, fetcher: fetcher as unknown as typeof fetch })
    expect(result).toMatchObject({ sent: false, keepalive: true })
  })

  it('skips a scene that cannot be serialized', () => {
    const fetcher = fakeFetch()
    const broken = { ...scene, items: [{ ...scene.items[0], width: Number.NaN }] } as PipScene
    expect(flushSceneOnExit({ projectId: 3, revision: 0, scene: broken, fetcher: asFetcher(fetcher) })).toMatchObject({ sent: false })
    expect(fetcher).not.toHaveBeenCalled()
  })
})

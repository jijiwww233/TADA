import { describe, expect, it, vi } from 'vitest'
import {
  chooseTextureTier, createTextureCache, textureTierSides, TEXTURE_MAX_LONG_EDGE,
} from './texture'

function harness(options: { width?: number; height?: number; maxEntries?: number; maxBytes?: number } = {}) {
  const drawImage = vi.fn()
  const close = vi.fn()
  const decode = vi.fn(async () => ({
    source: { fake: true } as unknown as CanvasImageSource,
    width: options.width ?? 4000,
    height: options.height ?? 3000,
    close,
  }))
  const createCanvas = () => ({
    width: 0,
    height: 0,
    getContext: () => ({ drawImage, translate: vi.fn(), scale: vi.fn(), filter: '' }),
  }) as unknown as HTMLCanvasElement
  const cache = createTextureCache({
    decode,
    createCanvas,
    ...(options.maxEntries !== undefined ? { maxEntries: options.maxEntries } : {}),
    ...(options.maxBytes !== undefined ? { maxBytes: options.maxBytes } : {}),
  })
  return { cache, decode, close, drawImage }
}

const BYTES_512 = 512 * 512 * 4

describe('texture tier strategy', () => {
  it('uses the 512 -> 1024 -> natural ladder with a 2048 cap', () => {
    expect(textureTierSides(3000)).toEqual([512, 1024, 2048])
    expect(textureTierSides(5000)).toEqual([512, 1024, 2048])
    expect(textureTierSides(900)).toEqual([512, 900])
    expect(textureTierSides(300)).toEqual([300])
    expect(textureTierSides(Number.NaN)).toEqual([512, 1024, TEXTURE_MAX_LONG_EDGE])
  })

  it('upgrades when the screen long edge outgrows the current texture', () => {
    expect(chooseTextureTier(400, 3000, null)).toBe(512)
    expect(chooseTextureTier(600, 3000, 512)).toBe(1024)
    expect(chooseTextureTier(1400, 3000, 1024)).toBe(2048)
    expect(chooseTextureTier(600, 3000, null)).toBe(1024)
  })

  it('downgrades after zooming back out', () => {
    expect(chooseTextureTier(400, 3000, 1024)).toBe(512)
    expect(chooseTextureTier(1000, 3000, 2048)).toBe(1024)
    expect(chooseTextureTier(100, 3000, 2048)).toBe(512)
  })

  it('keeps a hysteresis band so a zoom level does not flip tiers', () => {
    // Between 529.9 and 588.8 the tier depends on where you came from.
    expect(chooseTextureTier(570, 3000, 512)).toBe(512)
    expect(chooseTextureTier(570, 3000, 1024)).toBe(1024)
    expect(chooseTextureTier(570, 3000, null)).toBe(512)
    // With a natural 900 tier the same idea holds one step down.
    expect(chooseTextureTier(550, 900, 900)).toBe(900)
    expect(chooseTextureTier(550, 900, 512)).toBe(512)
  })

  it('never asks for more than the natural size and caps it at 2048', () => {
    expect(chooseTextureTier(5000, 5000, null)).toBe(2048)
    expect(chooseTextureTier(5000, 5000, 1024)).toBe(2048)
    expect(chooseTextureTier(5000, 5000, 2048)).toBe(2048)
    expect(chooseTextureTier(5000, 900, 512)).toBe(900)
    expect(chooseTextureTier(5000, 300, null)).toBe(300)
  })
})

describe('tiered texture cache', () => {
  it('builds 512 by default and follows explicit tiers', async () => {
    const { cache, decode, close } = harness()
    const base = await cache.get('a', null, false, false)
    expect(base).toMatchObject({ width: 512, height: 384, tier: 512, bytes: 512 * 384 * 4 })
    const mid = await cache.get('a', null, false, false, 1024)
    expect(mid).toMatchObject({ width: 1024, tier: 1024 })
    const full = await cache.get('a', null, false, false, 2048)
    expect(full).toMatchObject({ width: 2048, height: 1536, tier: 2048 })
    expect(full.bytes).toBe(2048 * 1536 * 4)
    expect(decode).toHaveBeenCalledTimes(3)
    expect(close).toHaveBeenCalledTimes(3)
  })

  it('never upscales a small original', async () => {
    const { cache } = harness({ width: 300, height: 200 })
    const texture = await cache.get('small', null, false, false, 2048)
    expect(texture).toMatchObject({ width: 300, height: 200 })
  })

  it('reuses one decode for identical requests and separates variants', async () => {
    const { cache, decode } = harness()
    await Promise.all([
      cache.get('a', null, false, false, 512),
      cache.get('a', null, false, false, 512),
    ])
    expect(decode).toHaveBeenCalledTimes(1)
    await cache.get('a', null, true, false, 512)
    await cache.get('a', null, false, true, 512)
    expect(decode).toHaveBeenCalledTimes(3)
    expect(cache.size()).toBe(3)
  })

  it('evicts least-recently-used textures when the byte budget is exceeded', async () => {
    const { cache } = harness({ width: 1000, height: 1000, maxBytes: BYTES_512 * 3 })
    await cache.get('a', null, false, false, 512)
    await cache.get('b', null, false, false, 512)
    await cache.get('c', null, false, false, 512)
    expect(cache.size()).toBe(3)
    expect(cache.stats().bytes).toBe(BYTES_512 * 3)
    await cache.get('d', null, false, false, 512)
    expect(cache.size()).toBe(3)
    expect(cache.stats().bytes).toBeLessThanOrEqual(BYTES_512 * 3)
  })

  it('evicts the oldest entry, not the most recently used one', async () => {
    const { cache, decode } = harness({ width: 1000, height: 1000, maxEntries: 2 })
    await cache.get('a', null, false, false, 512)
    await cache.get('b', null, false, false, 512)
    await cache.get('a', null, false, false, 512) // a becomes most recently used
    await cache.get('c', null, false, false, 512) // should evict b
    expect(decode).toHaveBeenCalledTimes(3)
    await cache.get('a', null, false, false, 512)
    expect(decode).toHaveBeenCalledTimes(3)
    await cache.get('b', null, false, false, 512)
    expect(decode).toHaveBeenCalledTimes(4)
  })

  it('reports tier counts, bytes and upgrade/downgrade transitions', async () => {
    const { cache } = harness()
    await cache.get('a', null, false, false, 512)
    await cache.get('a', null, false, false, 1024)
    await cache.get('a', null, false, false, 2048)
    await cache.get('a', null, false, false, 1024)
    const stats = cache.stats()
    expect(stats).toMatchObject({ upgrades: 2, downgrades: 1 })
    expect(stats.tiers).toMatchObject({ '1024': 1, '2048': 1 })
    expect(stats.bytes).toBeGreaterThan(0)
    expect(stats.budget).toBeGreaterThan(stats.bytes)
    expect(cache.size()).toBe(3)
  })

  it('clears textures and statistics on demand', async () => {
    const { cache } = harness()
    await cache.get('a', null, false, false, 512)
    cache.clear()
    expect(cache.size()).toBe(0)
    expect(cache.stats()).toMatchObject({ entries: 0, bytes: 0, upgrades: 0, downgrades: 0, tiers: {} })
  })
})

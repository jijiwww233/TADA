import { DEFAULT_CROP, type CropRect } from './geometry'
import { createTaskQueue } from './queue'

export interface Texture {
  source: CanvasImageSource
  width: number
  height: number
  /** Estimated decoded memory in bytes (width * height * 4), used by the cache budget. */
  bytes: number
  /** Long-edge cap this texture was built for: 512, 1024 or the (capped) natural size. */
  tier: number
  key: string
}

interface DecodedImage {
  source: CanvasImageSource
  width: number
  height: number
  close?: () => void
}

export const TEXTURE_BASE_LONG_EDGE = 512
export const TEXTURE_MID_LONG_EDGE = 1024
/** The original image is never decoded above this long edge. */
export const TEXTURE_MAX_LONG_EDGE = 2048
/** Upgrade when the on-screen long edge exceeds the current texture by ~15%. */
export const TEXTURE_UPGRADE_RATIO = 1.15
/** Downgrade only when the screen size is clearly below the lower tier (hysteresis). */
export const TEXTURE_DOWNGRADE_RATIO = 0.9
/** Default decoded-memory budget; devices with more RAM get a slightly larger one. */
export const TEXTURE_BUDGET_BYTES = 384 * 1024 * 1024

/**
 * Tier long edges for one image: 512 -> 1024 -> natural, with the natural size capped at 2048.
 * Small originals collapse the ladder so the texture is never upscaled.
 */
export function textureTierSides(naturalLongSide: number): number[] {
  const natural = Number.isFinite(naturalLongSide) && naturalLongSide > 0
    ? Math.min(TEXTURE_MAX_LONG_EDGE, Math.max(1, Math.round(naturalLongSide)))
    : TEXTURE_MAX_LONG_EDGE
  const sides: number[] = []
  if (natural > TEXTURE_BASE_LONG_EDGE) sides.push(TEXTURE_BASE_LONG_EDGE)
  if (natural > TEXTURE_MID_LONG_EDGE) sides.push(TEXTURE_MID_LONG_EDGE)
  sides.push(natural)
  return sides
}

/**
 * Pick the texture long edge for an on-screen size.
 *
 * - upgrade when displayPx > currentTier * 1.15 (the current texture is visibly too small);
 * - downgrade only when displayPx < lowerTier * 1.15 * 0.9, which leaves a deliberate
 *   hysteresis band: the same zoom level never flips the tier back and forth.
 * Returns the tier long edge itself (512 / 1024 / natural <= 2048).
 */
export function chooseTextureTier(displayPx: number, naturalLongSide: number, currentTier?: number | null): number {
  const sides = textureTierSides(naturalLongSide)
  const last = sides.length - 1
  const display = Number.isFinite(displayPx) && displayPx > 0 ? displayPx : 0
  const coverage = (side: number) => side * TEXTURE_UPGRADE_RATIO
  let index = currentTier !== null && currentTier !== undefined && Number.isFinite(currentTier)
    ? sides.indexOf(currentTier)
    : -1
  if (index < 0) {
    // No usable previous tier: stateless sharp-enough choice (no hysteresis).
    index = sides.findIndex(side => coverage(side) >= display)
    if (index < 0) index = last
  }
  while (index < last && display > coverage(sides[index])) index += 1
  while (index > 0 && display < coverage(sides[index - 1]) * TEXTURE_DOWNGRADE_RATIO) index -= 1
  return sides[index]
}

/** 384 MiB by default; roughly 96 MiB per reported GB of device memory, clamped to 192–512 MiB. */
export function defaultTextureBudget(): number {
  const memory = typeof navigator === 'undefined'
    ? undefined
    : (navigator as Navigator & { deviceMemory?: number }).deviceMemory
  if (typeof memory === 'number' && Number.isFinite(memory) && memory > 0) {
    return Math.round(Math.min(512, Math.max(192, memory * 96)) * 1024 * 1024)
  }
  return TEXTURE_BUDGET_BYTES
}

export interface TextureCacheStats {
  entries: number
  bytes: number
  budget: number
  /** Cached texture count per long-edge tier, e.g. { '512': 3, '1024': 1 }. */
  tiers: Record<string, number>
  upgrades: number
  downgrades: number
}

export interface TextureCacheOptions {
  decode?: (url: string) => Promise<DecodedImage>
  createCanvas?: () => HTMLCanvasElement
  maxEntries?: number
  /** Decoded-memory budget in bytes; eviction is LRU. */
  maxBytes?: number
  /** Base tier when callers do not pass an explicit one (kept for the old 512-only behaviour). */
  baseMaxSize?: number
  concurrency?: number
}

export interface TextureCache {
  get(url: string, crop: CropRect | null, flipX: boolean, grayscale: boolean, maxSize?: number): Promise<Texture>
  stats(): TextureCacheStats
  clear(): void
  size(): number
}

const keyOf = (url: string, crop: CropRect | null, flipX: boolean, grayscale: boolean, maxSize: number) => {
  const rect = crop ?? DEFAULT_CROP
  return [url, rect.x, rect.y, rect.width, rect.height, flipX ? 1 : 0, grayscale ? 1 : 0, maxSize].join('|')
}

async function defaultDecode(url: string): Promise<DecodedImage> {
  if (typeof createImageBitmap === 'function' && typeof fetch === 'function') {
    const response = await fetch(url)
    if (!response.ok) throw new Error('读取图片失败（' + response.status + '）')
    const bitmap = await createImageBitmap(await response.blob())
    return { source: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close() }
  }
  return new Promise<DecodedImage>((resolve, reject) => {
    const image = new Image()
    image.decoding = 'async'
    image.onload = () => resolve({ source: image, width: image.naturalWidth, height: image.naturalHeight })
    image.onerror = () => reject(new Error('读取图片失败'))
    image.src = url
  })
}

/**
 * Downscaled, cropped, flipped and optionally grayscale textures with view-size driven tiers.
 *
 * Tiers: 512 -> 1024 -> natural (capped at 2048). A zoomed-in image asks for a sharper tier once
 * its on-screen long edge passes the current texture by 15%; zooming back out drops a tier with a
 * hysteresis band so tiers do not oscillate. Memory is estimated as width * height * 4 per decoded
 * texture and bounded by an LRU byte budget, so a 100-image board cannot keep 100 full-size bitmaps
 * alive. Decoding is bounded by a queue; the source files are never modified.
 */
export function createTextureCache(options: TextureCacheOptions = {}): TextureCache {
  const decode = options.decode ?? defaultDecode
  const createCanvas = options.createCanvas ?? (() => document.createElement('canvas'))
  const maxEntries = options.maxEntries ?? 160
  const maxBytes = options.maxBytes ?? defaultTextureBudget()
  const baseMaxSize = options.baseMaxSize ?? TEXTURE_BASE_LONG_EDGE
  const queue = createTaskQueue(options.concurrency ?? 4)
  const cache = new Map<string, Texture>()
  const pending = new Map<string, Promise<Texture>>()
  const lastTierByUrl = new Map<string, number>()
  let totalBytes = 0
  let upgrades = 0
  let downgrades = 0

  const drop = (key: string) => {
    const entry = cache.get(key)
    if (!entry) return
    cache.delete(key)
    totalBytes -= entry.bytes
  }

  const remember = (key: string, texture: Texture) => {
    drop(key)
    cache.set(key, texture)
    totalBytes += texture.bytes
    while (cache.size > maxEntries || (totalBytes > maxBytes && cache.size > 1)) {
      const oldest = cache.keys().next().value
      if (oldest === undefined) break
      drop(oldest)
    }
    if (totalBytes < 0) totalBytes = 0
  }

  async function build(url: string, crop: CropRect | null, flipX: boolean, grayscale: boolean,
    maxSize: number): Promise<Texture> {
    const decoded = await decode(url)
    try {
      const rect = crop ?? DEFAULT_CROP
      const sourceWidth = Math.max(1, Math.round(decoded.width * rect.width))
      const sourceHeight = Math.max(1, Math.round(decoded.height * rect.height))
      const scale = Math.min(1, maxSize / Math.max(sourceWidth, sourceHeight))
      const width = Math.max(1, Math.round(sourceWidth * scale))
      const height = Math.max(1, Math.round(sourceHeight * scale))
      const canvas = createCanvas()
      canvas.width = width
      canvas.height = height
      const context = canvas.getContext('2d')
      if (!context) throw new Error('画布不可用')
      if (grayscale && 'filter' in context) context.filter = 'grayscale(1)'
      if (flipX) {
        context.translate(width, 0)
        context.scale(-1, 1)
      }
      context.drawImage(
        decoded.source,
        Math.round(decoded.width * rect.x), Math.round(decoded.height * rect.y), sourceWidth, sourceHeight,
        0, 0, width, height,
      )
      return {
        source: canvas, width, height, bytes: width * height * 4, tier: maxSize,
        key: keyOf(url, crop, flipX, grayscale, maxSize),
      }
    } finally {
      decoded.close?.()
    }
  }

  const noteTier = (url: string, maxSize: number) => {
    const previous = lastTierByUrl.get(url)
    if (previous !== undefined && maxSize > previous) upgrades += 1
    else if (previous !== undefined && maxSize < previous) downgrades += 1
    lastTierByUrl.set(url, maxSize)
  }

  return {
    get(url, crop, flipX, grayscale, maxSizeValue = baseMaxSize) {
      const maxSize = Math.max(1, Math.round(maxSizeValue))
      noteTier(url, maxSize)
      const key = keyOf(url, crop, flipX, grayscale, maxSize)
      const hit = cache.get(key)
      if (hit) {
        cache.delete(key)
        cache.set(key, hit)
        return Promise.resolve(hit)
      }
      const running = pending.get(key)
      if (running) return running
      const task = queue.run(() => build(url, crop, flipX, grayscale, maxSize))
        .then(texture => { remember(key, texture); return texture })
        .finally(() => { pending.delete(key) })
      pending.set(key, task)
      return task
    },
    stats() {
      const tiers: Record<string, number> = {}
      for (const texture of cache.values()) {
        const tier = String(texture.tier)
        tiers[tier] = (tiers[tier] ?? 0) + 1
      }
      return { entries: cache.size, bytes: totalBytes, budget: maxBytes, tiers, upgrades, downgrades }
    },
    clear() {
      cache.clear()
      pending.clear()
      lastTierByUrl.clear()
      totalBytes = 0
      upgrades = 0
      downgrades = 0
    },
    size() { return cache.size },
  }
}

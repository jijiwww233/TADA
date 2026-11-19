import { createContext, useContext, useEffect, useState } from 'react'

export type Swatch = { hex: string; ratio: number; L: number; C: number; h: number }
export type LabImage = { id: number; src: string; w: number; h: number; source: string; face: boolean; palette: Swatch[]; hues: number[] } & DetailFields
/**
 * Optional fields the detail page shows when they exist (analysis description / subject, tags, the post an image
 * belongs to, when it was saved). show.json has none of them today; the detail page hides whatever is missing.
 */
export type DetailFields = {
  platform?: string
  createdAt?: string
  score?: number
  description?: string | null
  subject?: string[] | null
  tags?: string[] | null
  post_id?: number | null
  position?: number | null
  post_url?: string | null
  saved_at?: string | null
  /** 帖子原标题（没有描述 / 主题时兜底） */
  post_title?: string | null
  /** 分析结果里的风格标签 */
  style_tags?: string[] | null
  /** 后端给的生效短标题（列表和详情接口都带）；卡片标题优先用它 */
  title?: string | null
}
/** items: every image (the scatter stage places the portrait itself); cards: everything except portraits, for all other demos. */
export type Manifest = { total: number; visual_ref: number; items: LabImage[]; cards: LabImage[]; fallback: string[]; libraryProjects?: { name: string; imageIds: number[] }[] }

const LIBRARY = { total: 0, visual_ref: 0 }
const FALLBACK = ['#CFE3FF', '#CFF5EA', '#FAD9E6', '#E6DDFB']

/** Loads the showcase, with optional extra images for the library. */
export function useManifest(includeLibrary = false, source = '/lab-assets/show/show.json'): Manifest | null | 'missing' {
  const [m, setM] = useState<Manifest | null | 'missing'>(null)
  useEffect(() => {
    fetch(source)
      .then(r => (r.ok ? r.json() : Promise.reject()))
      .then((j: {
        items: Omit<LabImage, 'hues'>[]
        libraryItems?: Omit<LabImage, 'hues'>[]
        projectItems?: Omit<LabImage, 'hues'>[]
        libraryProjects?: Manifest['libraryProjects']
      }) => {
        // Hues worth tinting with: the chromatic swatches, biggest first.
        const rows = includeLibrary ? [...j.items, ...(j.libraryItems ?? [])] : j.items
        const projectRows = includeLibrary ? j.projectItems ?? [] : []
        const items = [...rows, ...projectRows].map(it => ({ ...it, face: !!it.face, hues: it.palette.filter(s => s.C > 0.03).map(s => s.h) }))
        const cards = items.slice(0, rows.length).filter(it => !it.face)
        setM({ total: items.length, visual_ref: items.length, fallback: FALLBACK, items, cards, libraryProjects: includeLibrary ? j.libraryProjects : undefined })
      }, () => setM('missing'))
  }, [includeLibrary, source])
  return m
}

const ManifestContext = createContext<Manifest>({ ...LIBRARY, items: [], cards: [], fallback: FALLBACK })
export const ManifestProvider = ManifestContext.Provider
export const useImages = () => useContext(ManifestContext)

/** Repeat the list until it has n entries (demos want fixed counts). */
export function take<T>(list: T[], n: number): T[] {
  if (!list.length) return []
  return Array.from({ length: n }, (_, i) => list[i % list.length])
}

/** Same items, reordered so neighbours come from different parts of the list. */
export function spread<T>(list: T[], step = 5): T[] {
  const n = list.length
  if (n < 2) return list
  while (n % step === 0) step++
  return Array.from({ length: n }, (_, i) => list[(i * step) % n])
}

/** Small deterministic RNG so scattered layouts are the same on every load. */
export function rng(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// --- colour: memo "only keep the hue; lightness/chroma unified in OKLCH" ---

export function oklchToHex(L: number, C: number, h: number): string {
  const a = C * Math.cos((h * Math.PI) / 180)
  const b = C * Math.sin((h * Math.PI) / 180)
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3
  const rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map(v => {
    const c = Math.min(1, Math.max(0, v))
    const g = c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055
    return Math.round(g * 255).toString(16).padStart(2, '0')
  })
  return `#${rgb.join('')}`
}

/**
 * Up to `count` distinct hues from the collection, weighted by how much of each image they cover,
 * re-lit to the memo's soft range (default L 0.9 / C 0.05). Falls back to the memo colours.
 */
export function collectionPalette(m: Manifest, count = 4, L = 0.9, C = 0.05): string[] {
  const weight = new Map<number, number>() // 10° buckets
  for (const it of m.items) for (const s of it.palette) {
    if (s.C <= 0.03) continue
    const b = Math.round(s.h / 10) % 36
    weight.set(b, (weight.get(b) ?? 0) + s.ratio * Math.min(s.C, 0.12))
  }
  const picked: number[] = []
  for (const [b] of [...weight].sort((x, y) => y[1] - x[1])) {
    const h = b * 10
    if (picked.every(p => Math.min(Math.abs(p - h), 360 - Math.abs(p - h)) > 25)) picked.push(h)
    if (picked.length === count) break
  }
  return [...picked.map(h => oklchToHex(L, C, h)), ...m.fallback].slice(0, count)
}

/**
 * Tracing-paper tone: the most present *warm* hue of the collection (rust → mustard, 30°–110°), pushed very pale.
 * Returns the hue in degrees; lab.css builds the paper and the warm ink from it.
 */
export function warmHue(m: Manifest): number {
  let best = 75, bestW = -1
  const w = new Map<number, number>()
  for (const it of m.items) for (const s of it.palette) {
    if (s.C <= 0.02 || s.h < 30 || s.h > 110) continue
    const b = Math.round(s.h / 5) * 5
    w.set(b, (w.get(b) ?? 0) + s.ratio * Math.min(s.C, 0.1))
  }
  for (const [h, v] of w) if (v > bestW) { best = h; bestW = v }
  return best
}

import type { ImageDetail, ImageItem } from '../api'
import type { DemoData, LibraryImage, ReferenceRow } from '../api/wiring'
import { take } from '../lab/data'
import type { LabImage, Manifest, Swatch } from '../lab/data'
import type { ProjectDetail } from '../projects/contract'
import type { RecommendationResponse } from '../recommendation/contract'
import type { SearchHit } from '../search/contract'
import type { Project } from './Projects'

export const platformKeys = ['xhs', 'pinterest', 'zcool', 'huaban', 'local'] as const
const demoProjects = ['品牌手册 · 2026 春', '咖啡包装参考', '日式版式研究']
const purposes = ['layout', 'palette', 'style', 'subject']
export const emptyManifest: Manifest = { cards: [], items: [], fallback: [], total: 0, visual_ref: 0 }

export const demoReferenceQuery = '咖啡包装，想要复古感'

const coffeeReferences = [
  { file: '10.jpg', reason: '小标题靠上，大色块留在中间。咖啡包装可以把品牌名和主图分开，让包装在货架上更容易认。' },
  { file: '08.jpg', reason: '暖黄和黑色形成高对比。可以让暖黄占大面积，用黑字突出豆种和烘焙度。' },
  { file: '00.jpg', reason: '手写字和单色线描有老招牌的味道。可以用同样的笔触画产地风景，让包装更有年代感。' },
]

export function demoReferenceResult(text: string, cards: LabImage[]) {
  if (text.trim() !== demoReferenceQuery) return null
  const reasons: Record<number, string> = {}
  const groups = coffeeReferences.map(({ file, reason }) => {
    const item = cards.find(card => card.src.endsWith(`/${file}`))
    if (!item) return []
    reasons[item.id] = reason
    return [item]
  })
  const items = groups.flat()
  return items.length === coffeeReferences.length ? { text, items, groups, reasons } : null
}

function swatch(color: LibraryImage['colors'][number]): Swatch {
  const [r, g, b] = [1, 3, 5].map(i => parseInt(color.hex.slice(i, i + 2), 16) / 255)
  const max = Math.max(r, g, b)
  const c = max - Math.min(r, g, b)
  const h = c === 0 ? 0 : ((max === r ? (g - b) / c : max === g ? 2 + (b - r) / c : 4 + (r - g) / c) + 6) % 6 * 60
  return { hex: color.hex, ratio: color.ratio, L: (max + Math.min(r, g, b)) / 2, C: c, h }
}

export function fromLibrary(item: LibraryImage): LabImage {
  const palette = item.colors.map(swatch)
  return {
    id: item.id, src: item.url, w: item.width || 1, h: item.height || 1, source: item.original_name ?? '',
    face: false, palette, hues: palette.filter(color => color.C > 0.03).map(color => color.h),
    platform: item.platform, post_id: item.post_id, position: item.position, createdAt: item.created_at,
  }
}

export function mergeDetail(item: LabImage, detail: NonNullable<Awaited<ReturnType<import('../api/wiring').WiringApi['getDetail']>>>): LabImage {
  const post = detail.posts.find(value => value.id === item.post_id) ?? detail.posts[0]
  return {
    ...item, src: detail.download.url, source: detail.download.filename ?? item.source,
    description: detail.description, subject: detail.subject, style_tags: detail.styleTags, tags: detail.tags,
    saved_at: detail.savedAt, post_title: post?.title ?? item.post_title, palette: detail.colors.map(swatch),
  }
}

function demoRow(item: LabImage): LibraryImage {
  return {
    id: item.id, post_id: item.post_id ?? item.id, position: item.position ?? 0, url: item.src,
    platform: platformKeys.find(key => key === item.platform) ?? platformKeys[item.id % platformKeys.length],
    width: item.w, height: item.h, original_name: item.source, created_at: item.saved_at ?? '',
    colors: item.palette.map(({ hex, ratio }, rank) => ({ hex, ratio, rank })),
  }
}

function asHit(row: ImageItem): SearchHit {
  return {
    image_id: row.id, url: row.url, width: row.width, height: row.height, original_name: row.original_name,
    created_at: row.created_at, is_video: !!row.is_video, score: 0,
    sources: [{ platform: row.platform, post_id: row.post_id, position: row.position, source_id: null,
      canonical_url: null, title: null, is_video: !!row.is_video }],
  }
}

function asDemoDetail(item: LabImage): ImageDetail {
  const row = demoRow(item)
  return {
    id: row.id, url: row.url, width: row.width, height: row.height, original_name: row.original_name,
    created_at: row.created_at, posts: [], colors: row.colors,
    annotation: item.tags ? { tags: item.tags, description: null, edited: false } : null,
    analysis: {
      status: 'done', processing: false, model: null, visible_text: null, subject: item.subject ?? null,
      layout: null, style: item.style_tags ? { tags: item.style_tags, medium: '', style_note: '' } : null,
      description: item.description ?? null, prompt_tokens: null, completion_tokens: null,
      attempts: 0, error: null, updated_at: '',
    },
  }
}

export function demoFixture(manifest: Manifest): DemoData {
  const images = manifest.cards.map(demoRow)
  const hits = images.map(asHit)
  const columns: RecommendationResponse['columns'] = (['layout', 'palette', 'style'] as const).map((purpose, index) => ({
    purpose, label: ['排版', '配色', '风格'][index], status: 'ok', recall_source: 'demo', reviewed_ids: [],
    items: hits.filter((_, i) => i % 3 === index).map((hit, i) => ({
      image_id: hit.image_id, rank: i + 1, recall_rank: i + 1, purpose, media_scope: 'image',
      post_ids: [], reference_point: '',
    })),
    usage: { requests: 0, prompt_tokens: null, completion_tokens: null, latency_ms: 0 },
  }))
  const projects = demoProjects.map((name, index): ProjectDetail => {
    const sample = take(hits.slice(index * 3), [14, 11, 9][index])
    return { id: index + 1, name, brief: '', created_at: '2026-11-08T00:00:00Z',
      image_ids: sample.map(item => item.image_id), images: sample, pins: [], allowed_purposes: purposes }
  })
  if (manifest.libraryProjects?.length) {
    const projectImages = new Map(manifest.items.map(item => [item.id, asHit(demoRow(item))]))
    for (const project of manifest.libraryProjects) {
      const group = project.imageIds.flatMap(id => {
        const image = projectImages.get(id)
        return image ? [image] : []
      })
      projects.push({ id: projects.length + 1, name: project.name, brief: '', created_at: '2026-11-08T00:00:00Z',
        image_ids: group.map(image => image.image_id), images: group, pins: [], allowed_purposes: purposes })
    }
  }
  const days = new Map<string, number>()
  const sources = new Map<ImageItem['platform'], number>()
  for (const row of images) {
    const day = row.created_at.slice(0, 10)
    if (day) days.set(day, (days.get(day) ?? 0) + 1)
    sources.set(row.platform, (sources.get(row.platform) ?? 0) + 1)
  }
  return {
    images, details: manifest.items.map(asDemoDetail), projects,
    savedDates: new Map(manifest.items.flatMap(item => item.saved_at ? [[item.id, item.saved_at]] : [])),
    search: { status: 'ok', items: hits },
    similar: { status: 'ok', items: hits },
    recommendations: { status: 'ok', effective_query: '', columns, images: hits, notices: [] },
    pending: images.map((row): ReferenceRow => ({ ...row, category: 'uncertain', manual_reference: null, needs_confirmation: true })),
    daily: [...days].sort(([a], [b]) => a.localeCompare(b)).map(([date, count]) => ({ date, count })),
    platformCounts: [...sources].map(([platform, count]) => ({ platform, count })),
    libraryCounts: { collections: manifest.total, references: manifest.visual_ref },
  }
}

export function hitImages(hits: SearchHit[], library: LabImage[]): LabImage[] {
  const byId = new Map(library.map(item => [item.id, item]))
  return hits.map(hit => {
    const match = byId.get(hit.image_id)
    if (match) return { ...match, score: hit.score }
    const post = hit.sources[0]
    return { id: hit.image_id, src: hit.url, source: hit.original_name ?? '', w: hit.width || 1, h: hit.height || 1,
      face: false, palette: [], hues: [], platform: post?.platform, post_id: post?.post_id, position: post?.position,
      createdAt: hit.created_at, score: hit.score }
  })
}

export function projectView(project: ProjectDetail, library: LabImage[]): Project {
  return { id: String(project.id), name: project.name, note: project.brief,
    created: Date.parse(project.created_at), items: hitImages(project.images.map(row => ({ ...row, score: 0 })), library) }
}

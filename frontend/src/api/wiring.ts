import { getImage, getSyncStatus } from '../api'
import type { ColorSwatch, ImageDetail, ImageItem, ImportResult, SyncPlatform } from '../api'
import type { Annotation, AnnotationInput } from '../annotations/contract'
import { createProjectApi } from '../projects/api'
import type { Project, ProjectDetail } from '../projects/contract'
import { getRecommendations } from '../recommendation/contract'
import type { RecommendationResponse } from '../recommendation/contract'
import { searchImages } from '../search/contract'
import type { SearchResponse } from '../search/contract'
import { findSimilar } from '../search/similar'
import type { SimilarDimension } from '../search/similar'
import { importImageBatch } from '../localImport/api'
import * as watch from '../watch/api'
import type { WatchStatus } from '../watch/contract'

export type LibraryImage = ImageItem & { colors: ColorSwatch[] }
export type KeyStatus = { configured: boolean; last4: string | null; restart_required?: boolean }
export type Cooldown = { blocked: boolean; auto_blocked: boolean; manual_hold: boolean; message: string | null }
export type ReferenceRow = ImageItem & {
  category: 'visual_ref' | 'text_card' | 'life' | 'uncertain'
  manual_reference: 0 | 1 | null
  needs_confirmation: boolean
}
export type LibraryCounts = { collections: number; references: number }
export type DayCount = { date: string; count: number }
export type PlatformCount = { platform: ImageItem['platform']; count: number }
export type LibraryView = {
  platforms?: ReadonlySet<ImageItem['platform']>
  sort?: 'time' | 'color' | 'similar'
  color?: string
  similarities?: ReadonlyMap<number, number>
}

const platforms: SyncPlatform[] = ['xhs', 'pinterest', 'zcool', 'huaban']
const purposes = ['layout', 'palette', 'style', 'subject']
const noSearch: SearchResponse = { status: 'ok', items: [] }
const emptyWatch: WatchStatus = {
  allowed: false, watching: false, auto_analysis_enabled: false, analysis_configured: false,
  folders: [], daily_limit: 0, analyzed_today: 0, deferred: 0,
  estimated_cost_today: 0, estimated_cost_at_limit: 0, prices: { input_per_million: 0, output_per_million: 0 },
}
const locked: Cooldown = { blocked: true, auto_blocked: true, manual_hold: true, message: null }
type PlatformState = 'connected' | 'needs_login' | 'needs_verification' | 'blocked'
export type DemoData = {
  images?: LibraryImage[]
  details?: ImageDetail[]
  savedDates?: ReadonlyMap<number, string>
  importResult?: ImportResult
  libraryCounts?: LibraryCounts
  search?: SearchResponse
  similar?: SearchResponse
  recommendations?: RecommendationResponse | null
  projects?: ProjectDetail[]
  key?: KeyStatus
  cooldown?: Cooldown
  platformStates?: Partial<Record<SyncPlatform, PlatformState>>
  watch?: WatchStatus
  pending?: ReferenceRow[]
  daily?: DayCount[]
  platformCounts?: PlatformCount[]
}

export async function wiringJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) throw new Error(`操作失败（${response.status}）`)
  return response.json()
}

export const jsonBody = (method: 'POST' | 'PUT', body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})

function rgb(hex?: string) {
  return hex && /^#[a-f0-9]{6}$/i.test(hex) ? [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)) : null
}

function hue(hex?: string) {
  const color = rgb(hex)
  if (!color) return Infinity
  const [r, g, b] = color
  const max = Math.max(r, g, b)
  const delta = max - Math.min(r, g, b)
  if (!delta) return Infinity
  const sector = max === r ? (g - b) / delta : max === g ? 2 + (b - r) / delta : 4 + (r - g) / delta
  return (sector + 6) % 6
}

/** The visible scope is independent of how a collection or search results were fetched. */
export function selectLibraryImages(images: readonly LibraryImage[], view: LibraryView = {}) {
  const rows = images.filter(item => !view.platforms?.size || view.platforms.has(item.platform))
  const target = rgb(view.color)
  const distance = (item: LibraryImage, targetColor: number[]) => Math.min(...item.colors.map(swatch => {
    const color = rgb(swatch.hex)
    return color ? color.reduce((sum, value, i) => sum + (value - targetColor[i]) ** 2, 0) : Infinity
  }))
  const byTime = (a: LibraryImage, b: LibraryImage) =>
    (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0)
  return rows.sort((a, b) => {
    if (target) return distance(a, target) - distance(b, target) || byTime(a, b)
    if (view.sort === 'color') return hue(a.colors[0]?.hex) - hue(b.colors[0]?.hex) || byTime(a, b)
    if (view.sort === 'similar') {
      const scores = view.similarities
      return scores ? (scores.get(b.id) ?? -Infinity) - (scores.get(a.id) ?? -Infinity) : 0
    }
    return byTime(a, b)
  })
}

export function presentImage(detail: ImageDetail, savedAt: string | null = null) {
  return {
    ...detail,
    description: detail.annotation?.description ?? detail.analysis?.description ?? null,
    subject: detail.analysis?.subject ?? [],
    styleTags: detail.analysis?.style?.tags ?? [],
    tags: detail.annotation?.tags ?? detail.analysis?.subject ?? [],
    // Import timestamp (local files use mtime when present). It is not an upstream save date.
    savedAt,
    download: { url: detail.url, filename: detail.original_name },
  }
}

export type WiringMode = 'demo' | 'real'

/** Keep one instance per mode. Demo writes stay in that instance and never reach the backend. */
export function createWiringApi(mode: WiringMode, fixture: DemoData = {}) {
  const demo = mode === 'demo'
  const projects = createProjectApi()
  const details = new Map(fixture.details?.map(item => [item.id, item]))
  const demoProjects = new Map(fixture.projects?.map(project => [project.id, project]))
  const decisions = new Set<number>()
  let demoKey = fixture.key ?? { configured: false, last4: null }
  let watchState = fixture.watch ?? emptyWatch
  let nextProject = Math.max(0, ...demoProjects.keys())
  let nextFolder = Math.max(0, ...watchState.folders.map(folder => folder.id))

  async function getDetail(id: number) {
    const detail = demo ? details.get(id) : await getImage(id)
    return detail ? presentImage(detail, demo ? fixture.savedDates?.get(id) : null) : null
  }

  async function recommend(brief: string) {
    if (!brief.trim()) return null
    return demo ? fixture.recommendations ?? null : getRecommendations(brief)
  }

  function changeDemoFolder(id: number, patch: { enabled?: boolean; include_subfolders?: boolean }) {
    const existing = watchState.folders.find(folder => folder.id === id)
    if (!existing) throw new Error('监听文件夹不存在')
    const folder = { ...existing, ...patch }
    watchState = { ...watchState, folders: watchState.folders.map(item => item.id === id ? folder : item) }
    return folder
  }

  return {
    async listImages(view?: LibraryView) {
      const images = demo ? fixture.images ?? [] : await wiringJson<LibraryImage[]>('/api/images?include_palette=true')
      return selectLibraryImages(images, view)
    },
    async search(query: string) {
      if (!query.trim()) return noSearch
      if (!demo) return searchImages(query)
      const sample = fixture.search ?? noSearch
      if (sample.status !== 'ok') return sample
      const words = query.trim().toLowerCase().split(/\s+/)
      return { ...sample, items: sample.items.filter(hit => {
        const item = details.get(hit.image_id)
        const index = [hit.original_name, item?.annotation?.description ?? item?.analysis?.description,
          ...(item?.analysis?.subject ?? []), ...(item?.analysis?.style?.tags ?? []),
          ...(item?.annotation?.tags ?? [])].filter(Boolean).join(' ').toLowerCase()
        return words.every(word => index.includes(word))
      }) }
    },
    recommend,
    getDetail,
    async findSimilar(imageId: number, dimension?: SimilarDimension) {
      return demo ? fixture.similar ?? noSearch : findSimilar(imageId, dimension)
    },
    async findByColor(color: string, view?: LibraryView) {
      const images = demo ? fixture.images ?? [] : await wiringJson<LibraryImage[]>('/api/images?include_palette=true')
      return selectLibraryImages(images.filter(item => item.colors.length > 0), { ...view, color })
    },
    sourceUrl(post: ImageDetail['posts'][number]) {
      if (demo || (post.platform !== 'xhs' && !post.url)) return null
      return `/api/posts/${post.id}/source`
    },
    async setAnnotation(id: number, input: AnnotationInput) {
      if (!demo) {
        const result = await wiringJson<{ annotation: Annotation | null }>(`/api/images/${id}/annotation`, jsonBody('PUT', input))
        return result.annotation
      }
      const detail = details.get(id)
      if (!detail) return null
      const annotation = {
        description: input.description === undefined ? detail.annotation?.description ?? null : input.description,
        tags: input.tags === undefined ? detail.annotation?.tags ?? null : input.tags,
        edited: true,
      }
      const result = annotation.description === null && annotation.tags === null ? null : annotation
      details.set(id, { ...detail, annotation: result })
      return result
    },
    async listProjects() {
      if (!demo) return projects.listProjects()
      return { items: [...demoProjects.values()].map(({ id, name, brief, created_at }) =>
        ({ id, name, brief, created_at })), allowed_purposes: purposes }
    },
    async getProject(id: number) {
      return demo ? demoProjects.get(id) ?? null : projects.getProject(id)
    },
    editProjectImage(projectId: number, imageId: number, action: 'previous' | 'next' | 'remove') {
      const path = `/api/projects/${projectId}/pins/${imageId}`
      return action === 'remove'
        ? wiringJson<{ image_ids: number[] }>(path, { method: 'DELETE' })
        : wiringJson<{ image_ids: number[] }>(path + '/position', {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ direction: action }),
        })
    },
    async createProject(input: { name: string; brief: string }) {
      let project: Project
      if (demo) {
        project = { id: ++nextProject, ...input, created_at: new Date().toISOString() }
        demoProjects.set(project.id, { ...project, pins: [], images: [], image_ids: [], allowed_purposes: purposes })
      } else {
        project = await projects.createProject(input)
      }
      // A recommendation failure never invites the caller to create the same project again.
      try {
        return { project, recommendations: await recommend(input.brief), recommendationFailed: false }
      } catch {
        return { project, recommendations: null, recommendationFailed: true }
      }
    },
    async addToProject(projectId: number, imageId: number, use: string[]) {
      if (!demo) return projects.pinImage(projectId, imageId, use)
      const project = demoProjects.get(projectId)
      const image = fixture.images?.find(item => item.id === imageId)
      if (!project || !image) throw new Error('项目或图片不存在')
      const prior = project.pins.find(pin => pin.image_id === imageId)
      const pin = {
        id: prior?.id ?? Math.max(0, ...project.pins.map(item => item.id)) + 1, project_id: projectId,
        image_id: imageId, purposes: [...new Set([...(prior?.purposes ?? []), ...use])],
        note: prior?.note ?? '', created_at: prior?.created_at ?? new Date().toISOString(), needs_purpose: false,
      }
      const projectImage = {
        image_id: image.id, url: image.url, width: image.width, height: image.height,
        original_name: image.original_name, created_at: image.created_at, sources: [], is_video: !!image.is_video,
      }
      demoProjects.set(projectId, { ...project, pins: [...project.pins.filter(item => item.image_id !== imageId), pin],
        image_ids: [...new Set([...project.image_ids, imageId])],
        images: [...project.images.filter(item => item.image_id !== imageId), projectImage] })
      return pin
    },
    async getKeyStatus() {
      return demo ? demoKey : wiringJson<KeyStatus>('/api/settings/api-key', { cache: 'no-store' })
    },
    async verifyAndSaveKey(key: string) {
      if (!demo) return wiringJson<KeyStatus>('/api/settings/api-key', jsonBody('PUT', { key }))
      demoKey = { configured: true, last4: fixture.key?.last4 ?? 'DEMO' }
      return demoKey
    },
    async importLocalBatch(files: File[]): Promise<ImportResult> {
      if (!files.length) return { post_id: null, added: [], duplicates: [], failed: [] }
      if (!demo) return importImageBatch(files)
      return fixture.importResult ?? { post_id: null, added: [], duplicates: [], failed: [] }
    },
    async getLibraryCounts() {
      return demo ? fixture.libraryCounts ?? { collections: 697, references: 112 }
        : wiringJson<LibraryCounts>('/api/stats/library')
    },
    async getCooldown() {
      if (demo) return fixture.cooldown ?? locked
      return wiringJson<Cooldown>('/api/sync/xhs/cooldown', { cache: 'no-store' }).catch(() => locked)
    },
    async getPlatforms() {
      return Promise.all(platforms.map(async platform => {
        const state = demo ? fixture.platformStates?.[platform]
          : (await getSyncStatus(platform).catch(() => null))?.connection_state
        return { platform, state: state ?? null }
      }))
    },
    async getWatchStatus() {
      return demo ? watchState : watch.getWatchStatus()
    },
    async addWatchFolder(path: string) {
      if (!demo) return watch.addWatchFolder(path)
      const folder = { id: ++nextFolder, path, enabled: true, include_subfolders: true,
        last_import_at: null, imported_count: 0, last_error: null, pending_files: 0 }
      watchState = { ...watchState, folders: [...watchState.folders, folder] }
      return folder
    },
    async removeWatchFolder(id: number) {
      if (!demo) return watch.removeWatchFolder(id)
      watchState = { ...watchState, folders: watchState.folders.filter(folder => folder.id !== id) }
      return { removed: true as const }
    },
    async setWatchFolderEnabled(id: number, enabled: boolean) {
      return demo ? changeDemoFolder(id, { enabled }) : watch.setWatchFolderEnabled(id, enabled)
    },
    async setWatchFolderSubfolders(id: number, include: boolean) {
      return demo ? changeDemoFolder(id, { include_subfolders: include }) : watch.setWatchFolderSubfolders(id, include)
    },
    async setAutoAnalysis(enabled: boolean) {
      if (!demo) return watch.setWatchAutoAnalysis(enabled)
      watchState = { ...watchState, auto_analysis_enabled: enabled }
    },
    async setDailyLimit(limit: number) {
      if (!demo) return watch.setWatchDailyLimit(limit)
      watchState = { ...watchState, daily_limit: limit }
      return { daily_limit: limit }
    },
    async listUncertain() {
      if (demo) return (fixture.pending ?? []).filter(row => row.category === 'uncertain'
        && row.manual_reference === null && row.needs_confirmation && !decisions.has(row.id))
      return (await wiringJson<{ items: ReferenceRow[] }>('/api/reference/review?uncertain_only=true')).items
    },
    async decideReference(ids: number[], keep: boolean) {
      if (demo) {
        ids.forEach(id => decisions.add(id))
        return { operation: null, updated: new Set(ids).size }
      }
      return wiringJson<{ operation: string; updated: number }>('/api/reference/decisions',
        jsonBody('POST', { ids, is_reference: keep }))
    },
    async getDailyCounts(days?: number) {
      return demo ? fixture.daily ?? []
        : wiringJson<DayCount[]>(`/api/stats/daily${days === undefined ? '' : `?days=${days}`}`)
    },
    async getPlatformCounts() {
      return demo ? fixture.platformCounts ?? [] : wiringJson<PlatformCount[]>('/api/stats/platforms')
    },
  }
}

export type WiringApi = ReturnType<typeof createWiringApi>

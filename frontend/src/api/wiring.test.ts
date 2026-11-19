import { afterEach, describe, expect, it, vi } from 'vitest'
import { createWiringApi, presentImage, selectLibraryImages } from './wiring'
import type { LibraryImage, WiringApi } from './wiring'
import type { ImageDetail } from '../api'

const image = (id: number, platform: LibraryImage['platform'], hex: string): LibraryImage => ({
  id, platform, post_id: id, position: 0, url: `/images/${id}.jpg`, width: 10, height: 20,
  original_name: `${id}.jpg`, created_at: `2026-11-0${id}T00:00:00Z`,
  colors: [{ rank: 0, hex, ratio: 1 }],
})
const images = [image(1, 'local', '#ff0000'), image(2, 'pinterest', '#0000ff'), image(3, 'local', '#ff1100')]
const detail: ImageDetail = {
  id: 1, url: '/images/1.jpg', width: 10, height: 20, original_name: 'original.jpg', created_at: images[0].created_at,
  posts: [], analysis: null, colors: [], annotation: { description: '', tags: [], edited: true },
}
const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 })
afterEach(() => vi.unstubAllGlobals())

describe('wiring data layer', () => {
  it('sorts actual palette, date and similarity and filters platform without mutating inputs', () => {
    const ids = (rows: LibraryImage[]) => rows.map(row => row.id)
    expect(ids(selectLibraryImages(images))).toEqual([3, 2, 1])
    expect(ids(selectLibraryImages(images, { sort: 'color' }))).toEqual([1, 3, 2])
    expect(ids(selectLibraryImages(images, { platforms: new Set(['local']), color: '#ff0000' }))).toEqual([1, 3])
    expect(ids(selectLibraryImages(images, { sort: 'similar', similarities: new Map([[2, 1], [1, 0.7]]) }))).toEqual([2, 1, 3])
    expect(ids(images)).toEqual([1, 2, 3])
  })

  it('keeps explicit blank annotations and unknown save dates, and uses the original filename', () => {
    expect(presentImage(detail)).toMatchObject({ description: '', subject: [], styleTags: [], tags: [], savedAt: null,
      download: { url: '/images/1.jpg', filename: 'original.jpg' } })
  })

  it('uses the backend response for degraded search', async () => {
    const result = { status: 'ok', degraded: true, items: [] }
    const fetcher = vi.fn().mockResolvedValue(json(result))
    vi.stubGlobal('fetch', fetcher)
    expect(await createWiringApi('real').search('海报')).toEqual(result)
    expect(fetcher).toHaveBeenCalledWith('/api/search', expect.objectContaining({ method: 'POST', body: '{"query":"海报"}' }))
  })

  it.each<[string, (api: WiringApi) => Promise<unknown>, string, string, unknown]>([
    ['图库', api => api.listImages(), '/api/images?include_palette=true', 'GET', []],
    ['颜色查找', api => api.findByColor('#ff0000'), '/api/images?include_palette=true', 'GET', images],
    ['详情', api => api.getDetail(1), '/api/images/1', 'GET', detail],
    ['相似', api => api.findSimilar(1), '/api/images/1/similar', 'GET', { status: 'ok', items: [] }],
    ['标注', api => api.setAnnotation(1, { tags: [] }), '/api/images/1/annotation', 'PUT', { annotation: null }],
    ['Key 状态', api => api.getKeyStatus(), '/api/settings/api-key', 'GET', { configured: false, last4: null }],
    ['冷却', api => api.getCooldown(), '/api/sync/xhs/cooldown', 'GET', { blocked: true }],
    ['监听状态', api => api.getWatchStatus(), '/api/watch-folders', 'GET', { folders: [] }],
    ['添加目录', api => api.addWatchFolder('/demo'), '/api/watch-folders', 'POST', { id: 1 }],
    ['删除目录', api => api.removeWatchFolder(1), '/api/watch-folders/1', 'DELETE', { removed: true }],
    ['目录启停', api => api.setWatchFolderEnabled(1, true), '/api/watch-folders/1', 'PUT', { enabled: true }],
    ['子文件夹', api => api.setWatchFolderSubfolders(1, false), '/api/watch-folders/1', 'PUT', { include_subfolders: false }],
    ['自动分析', api => api.setAutoAnalysis(false), '/api/watch-folders/auto-analysis', 'PUT', { auto_analysis_enabled: false }],
    ['分析上限', api => api.setDailyLimit(10), '/api/watch-folders/settings', 'PUT', { daily_limit: 10 }],
    ['待确认', api => api.listUncertain(), '/api/reference/review?uncertain_only=true', 'GET', { items: [] }],
    ['人工判断', api => api.decideReference([1], false), '/api/reference/decisions', 'POST', { updated: 1 }],
    ['日历日期', api => api.getDailyCounts(7), '/api/stats/daily?days=7', 'GET', []],
    ['日历来源', api => api.getPlatformCounts(), '/api/stats/platforms', 'GET', []],
    ['推荐', api => api.recommend('做海报'), '/api/recommendations/columns', 'POST', { columns: [] }],
    ['项目列表', api => api.listProjects(), '/api/projects', 'GET', { items: [], allowed_purposes: [] }],
    ['项目详情', api => api.getProject(1), '/api/projects/1', 'GET', { id: 1, name: 'P', brief: '',
      created_at: '', pins: [], image_ids: [], images: [], allowed_purposes: [] }],
    ['加入项目', api => api.addToProject(1, 1, ['layout']), '/api/projects/1/pins', 'POST', { id: 1, project_id: 1,
      image_id: 1, purposes: ['layout'], note: '', created_at: '', needs_purpose: false }],
  ])('%s 请求走既有契约', async (_label, run, path, method, data) => {
    const fetcher = vi.fn().mockResolvedValue(json(data))
    vi.stubGlobal('fetch', fetcher)
    await run(createWiringApi('real'))
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher.mock.calls[0][0]).toBe(path)
    expect(fetcher.mock.calls[0][1]?.method ?? 'GET').toBe(method)
  })

  it('reads saved annotations from the backend envelope', async () => {
    const annotation = { description: null, tags: ['海报'], edited: true }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ annotation })))
    expect(await createWiringApi('real').setAnnotation(1, { tags: ['海报'] })).toEqual(annotation)
  })

  it('reads all platform states locally and fails closed if the cooldown route is unavailable', async () => {
    const fetcher = vi.fn((path: string) => path.endsWith('/cooldown')
      ? Promise.reject(new Error()) : Promise.resolve(json({ connection_state: 'connected' })))
    vi.stubGlobal('fetch', fetcher)
    const api = createWiringApi('real')
    expect(await api.getCooldown()).toMatchObject({ blocked: true, manual_hold: true })
    expect(await api.getPlatforms()).toEqual(['xhs', 'pinterest', 'zcool', 'huaban'].map(platform => ({ platform, state: 'connected' })))
    expect(fetcher.mock.calls.slice(1).map(([path]) => path)).toEqual(
      ['xhs', 'pinterest', 'zcool', 'huaban'].map(platform => `/api/sync/${platform}/status`))
    expect(api.sourceUrl({ id: 9, platform: 'xhs', url: 'https://example.invalid', title: null, position: 0 }))
      .toBe('/api/posts/9/source')
  })

  it('keeps the created project if recommendation fails and skips recommendations on an empty brief', async () => {
    const created = { id: 1, name: '海报', brief: '做海报', created_at: '' }
    const fetcher = vi.fn((path: string) => Promise.resolve(path === '/api/projects'
      ? json(created) : new Response(null, { status: 503 })))
    vi.stubGlobal('fetch', fetcher)
    const api = createWiringApi('real')
    expect(await api.createProject({ name: created.name, brief: created.brief }))
      .toEqual({ project: created, recommendations: null, recommendationFailed: true })
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual(['/api/projects', '/api/recommendations/columns'])
    fetcher.mockClear()
    await api.createProject({ name: created.name, brief: '' })
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual(['/api/projects'])
  })

  it('keeps demo reads and writes local and separate between instances', async () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    const api = createWiringApi('demo', { images, details: [detail] })
    expect(await api.listImages()).toHaveLength(3)
    await api.setAnnotation(1, { tags: ['海报'] })
    expect((await api.getDetail(1))?.tags).toEqual(['海报'])
    const { project } = await api.createProject({ name: '海报', brief: '做海报' })
    await api.addToProject(project.id, 1, ['layout'])
    expect((await api.getProject(project.id))?.image_ids).toEqual([1])
    const folder = await api.addWatchFolder('/demo')
    await api.setWatchFolderSubfolders(folder.id, false)
    await api.setAutoAnalysis(true)
    await api.setDailyLimit(42)
    expect(await api.getWatchStatus()).toMatchObject({ folders: [{ include_subfolders: false }],
      auto_analysis_enabled: true, daily_limit: 42 })
    expect((await createWiringApi('demo').listProjects()).items).toEqual([])
    expect(fetcher).not.toHaveBeenCalled()
  })
})

describe('onboarding data layer', () => {
  it('uses the real key and count endpoints and existing multipart import contract', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(json({ configured: true, last4: 'A7B9', restart_required: true }))
      .mockResolvedValueOnce(json({ collections: 0, references: 0 }))
      .mockResolvedValueOnce(json({ post_id: 1, added: [], duplicates: [], failed: [] }))
    vi.stubGlobal('fetch', fetcher)
    const api = createWiringApi('real')
    expect(await api.verifyAndSaveKey('synthetic-test-only')).toMatchObject({ restart_required: true })
    expect(await api.getLibraryCounts()).toEqual({ collections: 0, references: 0 })
    const file = new File(['image'], 'example.jpg', { lastModified: 1234 })
    expect((await api.importLocalBatch([file])).post_id).toBe(1)
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual(
      ['/api/settings/api-key', '/api/stats/library', '/api/images/import'])
    expect(fetcher.mock.calls[0][1].method).toBe('PUT')
    const body = fetcher.mock.calls[2][1].body as FormData
    expect(body.get('modified')).toBe('[1234]')
    expect((body.get('files') as File).name).toBe('example.jpg')
  })

  it('keeps onboarding demo completely offline and never stores a submitted key', async () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    const api = createWiringApi('demo')
    expect(await api.verifyAndSaveKey('synthetic-test-only')).toEqual({ configured: true, last4: 'DEMO' })
    expect(await api.getKeyStatus()).toEqual({ configured: true, last4: 'DEMO' })
    expect(await api.getLibraryCounts()).toEqual({ collections: 697, references: 112 })
    expect(await api.importLocalBatch([])).toEqual({ post_id: null, added: [], duplicates: [], failed: [] })
    expect(fetcher).not.toHaveBeenCalled()
  })
})

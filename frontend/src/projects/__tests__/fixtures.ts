import { vi } from 'vitest'
import type { ImageEntity } from '../../search/contract'
import type { Pin, Project, ProjectApi, ProjectDetail, ProjectImage } from '../contract'
import { mergeProjectImages } from '../images'
import { ProjectStore } from '../store'

export const purposes = ['layout', 'style', 'palette', 'subject']
export const project = (id: number): Project => ({ id, name: `项目 ${id}`, brief: `brief ${id}`, created_at: '2026-11-05' })
export const image = (id: number): ProjectImage => ({ image_id: id, url: `/images/${id}.png`, width: 1080, height: 1440,
  original_name: `图 ${id}`, created_at: '2026-11-05', is_video: false,
  sources: [{ post_id: 5, position: 129, platform: 'xhs', source_id: 'work', canonical_url: 'https://example.org/work', title: '展板', is_video: false }] })
export const pin = (projectId: number, imageId: number, uses = ['layout']): Pin => ({
  id: projectId * 100 + imageId, project_id: projectId, image_id: imageId, purposes: uses, note: '旧备注',
  created_at: '2026-11-05', needs_purpose: uses.length === 0,
})
export const detail = (id: number, pins: Pin[] = []): ProjectDetail => ({ ...project(id), pins,
  image_ids: pins.map(p => p.image_id), images: pins.map(p => image(p.image_id)), allowed_purposes: [...purposes] })

/** Scripted service responses, not a second implementation of backend merge rules. */
export function fakeApi(): ProjectApi {
  return {
    listProjects: vi.fn(async () => ({ items: [project(1), project(2)], allowed_purposes: [...purposes] })),
    createProject: vi.fn(async input => ({ ...project(3), ...input })),
    getProject: vi.fn(async id => detail(id)),
    updateProject: vi.fn(async (id, input) => ({ ...project(id), ...input })),
    listPins: vi.fn(async () => ({ items: [], images: [], allowed_purposes: [...purposes] })),
    pinImage: vi.fn(async () => { throw new Error('unscripted pin') }),
    removePurpose: vi.fn(async () => { throw new Error('unscripted remove') }),
  }
}
export function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (error?: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
export function harness(api = fakeApi()) {
  let entities: Record<number, ImageEntity> = mergeProjectImages({}, [image(70)])
  const listeners = new Set<() => void>()
  const merge = vi.fn((images: readonly ProjectImage[]) => { entities = mergeProjectImages(entities, images); listeners.forEach(fn => fn()) })
  const subscribe = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn) } }
  return { api, store: new ProjectStore(api, merge), entities: () => entities, subscribe, merge }
}

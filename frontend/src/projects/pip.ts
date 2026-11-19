import type { ImageItem } from '../api'
import type { ProjectState } from './store'

export function projectPipIds(state: ProjectState, libraryIds: readonly number[]): number[] {
  if (state.activeProjectId === null) return [...new Set(libraryIds)]
  if (state.detailState.status !== 'ok') return []
  return [...new Set((state.pinsByProject[state.activeProjectId] ?? [])
    .filter(p => p.purposes.length && !p.needs_purpose).map(p => p.image_id))]
}

/** Explicit host action; only reads shared entities. Never opens/rewrites native PiP. */
export function sendProjectPinsToPip(state: ProjectState, imagesById: Readonly<Record<number, ImageItem>>,
  libraryIds: readonly number[], receive: (images: ImageItem[]) => void) {
  const ids = projectPipIds(state, libraryIds)
  const images = ids.map(id => imagesById[id]).filter(Boolean)
  receive(images)
  return { imageIds: images.map(i => i.id), missingIds: ids.filter(id => !imagesById[id]) }
}

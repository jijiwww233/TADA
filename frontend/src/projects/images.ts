import type { ImageEntity } from '../search/contract'
import type { ProjectImage } from './contract'

/** Host passes its EXISTING imagesById here. There is no project-owned image map. */
export function mergeProjectImages(current: Record<number, ImageEntity>, images: readonly ProjectImage[]): Record<number, ImageEntity> {
  const next = { ...current }
  for (const image of images) {
    const old = current[image.image_id]; const first = image.sources[0]
    next[image.image_id] = { ...old, id: image.image_id, url: image.url, width: image.width, height: image.height,
      original_name: image.original_name, created_at: image.created_at, sources: image.sources, is_video: image.is_video,
      post_id: first?.post_id ?? old?.post_id ?? 0, position: first?.position ?? old?.position ?? 0,
      platform: first?.platform === 'xhs' ? 'xhs' : 'local' }
  }
  return next
}

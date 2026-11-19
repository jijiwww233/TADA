import { getImage } from '../api'
import { errorText } from '../api/errors'
import { importImageBatch } from '../localImport/api'
import { isImageName } from '../localImport/collect'
import type { ImageEntity } from '../search/contract'

const BATCH_SIZE = 8

export function isDroppedImage(file: File): boolean {
  return isImageName(file.name) || /\.(gif|bmp)$/i.test(file.name) || file.type.startsWith('image/')
}

export async function loadBoardImage(id: number): Promise<ImageEntity> {
  const { id: imageId, url, width, height, original_name, created_at, posts } = await getImage(id)
  const source = posts[0]
  const platform = source?.platform
  return {
    id: imageId, url, width, height, original_name, created_at,
    post_id: source?.id ?? imageId, position: source?.position ?? 0,
    platform: platform === 'xhs' || platform === 'pinterest' || platform === 'zcool' || platform === 'huaban'
      ? platform : 'local',
    is_video: source?.is_video,
    sources: posts.map(post => ({
      post_id: post.id, platform: post.platform, source_id: null, canonical_url: post.url,
      title: post.title, position: post.position, is_video: post.is_video ?? false,
    })),
  }
}

export async function importBoardFiles(files: File[], active: () => boolean) {
  const ids = new Set<number>()
  let error: string | null = null
  for (let index = 0; index < files.length && active(); index += BATCH_SIZE) {
    try {
      const batch = await importImageBatch(files.slice(index, index + BATCH_SIZE))
      for (const item of batch.added) ids.add(item.id)
      for (const item of batch.duplicates) ids.add(item.existing_image_id)
      if (batch.failed.length) error = '有图片导入失败，请检查格式和大小'
    } catch (cause) {
      error = errorText(cause, '导入失败，请重试')
      break
    }
  }
  return { ids: [...ids], error }
}

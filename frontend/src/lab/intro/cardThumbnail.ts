import { thumbnailSrc } from '../../imageThumbnail'

/** 卡片纹理只用缩略图：长边不超过 1024px（WebGL 纹理在 Edge 上更稳，也更快）。 */
export const CARD_THUMB_MAX = 1024

const imageCache = new Map<string, Promise<HTMLImageElement>>()
const urlCache = new Map<string, Promise<string>>()
const canvasCache = new Map<string, Promise<HTMLCanvasElement>>()

/** 先走后台已有的 600px 预览；没有预览的展示图再在浏览器里缩到 1024。 */
export function cardThumbSource(src: string) {
  return thumbnailSrc(src)
}

export function loadImage(src: string): Promise<HTMLImageElement> {
  const cached = imageCache.get(src)
  if (cached) return cached
  const pending = new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image()
    image.decoding = 'async'
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error(`图片加载失败：${src}`))
    image.src = src
  })
  imageCache.set(src, pending)
  pending.catch(() => imageCache.delete(src))
  return pending
}

function canvasOf(width: number, height: number) {
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(width))
  canvas.height = Math.max(1, Math.round(height))
  return canvas
}

/** 长边不超过 CARD_THUMB_MAX 的缩略图 canvas。 */
export function cardThumbnailCanvas(src: string): Promise<HTMLCanvasElement> {
  const cached = canvasCache.get(src)
  if (cached) return cached
  const pending = loadImage(cardThumbSource(src)).then(image => {
    const long = Math.max(image.naturalWidth || 1, image.naturalHeight || 1)
    const scale = Math.min(1, CARD_THUMB_MAX / long)
    const canvas = canvasOf((image.naturalWidth || 1) * scale, (image.naturalHeight || 1) * scale)
    const context = canvas.getContext('2d')
    if (!context) throw new Error('缩略图生成失败')
    context.imageSmoothingEnabled = true
    context.imageSmoothingQuality = 'high'
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    return canvas
  })
  canvasCache.set(src, pending)
  pending.catch(() => canvasCache.delete(src))
  return pending
}

/**
 * 给普通 <img> / WebGL 纹理用的缩略图 URL。
 * 原图本来就 <= 1024 时直接返回原地址，不重复编码。
 */
export function cardThumbnailUrl(src: string): Promise<string> {
  const cached = urlCache.get(src)
  if (cached) return cached
  const pending = cardThumbnailCanvas(src).then(canvas => {
    const source = cardThumbSource(src)
    // 后台预览已经是 <=600px；原图本身 <=1024 时也不用重复编码。
    return source !== src || Math.max(canvas.width, canvas.height) < CARD_THUMB_MAX
      ? source
      : canvas.toDataURL('image/jpeg', 0.84)
  })
  urlCache.set(src, pending)
  pending.catch(() => urlCache.delete(src))
  return pending
}

/** 把缩略图按 cover 方式裁进固定尺寸的纹理 canvas（WebGL 只上传这个，尺寸永远正确）。 */
export function cardTextureCanvas(src: string, width: number, height: number): Promise<HTMLCanvasElement> {
  return cardThumbnailCanvas(src).then(source => {
    const canvas = canvasOf(width, height)
    const context = canvas.getContext('2d')
    if (!context) throw new Error('纹理生成失败')
    const scale = Math.max(width / source.width, height / source.height)
    const drawW = source.width * scale
    const drawH = source.height * scale
    context.imageSmoothingEnabled = true
    context.imageSmoothingQuality = 'high'
    context.drawImage(source, (width - drawW) / 2, (height - drawH) / 2, drawW, drawH)
    return canvas
  })
}

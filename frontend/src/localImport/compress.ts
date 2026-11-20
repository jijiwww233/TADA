/**
 * 公网演示版上传前先压一遍图。
 *
 * 原因（验收当天实测）：公网地址的链路是「iPad → 服务器 nginx → SSH 反向隧道 → 本机 18080」，
 * 反向隧道实测只有约 60–70KB/s：100KB 2.2 秒、500KB 5.1 秒、1MB 14.9 秒、2MB 31.4 秒。
 * 一张 3MB 的相册照片要传 45 秒以上，10MB 要两分多钟；nginx 默认 60 秒 proxy_read_timeout，
 * 超时就是 504，前端只能显示「网络或服务出错」。所以公网演示版在上传前把长边压到 2400、
 * JPEG 质量 0.86（一般 300–800KB），既传得快，又足够看清排版和文字。
 *
 * 只影响公网演示版（VITE_PUBLIC_DEMO=1）；本机版还是原图入库。
 * 压不动（比如浏览器解不了 HEIC）就原样上传，交给后端的 pillow-heif。
 */

export interface PreparedFile {
  blob: Blob
  name: string
  lastModified: number
}

export const PUBLIC_DEMO = import.meta.env.VITE_PUBLIC_DEMO === '1'

/** 小于这个数不压：本来传得动，压了反而损画质 */
const SKIP_BYTES = 700 * 1024
/** 长边上限；压完仍超过 TARGET_BYTES 再压一档 */
const MAX_SIDE = 2400
const SECOND_SIDE = 1800
const QUALITY = 0.86
const SECOND_QUALITY = 0.72
const TARGET_BYTES = 1.6 * 1024 * 1024

const HEIC_NAME = /\.(heic|heif)$/i

function decode(blob: Blob): Promise<HTMLImageElement | null> {
  return new Promise(resolve => {
    const url = URL.createObjectURL(blob)
    const image = new Image()
    image.onload = () => { URL.revokeObjectURL(url); resolve(image) }
    image.onerror = () => { URL.revokeObjectURL(url); resolve(null) }
    image.src = url
  })
}

function render(image: HTMLImageElement, maxSide: number, quality: number): Promise<Blob | null> {
  const width = image.naturalWidth
  const height = image.naturalHeight
  if (!width || !height) return Promise.resolve(null)
  const scale = Math.min(1, maxSide / Math.max(width, height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(width * scale))
  canvas.height = Math.max(1, Math.round(height * scale))
  const context = canvas.getContext('2d')
  if (!context) return Promise.resolve(null)
  // PNG 透明区域转 JPEG 会变黑，先铺白底
  context.fillStyle = '#fff'
  context.fillRect(0, 0, canvas.width, canvas.height)
  context.drawImage(image, 0, 0, canvas.width, canvas.height)
  return new Promise(resolve => canvas.toBlob(blob => resolve(blob), 'image/jpeg', quality))
}

function jpegName(name: string): string {
  return name.replace(/\.[^./\\]+$/, '') + '.jpg'
}

async function shrink(file: File): Promise<PreparedFile> {
  const original: PreparedFile = { blob: file, name: file.name, lastModified: file.lastModified }
  const heic = HEIC_NAME.test(file.name)
  if (!heic && file.size <= SKIP_BYTES) return original
  const image = await decode(file)
  if (!image) return original
  let blob = await render(image, MAX_SIDE, QUALITY)
  if (!blob) return original
  if (blob.size > TARGET_BYTES) blob = (await render(image, SECOND_SIDE, SECOND_QUALITY)) ?? blob
  if (blob.size >= file.size) return original
  return { blob, name: heic ? jpegName(file.name) : file.name, lastModified: file.lastModified }
}

/** 公网演示版：大图先压小；本机版原样返回。 */
export async function prepareUpload(files: File[]): Promise<PreparedFile[]> {
  if (!PUBLIC_DEMO) return files.map(file => ({ blob: file, name: file.name, lastModified: file.lastModified }))
  return Promise.all(files.map(shrink))
}

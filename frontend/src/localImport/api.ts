import type { ImportResult } from '../api'
import { HttpError, networkError, responseError, uploadTimeoutError } from '../api/errors'
import { prepareUpload } from './compress'

/**
 * 上传一批图。
 *
 * 公网演示版（VITE_PUBLIC_DEMO=1）先压图再传：实测公网链路只有约 60–70KB/s，
 * 3MB 的照片要传 45 秒以上，超过 nginx 的 60 秒等待就是 504，用户只看到「网络或服务出错」。
 * 压到 300–800KB 之后一次 5–13 秒，稳定得多。本机版不压，原图入库。
 *
 * 再失败一次就重传一次：公网反向隧道偶尔会断一下（重传是同一批文件，
 * 服务端按 sha256 去重，不会存两份）。
 */
const UPLOAD_TIMEOUT_MS = 180_000
const RETRY_DELAY_MS = 1200
const RETRYABLE_STATUS = new Set([500, 502, 503, 504])

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

async function postOnce(prepared: Awaited<ReturnType<typeof prepareUpload>>, modified: number[]): Promise<Response> {
  const form = new FormData()
  for (const item of prepared) form.append('files', item.blob, item.name)
  form.append('modified', JSON.stringify(modified))
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS)
  try {
    return await fetch('/api/images/import', { method: 'POST', body: form, signal: controller.signal })
  } catch {
    throw controller.signal.aborted ? uploadTimeoutError() : networkError()
  } finally {
    clearTimeout(timer)
  }
}

function canRetry(error: unknown): boolean {
  return error instanceof HttpError && (error.status === 0 || RETRYABLE_STATUS.has(error.status))
}

export async function importImageBatch(files: File[]): Promise<ImportResult> {
  const prepared = await prepareUpload(files)
  const modified = files.map(file => file.lastModified)
  let lastError: unknown = networkError()
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (attempt > 0) await sleep(RETRY_DELAY_MS)
    let response: Response
    try {
      response = await postOnce(prepared, modified)
    } catch (error) {
      lastError = error
      if (attempt === 0 && canRetry(error)) continue
      throw error
    }
    if (!response.ok) {
      const error = responseError(response)
      if (attempt === 0 && RETRYABLE_STATUS.has(response.status)) {
        lastError = error
        continue
      }
      throw error
    }
    try {
      return await response.json() as ImportResult
    } catch {
      throw new Error('导入返回格式不正确')
    }
  }
  throw lastError
}

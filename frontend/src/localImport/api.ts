import type { ImportResult } from '../api'
import { networkError, responseError } from '../api/errors'

/**
 * Upload one batch to the local backend. Errors stay fixed Chinese strings on purpose:
 * the raw response body can contain paths or provider details the UI should not expose.
 */
export async function importImageBatch(files: File[]): Promise<ImportResult> {
  const form = new FormData()
  for (const file of files) form.append('files', file, file.name)
  form.append('modified', JSON.stringify(files.map(file => file.lastModified)))
  let response: Response
  try {
    response = await fetch('/api/images/import', { method: 'POST', body: form })
  } catch {
    throw networkError()
  }
  if (!response.ok) throw responseError(response)
  try {
    return await response.json() as ImportResult
  } catch {
    throw new Error('导入返回格式不正确')
  }
}

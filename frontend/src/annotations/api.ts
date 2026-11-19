import type { Annotation, AnnotationInput } from './contract'

/**
 * Persist the user's annotation for one image. Failures always surface as a fixed Chinese
 * message; the raw response body is never read or shown.
 */
export async function saveAnnotation(imageId: number, input: AnnotationInput): Promise<Annotation | null> {
  let response: Response
  try {
    response = await fetch(`/api/images/${imageId}/annotation`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    })
  } catch {
    throw new Error('保存失败，请重试')
  }
  if (!response.ok) throw new Error('保存失败，请重试')
  let data: unknown
  try {
    data = await response.json()
  } catch {
    throw new Error('保存失败，请重试')
  }
  if (data === null) return null
  if (typeof data !== 'object' || Array.isArray(data)) throw new Error('保存失败，请重试')
  const record = data as Partial<Annotation>
  return {
    description: typeof record.description === 'string' ? record.description : null,
    tags: Array.isArray(record.tags) ? record.tags.filter((tag): tag is string => typeof tag === 'string') : null,
    edited: Boolean(record.edited),
    ...(typeof record.edited_at === 'string' ? { edited_at: record.edited_at } : {}),
  }
}

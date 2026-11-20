import { wiringJson } from '../api/wiring'

/** 走现有 fetch 封装（/api 由 Vite 代理到本机 8100）。软删除，恢复见 backend/scripts/restore_deleted.py。 */
export type DeletedEnvelope = { id: number; deleted: boolean }

export function deleteImageRequest(id: number) {
  return wiringJson<DeletedEnvelope>(`/api/images/${id}`, { method: 'DELETE' })
}

export function deleteProjectRequest(id: number) {
  return wiringJson<DeletedEnvelope>(`/api/projects/${id}`, { method: 'DELETE' })
}

export function renameProjectRequest(id: number, name: string) {
  return wiringJson<{ id: number; name: string }>(`/api/projects/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

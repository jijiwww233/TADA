import type { ProjectImage } from '../projects/contract'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/projects/' + path, init)
  if (!response.ok) throw new Error('操作失败，请重试')
  return response.json()
}

export const loadProjectVibe = (projectId: number, signal: AbortSignal) =>
  request<{ items: ProjectImage[] }>(projectId + '/vibe', { signal })

export const rejectProjectImage = (projectId: number, imageId: number) =>
  request<{ rejected: boolean }>(projectId + '/vibe/reject/' + imageId, { method: 'POST' })

export const pinVibeImages = (projectId: number, imageIds: number[], purpose: string) =>
  request<{ pinned: number }>(projectId + '/vibe/pins', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_ids: imageIds, purpose }),
  })

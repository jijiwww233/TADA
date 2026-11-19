import { useEffect, useState } from 'react'
import type { ProjectImage } from '../projects/contract'
import { loadProjectVibe, pinVibeImages, rejectProjectImage } from './api'

export function useProjectVibe(projectId: number, brief: string, onPinned: (projectId: number) => void) {
  const [items, setItems] = useState<ProjectImage[]>([])
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    if (brief.trim()) {
      void loadProjectVibe(projectId, controller.signal).then(result => {
        if (!controller.signal.aborted) setItems(result.items)
      }).catch(() => {
        if (!controller.signal.aborted) setItems([])
      })
    }
    return () => controller.abort()
  }, [projectId, brief])

  const act = async (imageId: number, purpose?: string) => {
    if (busy !== null) return
    setBusy(imageId)
    setError('')
    try {
      if (purpose) {
        await pinVibeImages(projectId, [imageId], purpose)
        onPinned(projectId)
      } else {
        await rejectProjectImage(projectId, imageId)
      }
      setItems(previous => previous.filter(item => item.image_id !== imageId))
    } catch {
      setError('操作失败，请重试')
    } finally {
      setBusy(null)
    }
  }

  return { items, busy, error, act }
}

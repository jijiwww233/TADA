import { useEffect, useState } from 'react'
import { getAnalysisStatus, type AnalysisStatus } from '../api'

/**
 * 「分析中」提示用的全局分析状态。后端目前只给整体计数和「此刻正在分析的几张」，
 * 没有每张图自己的状态，所以卡片上只能标出正在分析的那几张；其余还没分析的图只能在详情页里说明。
 * 有图在排队或正在分析时每 6 秒问一次，没有时 30 秒问一次；演示模式不请求。
 */
export function useAnalysisStatus(enabled: boolean) {
  const [status, setStatus] = useState<AnalysisStatus | null>(null)
  useEffect(() => {
    if (!enabled) { setStatus(null); return }
    let live = true
    let timer = 0
    const tick = async () => {
      let next: AnalysisStatus | null = null
      try { next = await getAnalysisStatus() } catch { next = null }
      if (!live) return
      setStatus(next)
      const busy = !!next && (next.processing > 0 || (next.running && next.pending > 0))
      timer = window.setTimeout(() => void tick(), busy ? 6000 : 30000)
    }
    void tick()
    return () => { live = false; window.clearTimeout(timer) }
  }, [enabled])
  return status
}

/** 此刻正在分析的图片 id */
export function analyzingIds(status: AnalysisStatus | null): Set<number> {
  if (!status) return new Set()
  const ids = status.current_image_ids ?? (status.current_image_id != null ? [status.current_image_id] : [])
  return new Set(ids)
}

/** 详情页：这张图还没有分析结果时，用一句话说清楚它处在哪一步 */
export function pendingAnalysisNote(status: AnalysisStatus | null, imageId: number): { label: string; note: string } | null {
  if (!status) return null
  if (analyzingIds(status).has(imageId)) return { label: '分析中', note: '正在读这张图，标题和标签稍后出现。' }
  if (!status.configured) return { label: '未分析', note: '还没填 API Key，填好后会自动分析这张图（设置 → API）。' }
  if (status.running && status.pending > 0) return { label: '排队分析', note: `前面还有约 ${status.pending} 张在排队，分析完会自动补上标题和标签。` }
  return { label: '未分析', note: '分析暂停中。可以在设置 → 分析里开始。' }
}

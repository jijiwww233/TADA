import { useCallback, useEffect, useRef, useState } from 'react'
import type { ImportResult } from '../api'
import { importImageBatch as defaultImportBatch } from './api'
import type { LocalImportFailure, LocalImportProgress, LocalImportStatus } from './contract'

export interface UseLocalImportOptions {
  /** Called after every successful batch so the library can refresh incrementally. */
  onImported?: () => void
  importBatch?: (files: File[]) => Promise<ImportResult>
  batchSize?: number
}

const emptyProgress = (): LocalImportProgress => ({
  total: 0, processed: 0, added: 0, duplicates: 0, failed: [],
})

/**
 * Sequential, cancellable import batches. A failed batch stops the run but keeps everything
 * already imported (progress stays on screen); cancel becomes effective after the current batch.
 */
export function useLocalImport(options: UseLocalImportOptions = {}) {
  const { onImported, importBatch = defaultImportBatch, batchSize = 8 } = options
  const [status, setStatus] = useState<LocalImportStatus>('idle')
  const [progress, setProgress] = useState<LocalImportProgress>(emptyProgress)
  const [error, setError] = useState<string | null>(null)
  const [cancelled, setCancelled] = useState(false)
  const runningRef = useRef(false)
  const cancelRef = useRef(false)
  const runIdRef = useRef(0)

  // Ignore late batch responses after the panel unmounted.
  useEffect(() => () => { runIdRef.current += 1 }, [])

  const start = useCallback(async (incoming: readonly File[]) => {
    if (runningRef.current) return
    const files = incoming.filter(file => file.name.length > 0)
    const runId = ++runIdRef.current
    const active = () => runId === runIdRef.current
    runningRef.current = true
    cancelRef.current = false
    setStatus('importing')
    setError(null)
    setCancelled(false)
    setProgress({ total: files.length, processed: 0, added: 0, duplicates: 0, failed: [] })
    if (files.length === 0) {
      runningRef.current = false
      setStatus('error')
      setError('没有可导入的图片文件')
      return
    }
    const size = Math.max(1, Math.floor(batchSize))
    let processed = 0
    let added = 0
    let duplicates = 0
    const failed: LocalImportFailure[] = []
    const addedIds: number[] = []
    try {
      for (let offset = 0; offset < files.length; offset += size) {
        if (!active() || cancelRef.current) break
        const batch = files.slice(offset, offset + size)
        let result: ImportResult
        try {
          result = await importBatch(batch)
        } catch (err) {
          if (!active()) return
          const reason = err instanceof Error && err.message ? err.message : '导入失败，请重试'
          for (const file of batch) failed.push({ name: file.name, reason })
          processed += batch.length
          setProgress({ total: files.length, processed, added, duplicates, failed: [...failed], addedIds: [...addedIds] })
          setStatus('error')
          setError(reason)
          return
        }
        if (!active()) return
        added += result.added.length
        for (const item of result.added) addedIds.push(item.id)
        duplicates += result.duplicates.length
        for (const item of result.failed) {
          failed.push({ name: item.original_name || '未命名文件', reason: item.reason })
        }
        processed += batch.length
        setProgress({ total: files.length, processed, added, duplicates, failed: [...failed], addedIds: [...addedIds] })
        onImported?.()
      }
      if (!active()) return
      setStatus('done')
    } finally {
      if (active()) runningRef.current = false
    }
  }, [batchSize, importBatch, onImported])

  const cancel = useCallback(() => {
    if (!runningRef.current) return
    cancelRef.current = true
    setCancelled(true)
  }, [])

  const reset = useCallback(() => {
    if (runningRef.current) return
    runIdRef.current += 1
    cancelRef.current = false
    setStatus('idle')
    setProgress(emptyProgress())
    setError(null)
    setCancelled(false)
  }, [])

  return { status, progress, error, cancelled, start, cancel, reset }
}

import type { ImportResult } from '../api'

/** One collected image file plus its display/dedup path (folder relative when available). */
export interface CollectedFile {
  file: File
  path: string
}

export interface LocalImportFailure {
  name: string
  reason: string
}

export interface LocalImportProgress {
  total: number
  processed: number
  added: number
  duplicates: number
  failed: LocalImportFailure[]
  /** 这一轮新收进来的图片 id（「查看刚导入的」用） */
  addedIds?: number[]
}

export type LocalImportStatus = 'idle' | 'importing' | 'done' | 'error'

export interface LocalImportState {
  status: LocalImportStatus
  progress: LocalImportProgress
  error: string | null
  /** True once the user asked to cancel; the in-flight batch still finishes. */
  cancelled: boolean
}

export type ImportBatchApi = (files: File[]) => Promise<ImportResult>

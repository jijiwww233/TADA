import type {
  AnalyzeNowResult, WatchDailyLimitResult, WatchFolder, WatchScanResult, WatchStatus,
} from './contract'

const BASE = '/api/watch-folders'
const FALLBACK_ERROR = '操作失败，请重试'

function messageFrom(body: unknown): string {
  if (body && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim().length > 0) return detail
    if (detail && typeof detail === 'object') {
      const message = (detail as { message?: unknown }).message
      if (typeof message === 'string' && message.trim().length > 0) return message
    }
  }
  return FALLBACK_ERROR
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(BASE + path, init)
  } catch {
    throw new Error(FALLBACK_ERROR)
  }
  let body: unknown = null
  try {
    body = response.status === 204 ? null : await response.json()
  } catch {
    body = null
  }
  if (!response.ok) throw new Error(messageFrom(body))
  return body as T
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export async function getWatchStatus(): Promise<WatchStatus> {
  return request<WatchStatus>('')
}

export async function addWatchFolder(path: string): Promise<WatchFolder> {
  return request<WatchFolder>('', json({ path }))
}

export async function setWatchFolderEnabled(id: number, enabled: boolean): Promise<WatchFolder> {
  return request<WatchFolder>('/' + id, { ...json({ enabled }), method: 'PUT' })
}

export async function setWatchFolderSubfolders(id: number, includeSubfolders: boolean): Promise<WatchFolder> {
  return request<WatchFolder>('/' + id, { ...json({ include_subfolders: includeSubfolders }), method: 'PUT' })
}

export async function setWatchAutoAnalysis(enabled: boolean): Promise<void> {
  await request('/auto-analysis', { ...json({ enabled }), method: 'PUT' })
}

export async function removeWatchFolder(id: number): Promise<{ removed: true }> {
  return request<{ removed: true }>('/' + id, { method: 'DELETE' })
}

export async function setWatchDailyLimit(dailyLimit: number): Promise<WatchDailyLimitResult> {
  return request<WatchDailyLimitResult>('/settings', { ...json({ daily_limit: dailyLimit }), method: 'PUT' })
}

export async function analyzeNow(): Promise<AnalyzeNowResult> {
  return request<AnalyzeNowResult>('/analyze-now', { method: 'POST' })
}

export async function scanWatchNow(): Promise<WatchScanResult> {
  return request<WatchScanResult>('/scan-now', { method: 'POST' })
}

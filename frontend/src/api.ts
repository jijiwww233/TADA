import type { Annotation } from './annotations/contract'
import { responseError } from './api/errors'

export interface ImageItem {
  id: number
  post_id: number
  position: number
  url: string
  width: number | null
  height: number | null
  original_name: string | null
  created_at: string
  platform: 'local' | 'xhs' | 'pinterest' | 'zcool' | 'huaban'
  image_count?: number
  is_video?: boolean
  /** 生效短标题（人工优先，其次分析生成，12 字以内）；还没有时为 null，旧后端没有这个字段 */
  title?: string | null
}

export interface ImportResult {
  post_id: number | null
  added: { id: number; original_name: string; sha256: string; url: string }[]
  duplicates: { original_name: string; sha256: string; existing_image_id: number }[]
  failed: { original_name: string; reason: string }[]
}

export async function listImages(): Promise<ImageItem[]> {
  const resp = await fetch('/api/images')
  if (!resp.ok) throw responseError(resp)
  return resp.json()
}

export async function importImages(files: File[]): Promise<ImportResult> {
  const form = new FormData()
  for (const f of files) form.append('files', f, f.name)
  const resp = await fetch('/api/images/import', { method: 'POST', body: form })
  if (!resp.ok) throw responseError(resp)
  return resp.json()
}

export interface Layout {
  title_position: string[]
  columns: string[]
  whitespace: string[]
  image_text_ratio: string[]
  density: string[]
  layout_note: string
}

export interface Analysis {
  status: 'pending' | 'done' | 'failed'
  processing: boolean
  model: string | null
  visible_text: string | null
  subject: string[] | null
  layout: Layout | null
  style: { tags: string[]; medium: string; style_note: string } | null
  description: string | null
  prompt_tokens: number | null
  completion_tokens: number | null
  attempts: number
  error: string | null
  updated_at: string
}

export interface ColorSwatch {
  rank: number
  hex: string
  ratio: number
}

export interface ImageDetail {
  id: number
  url: string
  width: number | null
  height: number | null
  original_name: string | null
  created_at: string
  posts: { id: number; platform: string; url: string | null; title: string | null; position: number;
    sync_error?: string | null; connection_state?: string; connection_label?: string; image_count?: number; is_video?: boolean; fav_state?: string | null; source_state?: string | null;
    images?: {position:number;image_id:number|null;url:string|null;download_state:string}[] }[]
  analysis: Analysis | null
  colors: ColorSwatch[]
  annotation?: Annotation | null
  /** 生效短标题，同 ImageItem.title */
  title?: string | null
}

export interface AnalysisStatus {
  total: number
  pending: number
  processing: number
  done: number
  failed: number
  prompt_tokens: number
  completion_tokens: number
  running: boolean
  current_image_id: number | null
  /** 后端 worker.state() 一起返回：此刻正在分析的几张（并发时不止一张） */
  current_image_ids?: number[]
  last_error: string | null
  configured: boolean
  model: string | null
}

async function json<T>(resp: Response, _what: string): Promise<T> {
  if (!resp.ok) throw responseError(resp)
  return resp.json()
}

export async function getImage(id: number): Promise<ImageDetail> {
  return json(await fetch(`/api/images/${id}`), '读取图片详情')
}

export async function getAnalysisStatus(): Promise<AnalysisStatus> {
  return json(await fetch('/api/analysis/status'), '读取分析状态')
}

export async function setAnalysisRunning(run: boolean): Promise<AnalysisStatus> {
  return json(await fetch(`/api/analysis/${run ? 'start' : 'pause'}`, { method: 'POST' }), '切换分析')
}

export async function retryAnalysis(imageId?: number): Promise<{ requeued: number }> {
  const path = imageId === undefined ? '/api/analysis/retry-failed' : `/api/analysis/${imageId}/retry`
  return json(await fetch(path, { method: 'POST' }), '重试分析')
}

export interface SyncRun {
  id: number
  status: 'running' | 'completed' | 'partial' | 'paused' | 'failed'
  mode: string | null
  pause_reason: string | null
  error: string | null
  pages_done: number
  items_found: number
  items_new: number
  items_failed: number
  images_ok: number
  images_failed: number
  images_new: number | null
  started_at: string
  finished_at: string | null
}

export interface SyncStatus {
  cooldown?: { blocked: boolean; auto_blocked: boolean; manual_hold?: boolean; message?: string | null }
  busy: boolean
  connection_state?: 'connected'|'needs_login'|'needs_verification'|'blocked'
  connection_label?: string
  verification_waiting?: boolean
  action: 'sync' | 'login' | 'verify' | null
  message: string | null
  account_known: boolean
  last_run: SyncRun | null
  failures: { work_key: string; stage: string; error: string | null }[]
  auto?: { enabled: boolean; running: boolean; next_check_at: number | null; last_trigger_at?: number | null; needs_login: boolean }
}

export type SyncPlatform = 'xhs' | 'huaban' | 'pinterest' | 'zcool'

export interface AutoState {
  enabled: boolean
  running: boolean
  next_check_at: number | null
  last_trigger_at: number | null
  needs_login: boolean
  last_error: string | null
}

export interface AutoSettings {
  auto_sync: boolean
  /** Only the entry-launched formal instance may enable auto sync. */
  auto_sync_allowed?: boolean
  /** The shared cooldown lock is active/unreadable, so auto sync stays off. */
  auto_sync_lock_blocked?: boolean
  auto_platforms: Record<SyncPlatform, boolean>
  xhs: AutoState
  huaban: AutoState
  pinterest: AutoState
  zcool: AutoState
}

export async function getAutoSettings(): Promise<AutoSettings> {
  return json(await fetch('/api/settings'), '读取自动同步设置')
}

export async function setAutoSync(enabled: boolean): Promise<AutoSettings> {
  return json(await fetch('/api/settings/auto-sync?enabled=' + (enabled ? '1' : '0'), { method: 'POST' }), '保存自动同步设置')
}

export async function setPlatformAuto(platform: SyncPlatform, enabled: boolean): Promise<AutoSettings> {
  return json(await fetch(`/api/settings/auto-sync/${platform}?enabled=` + (enabled ? '1' : '0'), { method: 'POST' }), '保存平台自动检查设置')
}

export async function getSyncStatus(platform: SyncPlatform = 'xhs'): Promise<SyncStatus> {
  return json(await fetch(`/api/sync/${platform}/status`), '读取同步状态')
}

export async function startZcoolBackfill(): Promise<SyncStatus> {
  return json(await fetch('/api/sync/zcool/backfill', { method: 'POST' }), '开始回填站酷历史收藏')
}

export async function startSync(platform: SyncPlatform, action: 'start' | 'login'): Promise<SyncStatus> {
  return json(await fetch(`/api/sync/${platform}/${action}`, { method: 'POST' }), action === 'start' ? '开始同步' : '打开登录')
}

export async function verificationAction(platform: SyncPlatform, complete = false): Promise<SyncStatus> {
  return json(await fetch(`/api/sync/${platform}/${complete ? 'verification-complete' : 'verify'}`,{method:'POST'}),'手动验证')
}

export interface ImportFailure {
  /** 1-based position of the input link; failures never echo the raw share URL. */
  input_index?: number
  /** Canonical link without query/fragment (the share token is never returned). */
  link?: string | null
  /** Backend alias of `link`; also sanitized, kept for older payloads. */
  url?: string | null
  note_id?: string | null
  error: string
}

export interface ImportOutcome {
  mode: 'captured' | 'share'
  created: number
  skipped: number
  total: number
  failed: ImportFailure[]
  at?: string
}

export interface ImportStatus {
  blocked: boolean
  manual_hold?: boolean
  message: string | null
  last: { captured: ImportOutcome | null; share: ImportOutcome | null }
}

async function importError(resp: Response): Promise<Error> {
  if (resp.status === 403 || resp.status === 413) return responseError(resp)
  try {
    const data: unknown = await resp.json()
    const detail = (data as { detail?: unknown })?.detail
    if (typeof detail === 'string') return new Error(detail)
    if (detail && typeof (detail as { message?: unknown }).message === 'string') {
      return new Error((detail as { message: string }).message)
    }
  } catch {
    // fall through to the status code
  }
  return responseError(resp)
}

export async function getImportStatus(): Promise<ImportStatus> {
  return json(await fetch('/api/import/status'), '读取导入状态')
}

export async function importCaptured(payload: unknown): Promise<ImportOutcome> {
  const body = Array.isArray(payload) ? { notes: payload } : payload
  const resp = await fetch('/api/import/captured', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw await importError(resp)
  return resp.json()
}

export async function importShare(urls: string[]): Promise<ImportOutcome> {
  const resp = await fetch('/api/import/share', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ urls }),
  })
  if (!resp.ok) throw await importError(resp)
  return resp.json()
}

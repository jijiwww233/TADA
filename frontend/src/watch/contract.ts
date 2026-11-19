export interface WatchFolder {
  id: number
  path: string
  enabled: boolean
  include_subfolders: boolean
  last_import_at: string | null
  imported_count: number
  last_error: string | null
  pending_files: number
}

export interface WatchStatus {
  allowed: boolean
  watching: boolean
  auto_analysis_enabled: boolean
  analysis_configured: boolean
  folders: WatchFolder[]
  daily_limit: number
  analyzed_today: number
  deferred: number
  estimated_cost_today: number
  estimated_cost_at_limit: number
  prices: { input_per_million: number; output_per_million: number }
}

export interface WatchScanResult {
  folders: number
  imported: number
  duplicates: number
  failed: number
}

export interface AnalyzeNowResult {
  queued: number
  started: boolean
  deferred: number
}

export interface WatchDailyLimitResult {
  daily_limit: number
}

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  addWatchFolder, analyzeNow, getWatchStatus, removeWatchFolder, scanWatchNow,
  setWatchAutoAnalysis, setWatchDailyLimit, setWatchFolderEnabled, setWatchFolderSubfolders,
} from './api'
import type { WatchFolder, WatchScanResult, WatchStatus } from './contract'

const MAX_DAILY_LIMIT = 10000

const errorMessage = (error: unknown) =>
  error instanceof Error && error.message ? error.message : '操作失败，请重试'

function formatTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

/** Auto-import folder settings; it always renders (even while loading or after a failure). */
export default function WatchFolderPanel({ onImported }: { onImported?: () => void }) {
  const [status, setStatus] = useState<WatchStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [pathInput, setPathInput] = useState('')
  const [limitDraft, setLimitDraft] = useState<string | null>(null)
  const [scanResult, setScanResult] = useState<WatchScanResult | null>(null)
  const [busy, setBusy] = useState(false)
  const counts = useRef<Map<number, number> | null>(null)

  const load = useCallback(async () => {
    try {
      const next = await getWatchStatus()
      const previous = counts.current
      counts.current = new Map(next.folders.map(folder => [folder.id, folder.imported_count]))
      setStatus(next)
      if (previous && next.folders.some(folder => folder.imported_count > (previous.get(folder.id) ?? 0))) {
        onImported?.()
      }
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [onImported])

  useEffect(() => {
    void load()
    const timer = setInterval(() => { void load() }, 5000)
    return () => clearInterval(timer)
  }, [load])

  const run = async (action: () => Promise<void>) => {
    if (busy) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await action()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const addFolder = () => {
    const path = pathInput.trim()
    if (!path) return
    void run(async () => {
      await addWatchFolder(path)
      setPathInput('')
      await load()
    })
  }

  const toggleFolder = (folder: WatchFolder) => {
    void run(async () => {
      await setWatchFolderEnabled(folder.id, !folder.enabled)
      await load()
    })
  }

  const removeFolder = (id: number) => {
    void run(async () => {
      await removeWatchFolder(id)
      await load()
    })
  }

  const saveLimit = () => {
    const value = Number(limitInput)
    void run(async () => {
      await setWatchDailyLimit(value)
      await load()
      setLimitDraft(null)
      setNotice('每日上限已保存')
    })
  }

  const runAnalyzeNow = () => {
    void run(async () => {
      const result = await analyzeNow()
      setNotice(result.started ? '已排入分析队列' : '已排入分析队列，等待 AI 分析服务可用')
      await load()
    })
  }

  const runScanNow = () => {
    void run(async () => {
      setScanResult(await scanWatchNow())
      await load()
    })
  }

  const limitInput = limitDraft ?? (status ? String(status.daily_limit) : '')
  const limitValue = Number(limitInput)
  const limitValid = /^\d+$/.test(limitInput.trim())
    && limitValue >= 0 && limitValue <= MAX_DAILY_LIMIT

  return (
    <div className="analysis-bar" aria-label="自动导入文件夹">
      <strong>自动导入文件夹</strong>
      {status && (
        status.watching ? (
          <span className="muted">自动监听已开启：文件夹里出现新图片会自动导入。</span>
        ) : (
          <span className="muted">本实例不会自动监听（只有入口启动的正式实例会自动导入）。可添加文件夹后点「立即扫描」手动导入。</span>
        )
      )}
      <span className="muted">包含子文件夹默认开启。首次扫描会导入扫描范围内尚未入库的图片。</span>
      {busy && <span role="status">处理中…</span>}
      {error && <span className="error" role="alert">{error}</span>}
      {notice && <span role="status" className="muted">{notice}</span>}

      <label>
        路径
        <input
          aria-label="文件夹路径"
          value={pathInput}
          disabled={busy}
          placeholder="D:\Pictures\小红书同步"
          onChange={(event) => setPathInput(event.target.value)}
        />
      </label>
      <button type="button" onClick={addFolder} disabled={!pathInput.trim() || busy}>添加文件夹</button>

      {!status && !error && <span className="muted">加载中…</span>}
      {status && (
        <>
          {status.folders.length === 0 ? (
            <span className="muted">还没有添加文件夹。把同步工具保存图片的文件夹加进来即可。</span>
          ) : (
            status.folders.map(folder => (
              <div key={folder.id} style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                <span>{folder.path}</span>
                <span className="muted">{!folder.enabled ? '已暂停' : status.watching ? '监听中' : '等待手动扫描'}</span>
                <span className="muted">最近导入：{formatTime(folder.last_import_at)}</span>
                <span className="muted">累计导入 {folder.imported_count} 张</span>
                <label>
                  <input
                    type="checkbox"
                    checked={folder.include_subfolders}
                    disabled={busy}
                    onChange={event => {
                      const include = event.currentTarget.checked
                      void run(async () => {
                        await setWatchFolderSubfolders(folder.id, include)
                        await load()
                      })
                    }}
                  />
                  包含子文件夹
                </label>
                {folder.pending_files > 0 && <span className="muted">等待文件写完 {folder.pending_files} 张</span>}
                {folder.last_error && <span className="error">{folder.last_error}</span>}
                <button type="button" onClick={() => toggleFolder(folder)} disabled={busy}>
                  {folder.enabled ? '暂停' : '继续'}
                </button>
                <button type="button" onClick={() => removeFolder(folder.id)} disabled={busy}>移除</button>
              </div>
            ))
          )}

          <span>自动分析：{status.auto_analysis_enabled ? '已开启' : '已暂停'}</span>
          {status.allowed && (
            <button
              type="button"
              disabled={busy || (!status.auto_analysis_enabled && !status.analysis_configured)}
              onClick={() => void run(async () => {
                await setWatchAutoAnalysis(!status.auto_analysis_enabled)
                await load()
              })}
            >
              {status.auto_analysis_enabled ? '暂停自动分析' : '开启自动分析'}
            </button>
          )}
          {status.allowed && !status.analysis_configured && <span className="muted">请先配置 AI 分析服务。</span>}
          <label>
            每日自动分析上限
            <input
              type="number"
              min={0}
              max={MAX_DAILY_LIMIT}
              step={1}
              value={limitInput}
              disabled={busy}
              onChange={(event) => setLimitDraft(event.target.value)}
            />
          </label>
          <button type="button" onClick={saveLimit} disabled={!limitValid || busy}>保存上限</button>

          <span>
            今天已自动分析 {status.analyzed_today} 张 · 预计花费约 ¥{status.estimated_cost_today.toFixed(4)}
          </span>
          {status.estimated_cost_at_limit > 0 && (
            <span className="muted">按上限 {status.daily_limit} 张约 ¥{status.estimated_cost_at_limit.toFixed(4)}</span>
          )}
          {status.deferred > 0 && (
            <span className="muted">待自动分析 {status.deferred} 张（超过上限顺延，也可手动分析）</span>
          )}

          <button type="button" onClick={runAnalyzeNow} disabled={status.deferred <= 0 || busy}>
            现在分析（{status.deferred} 张）
          </button>
          <button type="button" onClick={runScanNow} disabled={busy}>立即扫描一次</button>
          {scanResult && (
            <span className="muted">
              扫描完成：新增 {scanResult.imported}，重复 {scanResult.duplicates}，失败 {scanResult.failed}
            </span>
          )}
        </>
      )}
    </div>
  )
}

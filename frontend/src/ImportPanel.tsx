import { useCallback, useEffect, useState } from 'react'
import {
  getImportStatus,
  importCaptured,
  importShare,
  type ImportOutcome,
  type ImportStatus,
} from './api'
import { extractUrls } from './importLinks'

const MAX_BATCH = 20

function when(at?: string): string {
  return at ? at.replace('T', ' ').slice(0, 19) : ''
}

function ResultLine({ outcome }: { outcome: ImportOutcome }) {
  const at = when(outcome.at)
  return (
    <div className="msg">
      <p>
        新增 {outcome.created} 条 · 重复跳过 {outcome.skipped} 条 · 失败 {outcome.failed.length} 条
        {at && <span className="muted"> · {at}</span>}
      </p>
      {outcome.failed.length > 0 && (
        <ul className="error">
          {outcome.failed.map((f, i) => (
            <li key={i}>
              {(f.link ?? f.url ?? f.note_id ?? (f.input_index ? '第 ' + f.input_index + ' 条' : '未知条目')) + '：' + f.error}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function LastLine({ label, outcome }: { label: string; outcome: ImportOutcome | null }) {
  if (!outcome) return null
  const at = when(outcome.at)
  return (
    <p className="msg muted">
      上次{label}：新增 {outcome.created} 条 · 重复跳过 {outcome.skipped} 条
      {at && ` · ${at}`}
    </p>
  )
}

export default function ImportPanel({ onImported }: { onImported: () => void }) {
  const [status, setStatus] = useState<ImportStatus | null>(null)
  const [shareText, setShareText] = useState('')
  const [captureText, setCaptureText] = useState('')
  const [busy, setBusy] = useState<'share' | 'captured' | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [shareResult, setShareResult] = useState<ImportOutcome | null>(null)
  const [captureResult, setCaptureResult] = useState<ImportOutcome | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // The status read is advisory (cooldown flag + last outcome). If it fails, keep the
  // entries usable and let the import action itself surface a real error.
  const load = useCallback(async () => {
    try {
      setStatus(await getImportStatus())
    } catch {
      setStatus(null)
    }
  }, [])

  // Initial read plus a slow poll: the cooldown may be lifted from another tab, and the
  // local endpoint is cheap to poll.
  useEffect(() => {
    const first = setTimeout(() => {
      void load()
    }, 0)
    const timer = setInterval(() => {
      void load()
    }, 15000)
    return () => {
      clearTimeout(first)
      clearInterval(timer)
    }
  }, [load])

  useEffect(() => {
    if (!busy) return
    const timer = setInterval(() => setElapsed((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [busy])

  const cooling = Boolean(status?.blocked)
  const disabled = cooling || busy !== null

  async function doShare() {
    if (disabled) return
    const urls = extractUrls(shareText)
    if (urls.length === 0) {
      setError('没有识别到小红书链接，请粘贴分享链接或分享口令')
      return
    }
    const batch = urls.slice(0, MAX_BATCH)
    setError(null)
    setNotice(urls.length > MAX_BATCH ? `每批最多 ${MAX_BATCH} 条，本次先导入前 ${MAX_BATCH} 条` : null)
    setBusy('share')
    setElapsed(0)
    try {
      setShareResult(await importShare(batch))
      onImported()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function doCaptured() {
    if (disabled) return
    let payload: unknown
    try {
      payload = JSON.parse(captureText)
    } catch {
      setError('粘贴的内容不是有效 JSON')
      return
    }
    const notes = Array.isArray(payload) ? payload : (payload as { notes?: unknown[] })?.notes
    if (!Array.isArray(notes) || notes.length === 0) {
      setError('JSON 里没有 notes 列表（或列表为空）')
      return
    }
    setError(null)
    setNotice(null)
    setBusy('captured')
    setElapsed(0)
    try {
      setCaptureResult(await importCaptured(payload))
      onImported()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="import-panel">
      <h2>小红书导入</h2>
      {/* Static review item 7: an import only stores metadata; saying so avoids demoing
          "paste a link, then find the image" as if the image were already in the library. */}
      <p className="muted">
        两个入口都只收录笔记信息和图片链接，不下载图片；刚导入的笔记暂时不会出现在图库或三栏推荐里。
      </p>
      {cooling && (
        <p className="msg error" role="status">
          平台冷却中：为避免任何小红书请求，两个导入入口暂时禁用；冷却解除后自动恢复。
        </p>
      )}
      {notice && <p className="msg muted">{notice}</p>}
      <div className="import-entries">
        <div className="import-entry">
          <h3>分享链接导入</h3>
          <p className="muted">
            粘贴分享链接或分享口令，每批最多 {MAX_BATCH} 条；逐条访问，随机间隔 0.8–2 秒。
          </p>
          <textarea
            aria-label="分享链接"
            rows={3}
            value={shareText}
            disabled={disabled}
            onChange={(e) => setShareText(e.target.value)}
            placeholder="https://www.xiaohongshu.com/explore/… 或 http://xhslink.com/…"
          />
          <button type="button" onClick={doShare} disabled={disabled}>
            {cooling ? '冷却中' : busy === 'share' ? '导入中…' : '导入分享链接'}
          </button>
          {busy === 'share' && (
            <span role="status">已等待 {elapsed} 秒（单条约 1–2 秒，请勿关闭页面）</span>
          )}
          {shareResult && <ResultLine outcome={shareResult} />}
        </div>
        <div className="import-entry">
          <h3>浏览器采集导入</h3>
          <p className="muted">
            在收藏页用浏览器扩展采集后直接入库；也可以把扩展生成的 JSON 粘到下面手动导入。只保存页面已有的数据，不下载图片。
          </p>
          <textarea
            aria-label="采集数据 JSON"
            rows={3}
            value={captureText}
            disabled={disabled}
            onChange={(e) => setCaptureText(e.target.value)}
            placeholder={'{"notes":[{"note_id":"…","title":"…","images":[{"url":"…"}]}]}'}
          />
          <button type="button" onClick={doCaptured} disabled={disabled}>
            {cooling ? '冷却中' : busy === 'captured' ? '导入中…' : '导入采集数据'}
          </button>
          {captureResult && <ResultLine outcome={captureResult} />}
        </div>
      </div>
      {error && (
        <p className="msg error" role="alert">
          {error}
        </p>
      )}
      {!shareResult && <LastLine label="分享链接导入" outcome={status?.last.share ?? null} />}
      {!captureResult && <LastLine label="采集导入" outcome={status?.last.captured ?? null} />}
    </section>
  )
}

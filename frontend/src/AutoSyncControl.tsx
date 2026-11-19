import { useCallback, useEffect, useState } from 'react'
import { getAutoSettings, setAutoSync, setPlatformAuto, type AutoSettings, type SyncPlatform } from './api'

const PLATFORMS: { key: SyncPlatform; label: string }[] = [
  { key: 'xhs', label: '小红书' },
  { key: 'huaban', label: '花瓣' },
  { key: 'pinterest', label: 'Pinterest' },
  { key: 'zcool', label: '站酷' },
]

function fmt(ts: number | null) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleTimeString()
}

export default function AutoSyncControl() {
  const [s, setS] = useState<AutoSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try { setS(await getAutoSettings()); setError(null) }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }, [])
  useEffect(() => { void load(); const t = setInterval(load, 5000); return () => clearInterval(t) }, [load])
  if (!s) return error ? <p className="error">{error}</p> : null
  const a = s.xhs
  const entryOnly = s.auto_sync_allowed === false
  return <div className="analysis-bar" aria-label="自动同步">
    <label><input type="checkbox" checked={s.auto_sync} disabled={entryOnly} onChange={async e => {
      try { setS(await setAutoSync(e.target.checked)) } catch (err) { setError(String(err)) }
    }} />自动同步</label>
    <span className="muted">当前：{s.auto_sync ? '开' : '关'} · 上次触发：{fmt(a.last_trigger_at)} · 下次检查：{fmt(a.next_check_at)}</span>
    {entryOnly && <span className="muted">只有从入口启动的正式实例可以自动同步</span>}
    {s.auto_sync_lock_blocked && <span className="error">冷却锁定中，自动同步已暂停</span>}
    {PLATFORMS.map(({ key, label }) => (
      <label key={key} className="muted">
        <input type="checkbox" disabled={!s.auto_sync}
          checked={s.auto_platforms?.[key] ?? true}
          onChange={async e => {
            try { setS(await setPlatformAuto(key, e.target.checked)) } catch (err) { setError(String(err)) }
          }} />
        {label}自动检查
      </label>
    ))}
    {a.needs_login && <span className="error">需要重新登录</span>}
    {a.last_error && <span className="error">{a.last_error}</span>}
    {error && <span className="error">{error}</span>}
  </div>
}

import { useEffect, useRef, useState } from 'react'
import { getAutoSettings, getSyncStatus, setAutoSync, setPlatformAuto, startSync, verificationAction,
  type AutoSettings, type SyncPlatform, type SyncStatus } from '../api'

const platforms: { key: SyncPlatform; name: string }[] = [
  { key: 'xhs', name: '小红书' }, { key: 'pinterest', name: 'Pinterest' },
  { key: 'zcool', name: '站酷' }, { key: 'huaban', name: '花瓣' },
]

export function PlatformSync({ onSynced }: { onSynced: () => void }) {
  const [statuses, setStatuses] = useState<Partial<Record<SyncPlatform, SyncStatus>>>({})
  const [auto, setAuto] = useState<AutoSettings>()
  const [pending, setPending] = useState('')
  const [error, setError] = useState('')
  const onSyncedRef = useRef(onSynced)
  onSyncedRef.current = onSynced
  const previousRuns = useRef(new Map<SyncPlatform, SyncStatus['last_run']>())
  const requestVersion = useRef(0)

  useEffect(() => {
    let cancelled = false
    let timer: number
    const refresh = async () => {
      const version = requestVersion.current
      const [settings, ...results] = await Promise.all([
        getAutoSettings().catch(() => undefined),
        ...platforms.map(({ key }) => getSyncStatus(key).catch(() => undefined)),
      ])
      if (cancelled) return
      if (version === requestVersion.current) {
        setAuto(settings)
        const next: Partial<Record<SyncPlatform, SyncStatus>> = {}
        platforms.forEach(({ key }, index) => {
          const status = results[index]
          if (!status) return
          next[key] = status
          const current = status.last_run
          const previous = previousRuns.current.get(key)
          if (current && previousRuns.current.has(key) &&
            (previous?.id !== current.id || previous.images_new !== current.images_new)) onSyncedRef.current()
          previousRuns.current.set(key, current)
        })
        setStatuses(next)
      }
      timer = window.setTimeout(() => void refresh(), 2500)
    }
    void refresh()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [])

  const act = async (key: string, action: () => Promise<void>) => {
    if (pending) return
    requestVersion.current += 1
    setPending(key)
    setError('')
    try { await action() }
    catch (err) { setError(err instanceof Error ? err.message : '操作失败，请重试') }
    finally { requestVersion.current += 1; setPending('') }
  }
  const update = async (key: SyncPlatform, action: Promise<SyncStatus>) => {
    const status = await action
    setStatuses(current => ({ ...current, [key]: status }))
  }

  return <>
    <label className="m-set-row">
      <span>自动同步已连接的平台</span>
      <input type="checkbox" checked={auto?.auto_sync ?? false}
        disabled={!auto || Boolean(pending) || auto.auto_sync_allowed === false || auto.auto_sync_lock_blocked}
        onChange={event => {
          const enabled = event.currentTarget.checked
          void act('auto', async () => setAuto(await setAutoSync(enabled)))
        }} />
    </label>
    {auto?.auto_sync_allowed === false && <p className="ds-help">此实例未开启定时同步。启动本机版前设置 <code>WOMINMIN_ALLOW_AUTO_SYNC=1</code>，然后重新启动本机后端。</p>}
    {auto?.auto_sync_lock_blocked && <p className="ds-help">平台处于冷却期，定时同步已暂停。</p>}
    {platforms.map(({ key, name }) => {
      const status = statuses[key]
      const cooling = Boolean(status?.cooldown?.blocked)
      const connected = status?.connection_state === 'connected'
      const verify = key === 'huaban' || key === 'zcool'
      const needsVerification = status?.connection_state === 'needs_verification' || status?.connection_state === 'blocked'
      const automatic = auto?.[key]
      const last = status?.last_run
      return <div className="m-set-row m-platform-row" key={key}>
        <div>
          <span>{name}</span>
          <small className="ds-help">{cooling ? '冷却中' : status?.action === 'sync' ? '同步中'
            : status?.action === 'login' ? '请在这台电脑弹出的窗口完成登录'
            : status?.action === 'verify' ? '请在弹出的窗口完成验证'
            : status?.connection_label ?? '暂未读取到连接状态'}</small>
          {!status?.busy && last && <small className="ds-help">{last.status === 'failed' ? '上次同步失败'
            : last.status === 'paused' ? '上次同步已暂停' : `上次新增 ${last.images_new ?? 0} 张`}</small>}
          {automatic?.running && automatic.next_check_at && <small className="ds-help">
            下次检查 {new Date(automatic.next_check_at * 1000).toLocaleTimeString()}
          </small>}
        </div>
        <div className="m-platform-actions">
          {auto?.auto_sync && <label><input type="checkbox" checked={auto.auto_platforms[key] ?? true}
            disabled={Boolean(pending)} onChange={event => {
              const enabled = event.currentTarget.checked
              void act(key, async () => setAuto(await setPlatformAuto(key, enabled)))
            }} />自动检查<span className="sr-only">{name}</span></label>}
          {verify && status?.action === 'verify' && <button className="ds-btn ds-btn--secondary" disabled={Boolean(pending)}
            onClick={() => void act(key, async () => { await verificationAction(key, true) })}>我已完成验证</button>}
          {status && !status.busy && !cooling && (verify && needsVerification
            ? <button className="ds-btn ds-btn--secondary" disabled={Boolean(pending)}
              onClick={() => void act(key, () => update(key, verificationAction(key)))}>打开验证窗口</button>
            : <button className="ds-btn ds-btn--secondary" disabled={Boolean(pending)}
              aria-label={connected ? `同步${name}收藏` : `连接${name}`}
              onClick={() => void act(key, () => update(key, startSync(key, connected ? 'start' : 'login')))}>
              {connected ? '同步收藏' : '连接'}
            </button>)}
          {status && !status.busy && !cooling && !needsVerification && connected &&
            <button type="button" className="ds-btn ds-btn--ghost" aria-label={`重新登录${name}`} disabled={Boolean(pending)}
              onClick={() => void act(key, () => update(key, startSync(key, 'login')))}>重新登录</button>}
        </div>
      </div>
    })}
    {error && <p role="alert" className="ds-help">{error}</p>}
  </>
}

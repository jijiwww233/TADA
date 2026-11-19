import { useCallback, useEffect, useRef, useState } from 'react'
import { getSyncStatus, startSync, startZcoolBackfill, verificationAction, type SyncStatus, type SyncPlatform } from './api'

const REASONS: Record<string, string> = {
  needs_login: '需要登录小红书',
  blocked: '小红书要求安全验证，请稍后再试',
  network: '网络或页面加载异常（已重试），请稍后再试',
  rate_limited: '请求过于频繁，请过几分钟再试',
  page_limit: '本次已读到上限，再点一次继续',
  limit: '本次已读到 1000 条上限，再点一次继续',
  stale_process: '上次同步被中断，再点一次继续',
}
const STAGES: Record<string, string> = {
  list: '列表',
  detail: '详情',
  image_download: '图片下载',
  store: '入库',
  contract: '格式',
}

const PLATFORM_LABEL: Record<SyncPlatform, string> = { xhs: '小红书', huaban: '花瓣', pinterest: 'Pinterest', zcool: '站酷' }

export default function SyncBar({ onSynced, platform = 'xhs' }: { onSynced: () => void; platform?: SyncPlatform }) {
  const label = PLATFORM_LABEL[platform]
  const [st, setSt] = useState<SyncStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const wasBusy = useRef(false)

  const load = useCallback(async () => {
    try {
      const s = await getSyncStatus(platform)
      setSt(s)
      setError(null)
      if (s.busy || wasBusy.current) onSynced() // committed pages appear while syncing
      wasBusy.current = s.busy
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [onSynced, platform])

  useEffect(() => {
    load()
    const refresh = () => { void load() }
    window.addEventListener("sync-all-started", refresh)
    return () => window.removeEventListener("sync-all-started", refresh)
  }, [load])

  useEffect(() => {
    if (!st?.busy) return
    const t = setInterval(load, 2000)
    return () => clearInterval(t)
  }, [load, st?.busy])

  const act = async (action: 'start' | 'login') => {
    try {
      setSt(await startSync(platform, action))
      wasBusy.current = true
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  if (!st) return error ? <p className="analysis-bar error">{error}</p> : null
  const run = st.last_run
  const cooling = Boolean(st.cooldown?.blocked)
  const needsLogin = !st.busy && run?.status === 'paused' && run.pause_reason === 'needs_login'

  return (
    <div className="analysis-bar">
      <strong aria-label={`${label}连接状态`}>{st.connection_label ?? (cooling ? '被拦截' : st.account_known ? '已连接' : '需要登录')}</strong>{' '}
      <button onClick={() => act('start')} disabled={st.busy || cooling}>
        同步{label}收藏
      </button>
      {cooling && <span role="status">平台风控中，请稍后手动同步（需用户明确解除冷却）</span>}
      {st.action === 'sync' && <span>同步中…已处理 {run?.pages_done ?? 0} 页</span>}
      {platform === 'zcool' && !st.busy && (
        <button onClick={async () => {
          try { setSt(await startZcoolBackfill()); wasBusy.current = true }
          catch (e) { setError(e instanceof Error ? e.message : String(e)) }
        }}>回填更早收藏</button>
      )}
      {(platform==='zcool'||platform==='huaban') && !st.busy && ['needs_verification','blocked'].includes(st.connection_state ?? '') && <>
        <span>需要你在弹出的窗口里手动完成验证</span><button type="button" onClick={async()=>{try{setSt(await verificationAction(platform));wasBusy.current=true}catch{setError('验证窗口打开失败，请重试')}}}>打开{label}验证窗口</button>
      </>}
      {st.action==='verify' && <><span>需要你在弹出的窗口里手动完成验证</span><button type="button" onClick={async()=>{try{await verificationAction(platform,true)}catch{setError('验证确认失败，请重试')}}}>我已完成验证，继续同步</button></>}
      {st.action === 'login'  && <span>请在弹出的 Chrome 窗口里登录{label}（最多等 8 分钟）</span>}
      {!st.busy && run && (
        <span>
          上次同步：新增作品 {run.items_new}
          {run.images_new != null && ` · 新增图片 ${run.images_new}`}
          {run.items_failed > 0 && <span className="error"> · 失败 {run.items_failed}</span>}
          {run.status === 'paused' && run.pause_reason && (
            <span className="error"> · 已暂停：{REASONS[run.pause_reason] ?? run.pause_reason}</span>
          )}
          {run.status === 'failed' && <span className="error"> · 同步失败：{run.error}</span>}
          {run.status !== 'failed' && run.error && <span className="muted"> · {run.error}</span>}
        </span>
      )}
      {needsLogin && !cooling && <button onClick={() => act('login')}>打开{label}登录窗口</button>}
      {!cooling && !st.busy && !run && !st.account_known && <>
        <span className="muted">首次同步需要登录{label}</span>
        <button onClick={() => act('login')}>登录{label}</button>
      </>}
      {!st.busy && st.message && <span className="muted">{st.message}</span>}
      {st.auto?.enabled && <span className="muted">自动检查：{st.auto.running ? '已开启' : '已停止'}
        {st.auto.next_check_at ? ` · 下次 ${new Date(st.auto.next_check_at * 1000).toLocaleTimeString()}` : ''}
      </span>}
      {!st.busy &&
        st.failures.map((f) => (
          <span key={`${f.work_key}-${f.stage}`} className="error">
            {STAGES[f.stage] ?? f.stage}：{f.error}
          </span>
        ))}
      {error && <span className="error">{error}</span>}
    </div>
  )
}

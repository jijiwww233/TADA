import { useLayoutEffect, useRef, useState } from 'react'
import { ArrowLeft } from 'lucide'
import { Icon } from '../lab/Icon'
import type { LabImage } from '../lab/data'
import { wiringJson, type WiringApi } from '../api/wiring'

type Decision = { ids: number[]; keep: boolean; operation: string | null }
const BATCH_LIMIT = 2000

export function PhonePending({ items, status, api, demoReviewed, onClose, onReturn, onRetry, onChanged }: {
  items: LabImage[]
  status: 'loading' | 'ready' | 'error'
  api: WiringApi
  demoReviewed: Set<number> | null
  onClose: () => void
  onReturn: () => void
  onRetry: () => void
  onChanged: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const inFlight = useRef(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [previouslyReviewed] = useState(() => new Set(demoReviewed))

  useLayoutEffect(() => {
    const node = dialog.current
    node?.showModal()
    return () => node?.close()
  }, [])

  const processed = new Map(decisions.flatMap(decision => decision.ids.map(id => [id, decision] as const)))
  const queue = items.filter(item => !previouslyReviewed.has(item.id))
  const remaining = queue.filter(item => !processed.has(item.id))
  const done = status === 'ready' && remaining.length === 0
  /** 标题下固定显示的两个数：处理一张就 +1（撤销会减回去） */
  const kept = decisions.reduce((total, decision) => decision.keep ? total + decision.ids.length : total, 0)
  const dropped = decisions.reduce((total, decision) => decision.keep ? total : total + decision.ids.length, 0)

  const run = async (task: () => Promise<void>, failure: string) => {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError('')
    try { await task() }
    catch { setError(failure) }
    finally { inFlight.current = false; setBusy(false) }
  }
  const decide = (ids: number[], keep: boolean) => run(async () => {
    for (let start = 0; start < ids.length; start += BATCH_LIMIT) {
      const batch = ids.slice(start, start + BATCH_LIMIT)
      const operation = demoReviewed ? null : (await api.decideReference(batch, keep)).operation
      batch.forEach(id => demoReviewed?.add(id))
      setDecisions(current => [...current, { ids: batch, keep, operation }])
      onChanged()
    }
  }, '没保存上，请再试一次。')
  const undo = (decision: Decision) => run(async () => {
    const restored = decision.operation === null
      ? decision.ids
      : (await wiringJson<{ restored: number[] }>(
          `/api/reference/decisions/${encodeURIComponent(decision.operation)}/undo`, { method: 'POST' },
        )).restored
    const restoredIds = new Set(restored)
    restored.forEach(id => demoReviewed?.delete(id))
    setDecisions(current => current.flatMap(entry => {
      if (entry !== decision) return [entry]
      const ids = entry.ids.filter(id => !restoredIds.has(id))
      return ids.length ? [{ ...entry, ids }] : []
    }))
    if (restored.length !== decision.ids.length) setError('有图片后来又被修改了，无法撤销这部分。')
    onChanged()
  }, '没撤销成功，请再试一次。')

  return (
    <dialog ref={dialog} className="m-phone-pending" aria-labelledby="m-phone-pending-title"
      onCancel={event => { event.preventDefault(); if (!inFlight.current) onClose() }}>
      <header className="m-phone-pending-head">
        <button type="button" aria-label="返回" disabled={busy} onClick={onClose}><Icon icon={ArrowLeft} size={20} /></button>
        <div className="m-phone-pending-titles">
          <h2 id="m-phone-pending-title">待确认（{remaining.length}）</h2>
          <p className="m-phone-pending-tally">已保留 {kept} · 不是参考 {dropped}</p>
        </div>
      </header>
      <div className="m-phone-pending-body" data-lenis-prevent>
        <p className="m-phone-pending-intro">AI 觉得这些可能不是设计参考，比如截图、表情包。帮它分一下：保留，或者标为不是参考。</p>
        {status === 'loading' && <p role="status">正在读待确认的图片…</p>}
        {status === 'error' && <button type="button" onClick={onRetry}>没读到图片，点这里重试</button>}
        {queue.map((item, index) => {
          const decision = processed.get(item.id)
          if (decision && item.id !== decision.ids[0]) return null
          const title = item.title?.trim() || item.source || '未命名图片'
          return (
            <article className="m-phone-pending-item" key={item.id}>
              {decision ? (
                <div className="m-phone-pending-result">
                  <span>{decision.keep ? '已保留' : '已标为不是参考'}{decision.ids.length > 1 ? ` ${decision.ids.length} 张` : ''} · </span>
                  <button type="button" disabled={busy} onClick={() => void undo(decision)}>撤销</button>
                </div>
              ) : (
                <>
                  <img src={item.src} alt={title} width={item.w} height={item.h} loading={index < 2 ? 'eager' : 'lazy'} />
                  <p className="m-phone-pending-caption" title={title}>{title}</p>
                  <div className="m-phone-pending-actions">
                    <button type="button" className="m-phone-pending-keep" disabled={busy} onClick={() => void decide([item.id], true)}>保留</button>
                    <button type="button" disabled={busy} onClick={() => void decide([item.id], false)}>不是参考</button>
                  </div>
                </>
              )}
            </article>
          )
        })}
      </div>
      <footer className="m-phone-pending-foot">
        {error && <p role="alert">{error}</p>}
        {done ? (
          <>
            <p role="status">都分好了</p>
            <button type="button" className="m-phone-pending-keep" disabled={busy} onClick={onReturn}>回素材库</button>
          </>
        ) : remaining.length > 0 && (
          <button type="button" className="m-phone-pending-keep" disabled={busy}
            onClick={() => void decide(remaining.map(item => item.id), true)}>剩下的全部保留</button>
        )}
      </footer>
    </dialog>
  )
}

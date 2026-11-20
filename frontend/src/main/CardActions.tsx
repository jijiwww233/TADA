import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { PHONE_QUERY } from './useMobileLayout'
import './card-actions.css'

export type MenuPoint = { x: number; y: number }

type PressHandlers = {
  onPointerDown: (event: React.PointerEvent<HTMLElement>) => void
  onPointerMove: (event: React.PointerEvent<HTMLElement>) => void
  onPointerUp: (event: React.PointerEvent<HTMLElement>) => void
  onPointerCancel: (event: React.PointerEvent<HTMLElement>) => void
  onTouchStart: (event: React.TouchEvent<HTMLElement>) => void
  onTouchMove: (event: React.TouchEvent<HTMLElement>) => void
  onTouchEnd: (event: React.TouchEvent<HTMLElement>) => void
  onTouchCancel: (event: React.TouchEvent<HTMLElement>) => void
  onContextMenu: (event: React.MouseEvent<HTMLElement>) => void
}

export type CardPress = {
  handlers: PressHandlers
  /** 长按 / 右键之后浏览器补的那一下 click 要吞掉，否则会顺手打开详情 */
  swallowClick: () => boolean
}

/** 长按多久弹菜单 */
export const LONG_PRESS_MS = 500
/** 手指挪动超过这么多像素就取消长按（滚动 / 拖拽不算长按） */
const MOVE_CANCEL_PX = 10
/** 长按 / 右键之后多久内的 click 算“补的那一下” */
const SWALLOW_CLICK_MS = 1200

/**
 * 卡片的三件套：手机长按 500ms、电脑右键（contextmenu）、卡片上的「…」按钮，统一打开同一个小菜单。
 * pointer 事件负责主路径（真机上的触摸就是 pointerType === 'touch'），touch 事件一并接住当兜底；
 * 两套事件同时出现时共用同一个计时器，不会弹两次。
 */
export function useCardPress(open: (point: MenuPoint, target: HTMLElement) => void): CardPress {
  const timer = useRef<number | null>(null)
  const origin = useRef<MenuPoint | null>(null)
  const swallowUntil = useRef(0)
  const openRef = useRef(open)
  openRef.current = open

  const stop = () => {
    if (timer.current !== null) { window.clearTimeout(timer.current); timer.current = null }
    origin.current = null
  }
  const fire = (target: HTMLElement, point: MenuPoint) => {
    stop()
    swallowUntil.current = performance.now() + SWALLOW_CLICK_MS
    openRef.current(point, target)
  }
  const start = (target: HTMLElement, point: MenuPoint) => {
    if (timer.current !== null) return
    origin.current = point
    timer.current = window.setTimeout(() => fire(target, point), LONG_PRESS_MS)
  }
  const moved = (x: number, y: number) => {
    const from = origin.current
    return !!from && Math.hypot(x - from.x, y - from.y) > MOVE_CANCEL_PX
  }

  // 页面一滚就一定不是长按（有些浏览器滚动前不一定发 pointercancel）
  useEffect(() => {
    const cancel = () => stop()
    window.addEventListener('scroll', cancel, { passive: true })
    return () => { window.removeEventListener('scroll', cancel); stop() }
  }, [])

  // 手机上不要长按菜单：微信和浏览器会抢先弹自己的菜单，两套叠在一起很乱。删除只从详情页的「更多」进
  const phone = () => window.matchMedia(PHONE_QUERY).matches

  return {
    handlers: {
      onPointerDown: event => {
        if (phone()) return
        if (!event.isPrimary) return
        if (event.pointerType !== 'touch' && event.pointerType !== 'pen') return
        start(event.currentTarget, { x: event.clientX, y: event.clientY })
      },
      onPointerMove: event => { if (moved(event.clientX, event.clientY)) stop() },
      onPointerUp: () => stop(),
      onPointerCancel: () => stop(),
      onTouchStart: event => {
        if (phone()) return
        const touch = event.touches[0]
        if (touch) start(event.currentTarget, { x: touch.clientX, y: touch.clientY })
      },
      onTouchMove: event => {
        const touch = event.touches[0]
        if (touch && moved(touch.clientX, touch.clientY)) stop()
      },
      onTouchEnd: () => stop(),
      onTouchCancel: () => stop(),
      onContextMenu: event => {
        // 电脑右键、安卓长按都会发这个；不拦掉会弹出浏览器自己的菜单
        event.preventDefault()
        if (phone()) return
        fire(event.currentTarget, { x: event.clientX, y: event.clientY })
      },
    },
    swallowClick: () => {
      if (performance.now() >= swallowUntil.current) return false
      swallowUntil.current = 0
      return true
    },
  }
}

export type MenuAction = { key: string; label: string; danger?: boolean; act: () => void }

/** 卡片小菜单：小浮层，点外面 / Esc / 滚动都收起 */
export function ActionMenu({ point, title, actions, onClose }: {
  point: MenuPoint
  title?: string
  actions: MenuAction[]
  onClose: () => void
}) {
  const panel = useRef<HTMLDivElement | null>(null)
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  // 贴着手指 / 鼠标的位置放，放不下就翻到另一边，并夹在窗口里
  useLayoutEffect(() => {
    const el = panel.current
    if (!el) return
    const box = el.getBoundingClientRect()
    const margin = 10
    const gap = 12
    let top = point.y + gap
    if (top + box.height > window.innerHeight - margin) top = point.y - box.height - gap
    top = Math.max(margin, Math.min(top, window.innerHeight - box.height - margin))
    let left = point.x
    if (left + box.width > window.innerWidth - margin) left = point.x - box.width
    left = Math.max(margin, Math.min(left, window.innerWidth - box.width - margin))
    setPlace({ left, top })
  }, [point])

  useEffect(() => {
    const onDown = (event: PointerEvent) => {
      if (panel.current?.contains(event.target as Node)) return
      closeRef.current()
    }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') closeRef.current() }
    const onScroll = () => closeRef.current()
    document.addEventListener('pointerdown', onDown, true)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      document.removeEventListener('pointerdown', onDown, true)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [])

  return createPortal(
    <div className="m-app m-actroot m-actroot-menu">
      <div ref={panel} className="m-actmenu" role="menu" aria-label={title ?? '操作'}
        style={place ? { left: place.left, top: place.top } : { left: -9999, top: -9999 }}>
        {title && <p className="m-actmenu-title">{title}</p>}
        {actions.map(action => (
          <button key={action.key} type="button" role="menuitem" className={`m-actmenu-item${action.danger ? ' is-danger' : ''}`}
            onClick={() => { closeRef.current(); action.act() }}>{action.label}</button>
        ))}
      </div>
    </div>,
    document.body,
  )
}

/** 小面板的公共壳：遮罩 + 居中卡片，Esc / 点遮罩关掉 */
function Overlay({ title, onClose, busy, children }: {
  title: string
  onClose: () => void
  busy: boolean
  children: ReactNode
}) {
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  const busyRef = useRef(busy)
  busyRef.current = busy
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busyRef.current) closeRef.current() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])
  return createPortal(
    <div className="m-app m-actroot m-actroot-dialog">
      <div className="m-actmodal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="m-actmodal-bg" onClick={() => { if (!busyRef.current) closeRef.current() }} />
        <div className="m-actmodal-panel">
          <h2 className="m-actmodal-title">{title}</h2>
          {children}
        </div>
      </div>
    </div>,
    document.body,
  )
}

/** 改名：输入框预填当前名字，保存成功后自己关掉 */
export function RenameDialog({ title = '项目改名', label = '项目名字', initial, onSubmit, onClose }: {
  title?: string
  label?: string
  initial: string
  /** 返回 true 才关面板（失败时留在面板里，用户还能重试或取消） */
  onSubmit: (name: string) => Promise<boolean>
  onClose: () => void
}) {
  const [value, setValue] = useState(initial)
  const [busy, setBusy] = useState(false)
  const name = value.trim()
  const submit = async () => {
    if (!name || busy) return
    setBusy(true)
    try { if (await onSubmit(name)) onClose() } finally { setBusy(false) }
  }
  return (
    <Overlay title={title} onClose={onClose} busy={busy}>
      <form className="m-actform" onSubmit={event => { event.preventDefault(); void submit() }}>
        <label className="m-actfield">
          <span>{label}</span>
          <input className="m-actinput" autoFocus value={value} maxLength={120} aria-label={label}
            onChange={event => setValue(event.target.value)}
            onFocus={event => event.currentTarget.select()} />
        </label>
        <p className="m-acthelp">改名后项目里的图片和顺序都不变。</p>
        <div className="m-actactions">
          <button type="button" className="ds-btn ds-btn--secondary" onClick={onClose} disabled={busy}>取消</button>
          <button type="submit" className="ds-btn ds-btn--primary" disabled={!name || busy}>{busy ? '保存中…' : '保存'}</button>
        </div>
      </form>
    </Overlay>
  )
}

/** 删除前确认：确认按钮一直不给默认焦点，要用户主动点 */
export function ConfirmDialog({ title, text, confirmLabel = '删除', busyLabel = '删除中…', textConfirm = false, onConfirm, onClose }: {
  title: string
  text: ReactNode
  confirmLabel?: string
  busyLabel?: string
  /** 手机：确认键做成陶土橙字的幽灵按钮（不是实心橙底），取消是浅棕次要按钮 */
  textConfirm?: boolean
  /** 返回 true 才关面板（失败时留在面板里，用户还能重试或取消） */
  onConfirm: () => Promise<boolean>
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  const confirm = async () => {
    if (busy) return
    setBusy(true)
    try { if (await onConfirm()) onClose() } finally { setBusy(false) }
  }
  return (
    <Overlay title={title} onClose={onClose} busy={busy}>
      <p className="m-acttext">{text}</p>
      <div className="m-actactions">
        <button type="button" autoFocus className="ds-btn ds-btn--secondary" onClick={onClose} disabled={busy}>取消</button>
        <button type="button" className={textConfirm ? 'ds-btn ds-btn--ghost m-actconfirm-text' : 'ds-btn ds-btn--primary'}
          onClick={() => void confirm()} disabled={busy}>
          {busy ? busyLabel : confirmLabel}
        </button>
      </div>
    </Overlay>
  )
}

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { motion, MotionConfig } from 'motion/react'
import { OVERLAY } from './motion'

/** 一个 dock 项：图标 + 名字 + 是否当前视图 + 点了做什么。 */
export type DockItem = { k: string; label: string; icon: ReactNode; on: boolean; act: () => void }

const SIZE = 52 // 一颗图标的边长
const GAP = 8 // 图标之间
const PAD = 10 // 图标和玻璃边之间
const MAX_SCALE = 0.6 // 光标底下的图标额外放大多少
const SPREAD = 62 // 放大影响到多远
const LINE_W = 124 // 收起时的尺寸（看不见，只给展开动画当起点）
const LINE_H = 6
const OPEN_H = 68
const EDGE = 24 // 指针进到视口底部这么近才把 dock 叫出来
const LINGER = 500 // 指针离开 dock 后多久收起

/**
 * 底部导航：平时完全不显示，指针进到视口底部 24px 以内才浮出玻璃 dock，
 * 离开 dock 0.5 秒后收起；展开后照旧按距离鱼眼放大、邻居被挤开。
 * 触摸设备没有 hover，直接常驻展开。
 */
export function Dock({ items, label = 'Primary navigation' }: { items: DockItem[]; label?: string }) {
  const [open, setOpen] = useState(false)
  const [mx, setMx] = useState<number | null>(null)
  const rowRef = useRef<HTMLDivElement>(null)
  const navRef = useRef<HTMLElement>(null)

  const closedW = items.length * SIZE + (items.length - 1) * GAP + PAD * 2
  const pitch = SIZE + GAP

  useEffect(() => {
    if (window.matchMedia('(pointer: coarse)').matches) { setOpen(true); return }
    let timer = 0
    const stay = () => { window.clearTimeout(timer); timer = 0 }
    const leaveSoon = () => { if (!timer) timer = window.setTimeout(() => { timer = 0; setOpen(false) }, LINGER) }
    const onMove = (e: PointerEvent) => {
      if (e.pointerType === 'touch') return
      if (e.clientY >= window.innerHeight - EDGE) { stay(); setOpen(true); return }
      const box = navRef.current?.getBoundingClientRect()
      const over = !!box && e.clientX >= box.left && e.clientX <= box.right && e.clientY >= box.top && e.clientY <= box.bottom
      if (over) stay(); else leaveSoon()
    }
    const hide = () => { stay(); setOpen(false) }
    window.addEventListener('pointermove', onMove, { passive: true })
    document.documentElement.addEventListener('pointerleave', leaveSoon)
    window.addEventListener('blur', hide)
    return () => {
      stay()
      window.removeEventListener('pointermove', onMove)
      document.documentElement.removeEventListener('pointerleave', leaveSoon)
      window.removeEventListener('blur', hide)
    }
  }, [])

  const scales = items.map((_, i) => {
    if (!open || mx === null) return 1
    const d = mx - (i * pitch + SIZE / 2)
    return 1 + MAX_SCALE * Math.exp(-(d * d) / (2 * SPREAD * SPREAD))
  })
  const extra = scales.map(s => (s - 1) * SIZE)
  const totalExtra = extra.reduce((a, b) => a + b, 0)
  const shifts = extra.map((_, i) => {
    const before = extra.slice(0, i).reduce((a, b) => a + b, 0)
    return before + extra[i] / 2 - totalExtra / 2
  })

  const move = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === 'touch') return
    const row = rowRef.current
    if (!row) return
    setMx(e.clientX - row.getBoundingClientRect().left)
  }

  return (
    <MotionConfig reducedMotion="user">
      <motion.nav
        ref={navRef}
        aria-label={label}
        className={`m-dock${open ? ' is-open' : ''}`}
        initial={false}
        animate={{ width: open ? closedW + totalExtra : LINE_W, height: open ? OPEN_H : LINE_H, borderRadius: open ? 24 : 999 }}
        transition={OVERLAY}
        onFocusCapture={() => setOpen(true)}
        onBlurCapture={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false) }}
      >
        <button type="button" className="m-dock-hint" tabIndex={open ? -1 : 0}
          aria-label="Open navigation" onFocus={() => setOpen(true)} />

        <div className="m-dock-glass" aria-hidden="true" />

        <div ref={rowRef} className="m-dock-row" inert={open ? undefined : true}
          onPointerMove={move} onPointerLeave={() => setMx(null)}>
          {items.map((item, i) => (
            <div key={item.k} className="m-dock-slot" style={{ transform: `translateX(${shifts[i]}px)` }}>
              <motion.button
                type="button"
                className={`m-dock-tile${item.on ? ' is-on' : ''}`}
                style={{ transform: `scale(${scales[i]})` }}
                aria-label={item.label}
                aria-current={item.on ? 'page' : undefined}
                onClick={item.act}
              >
                {item.icon}
              </motion.button>
            </div>
          ))}
        </div>
      </motion.nav>
    </MotionConfig>
  )
}

import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { DRAG, OVERLAY, springStep } from './motion'

export interface OptionWheelProps {
  items: string[]
  defaultSelected?: number
  onChange?: (index: number, item: string) => void
  /** 点正中那一项或按回车：确认当前项 */
  onConfirm?: (index: number, item: string) => void
  /** 无障碍名字 */
  label?: string
  /** 一行多高 */
  rowHeight?: number
  curve?: number
  tilt?: number
  blur?: number
  fade?: number
  minOpacity?: number
  inset?: number
  className?: string
}

/* 吸附用浮层那档弹簧（260 / 28），拖动中用跟手那档（1200 / 80，不回弹）：见 motion.ts */
/** 触控板的小滚动攒够这么多像素才算一格 */
const WHEEL_STEP = 40
/** 松手时按速度往前多带的时间（秒），决定惯性能多走几项 */
const THROW = 0.12

/**
 * 弧形选项轮盘（reactbits 的 option-wheel）：滚轮 / 拖拽 / 方向键翻转，
 * 选中的那条停在中间、颜色最深，上下两条沿弧线往后缩、变淡、变模糊。
 * 手感：滚轮每格正好一项；拖动跟手，松手带一点惯性再用弹簧吸到最近一项；方向键一次一项。
 */
export function OptionWheel({
  items,
  defaultSelected = 0,
  onChange,
  onConfirm,
  label = '排序方式',
  rowHeight = 40,
  curve = 1,
  tilt = 9,
  blur = 1.6,
  fade = 0.42,
  minOpacity = 0.12,
  inset = 70,
  className = '',
}: OptionWheelProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<(HTMLDivElement | null)[]>([])
  const pos = useRef(defaultSelected)
  const vel = useRef(0)
  const target = useRef(defaultSelected)
  // 正在拖：用跟手弹簧；松手后用浮层弹簧吸附
  const follow = useRef(false)
  const raf = useRef<number | null>(null)
  const last = useRef(0)
  const selectedRef = useRef(defaultSelected)
  const onChangeRef = useRef(onChange)
  const wheelAcc = useRef(0)
  const wheelIdle = useRef<ReturnType<typeof setTimeout> | null>(null)
  const drag = useRef<{ y: number; start: number; id: number; samples: { t: number; p: number }[] } | null>(null)
  const dragMoved = useRef(false)
  const [selectedIndex, setSelectedIndex] = useState(defaultSelected)
  const [dragging, setDragging] = useState(false)

  onChangeRef.current = onChange
  const count = items.length
  const clamp = useCallback((v: number) => Math.min(Math.max(v, 0), Math.max(count - 1, 0)), [count])

  const select = useCallback((idx: number) => {
    if (idx === selectedRef.current) return
    selectedRef.current = idx
    setSelectedIndex(idx)
    onChangeRef.current?.(idx, items[idx])
  }, [items])

  /** 按当前位置把每一项摆到弧线上 */
  const paint = useCallback(() => {
    const now = pos.current
    const tiltRad = (tilt * Math.PI) / 180
    const R = tiltRad > 0.0005 ? rowHeight / tiltRad : 0
    for (let i = 0; i < count; i++) {
      const el = itemRefs.current[i]
      if (!el) continue
      const d = i - now
      const dist = Math.abs(d)
      let x = 0
      let y = d * rowHeight
      let rot = 0
      if (R > 0) {
        const ang = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, d * tiltRad))
        y = R * Math.sin(ang)
        x = -R * (1 - Math.cos(ang)) * curve
        rot = (ang * 180) / Math.PI
      }
      el.style.transform = `translate(${x.toFixed(2)}px, calc(${y.toFixed(2)}px - 50%)) rotate(${rot.toFixed(3)}deg)`
      el.style.opacity = String(Math.max(minOpacity, 1 - dist * fade))
      el.style.filter = blur > 0 ? `blur(${(dist * blur).toFixed(2)}px)` : 'none'
      el.style.setProperty('--ow-p', Math.max(0, 1 - Math.min(dist, 1)).toFixed(4))
    }
  }, [count, rowHeight, curve, tilt, blur, fade, minOpacity])

  /** 弹簧一帧：a = -k(x - t) - c·v，小步长积分，停稳就收 */
  const step = useCallback((now: number) => {
    let dt = Math.min((now - last.current) / 1000, 0.05)
    last.current = now
    while (dt > 0) {
      const h = Math.min(dt, 1 / 240)
      const s = springStep(pos.current, vel.current, target.current, follow.current ? DRAG : OVERLAY, h)
      pos.current = s.x
      vel.current = s.v
      dt -= h
    }
    const settled = Math.abs(pos.current - target.current) < 0.001 && Math.abs(vel.current) < 0.01
    if (settled) { pos.current = target.current; vel.current = 0 }
    if (follow.current) select(clamp(Math.round(pos.current)))
    paint()
    raf.current = settled ? null : requestAnimationFrame(step)
  }, [paint, select, clamp])

  const springTo = useCallback((t: number) => {
    target.current = clamp(Math.round(t))
    select(target.current)
    if (raf.current == null) {
      last.current = performance.now()
      raf.current = requestAnimationFrame(step)
    }
  }, [clamp, select, step])

  // 滚轮：一次事件最多走一项；触控板的小滚动攒够一格再走；页面不跟着滚
  useEffect(() => {
    const el = rootRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      e.stopPropagation()
      const delta = e.deltaMode === 1 ? e.deltaY * WHEEL_STEP : e.deltaY
      wheelAcc.current += delta
      if (wheelIdle.current) clearTimeout(wheelIdle.current)
      wheelIdle.current = setTimeout(() => { wheelAcc.current = 0 }, 160)
      if (Math.abs(wheelAcc.current) < WHEEL_STEP) return
      const dir = Math.sign(wheelAcc.current)
      wheelAcc.current = 0
      springTo(target.current + dir)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      el.removeEventListener('wheel', onWheel)
      if (wheelIdle.current) clearTimeout(wheelIdle.current)
    }
  }, [springTo])

  useEffect(() => { paint() }, [paint])
  useEffect(() => () => { if (raf.current != null) cancelAnimationFrame(raf.current) }, [])

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    drag.current = { y: e.clientY, start: pos.current, id: e.pointerId, samples: [{ t: performance.now(), p: pos.current }] }
    dragMoved.current = false
  }
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d) return
    const dy = e.clientY - d.y
    if (!dragMoved.current && Math.abs(dy) > 4) {
      dragMoved.current = true
      setDragging(true)
      rootRef.current?.setPointerCapture(d.id)
      if (raf.current != null) { cancelAnimationFrame(raf.current); raf.current = null }
    }
    if (!dragMoved.current) return
    // 跟手：目标 = 手指位置，用跟手弹簧追上去（很硬、不回弹）；超出两头时有点阻力
    const raw = d.start - dy / rowHeight
    const over = raw < 0 ? raw : raw > count - 1 ? raw - (count - 1) : 0
    follow.current = true
    target.current = raw - over * 0.6
    const t = performance.now()
    d.samples.push({ t, p: target.current })
    while (d.samples.length > 2 && t - d.samples[0].t > 100) d.samples.shift()
    if (raf.current == null) { last.current = performance.now(); raf.current = requestAnimationFrame(step) }
  }
  const onPointerEnd = () => {
    const d = drag.current
    if (!d) return
    drag.current = null
    setDragging(false)
    if (!dragMoved.current) return
    const first = d.samples[0]
    const end = d.samples[d.samples.length - 1]
    const span = (end.t - first.t) / 1000
    const fling = span > 0 ? (end.p - first.p) / span : 0
    follow.current = false
    // 惯性：按松手速度往前多带一点，再用浮层弹簧吸到最近一项（当前速度原样带过去）
    springTo(pos.current + fling * THROW)
  }
  const onItem = (index: number) => {
    if (dragMoved.current) return
    if (index === selectedRef.current && onConfirm) { onConfirm(index, items[index]); return }
    springTo(index)
  }
  const onKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' && onConfirm) {
      e.preventDefault()
      onConfirm(selectedRef.current, items[selectedRef.current])
      return
    }
    let delta: number | null = null
    if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') delta = -1
    else if (e.key === 'ArrowDown' || e.key === 'ArrowRight') delta = 1
    if (delta == null) return
    e.preventDefault()
    springTo(target.current + delta)
  }

  return (
    <div
      ref={rootRef}
      role="listbox"
      tabIndex={0}
      aria-label={label}
      className={`m-ow${dragging ? ' is-dragging' : ''}${className ? ` ${className}` : ''}`}
      style={{ '--ow-font-size': `${Math.round(rowHeight * 0.62)}px`, '--ow-inset': `${inset}px` } as CSSProperties}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      onPointerCancel={onPointerEnd}
      onKeyDown={onKey}
    >
      {items.map((text, index) => (
        <div
          key={`${text}-${index}`}
          ref={el => { itemRefs.current[index] = el }}
          role="option"
          aria-selected={selectedIndex === index}
          className={`m-ow-item${selectedIndex === index ? ' is-on' : ''}`}
          onClick={() => onItem(index)}
        >
          {text}
        </div>
      ))}
    </div>
  )
}

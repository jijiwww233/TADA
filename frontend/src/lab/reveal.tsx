import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useReducedMotionConfig as useReducedMotion } from 'motion/react'

/**
 * Round 4 entrances — one system for text and numbers, two schemes:
 *  1 · 半调 (halftone ink bleed): like ink just dropped on paper — first a spread-out cloud of halftone dots, which
 *      slowly tightens into sharp type (the NEAR "Ink Bleed" poster). One SVG filter per block:
 *      blur(alpha) × gain − dot screen → hard threshold → ink. Only the blur radius / gain change per frame.
 *  2 · 近焦模糊 (near blur, our reading of the Google Flow effect — not verified, see the report): a soft front runs
 *      through the letters; letters near it are blurred, letters it has passed are sharp, letters ahead are not there
 *      yet. Afterwards, letters near the pointer go soft. Only the few letters near the front carry a filter.
 * No ready-made web component fitted (Ink Bleed is a Photoshop technique), so both are hand-written.
 * Reduced motion → clean text immediately, no filter at all.
 */

const easeOut = (t: number) => 1 - (1 - t) ** 3
const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2)
const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

/** 0 until the element is first on screen, then +1 on every replay. */
function useStart(ref: React.RefObject<Element | null>, replay: number) {
  const [n, setN] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setN(v => v || 1); io.disconnect() } }, { threshold: 0.4 })
    io.observe(el)
    return () => io.disconnect()
  }, [ref])
  useEffect(() => { if (replay) setN(v => v + 1) }, [replay])
  return n
}

/** Calls step(t) with t 0..1 over `ms`, after `delay`, each time `start` changes (> 0). step(0) while waiting. */
function useTween(start: number, ms: number, delay: number, step: (t: number) => void, off = false) {
  const reduced = useReducedMotion()
  const fn = useRef(step)
  fn.current = step
  useLayoutEffect(() => {
    if (reduced || off) { fn.current(1); return }
    fn.current(0)
    if (!start) return
    let raf = 0
    const t0 = performance.now() + delay
    const tick = (now: number) => {
      const t = clamp01((now - t0) / ms)
      fn.current(t)
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [start, ms, delay, reduced, off])
}

type RevealProps = { children: ReactNode; className?: string; style?: CSSProperties; delay?: number; replay?: number; off?: boolean }

// ---------------------------------------------------------------- 1 · halftone

/** Dot screen tile, 45°: a dot in each corner and the centre; white = "ink here first". */
const dotTile = (p: number) => `data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns='http://www.w3.org/2000/svg' width='${p}' height='${p}'><defs><radialGradient id='g'><stop offset='0' stop-color='#fff'/><stop offset='1' stop-color='#fff' stop-opacity='0'/></radialGradient></defs>` +
  [[0, 0], [p, 0], [0, p], [p, p], [p / 2, p / 2]].map(([x, y]) => `<circle cx='${x}' cy='${y}' r='${p / 2}' fill='url(#g)'/>`).join('') + '</svg>')}`

/**
 * The halftone filter, as an element + a setter. `pitch` = dot spacing in CSS px.
 * alpha = gain·blur(A) + 0.9·dot − 0.9, then a steep ramp: where the blurred ink is dense, dots merge into solid;
 * where it is thin only the dot centres survive. At blur 0 / gain 1 it is just the letters again.
 */
function useHalftone(pitch: number) {
  const id = `ht${useId().replace(/[^a-z0-9]/gi, '')}`
  const blur = useRef<SVGFEGaussianBlurElement>(null)
  const comp = useRef<SVGFECompositeElement>(null)
  const svg = (
    <svg width="0" height="0" className="pointer-events-none absolute" aria-hidden>
      <filter id={id} x="-25%" y="-40%" width="150%" height="180%" colorInterpolationFilters="sRGB">
        <feGaussianBlur ref={blur} in="SourceAlpha" stdDeviation="0" result="b" />
        <feImage href={dotTile(pitch)} x="0" y="0" width={pitch} height={pitch} preserveAspectRatio="none" result="d" />
        <feTile in="d" result="dt" />
        <feComposite ref={comp} in="b" in2="dt" operator="arithmetic" k1="0" k2="1" k3="0.9" k4="-0.9" result="h" />
        <feComponentTransfer in="h" result="m"><feFuncA type="linear" slope="14" intercept="-0.4" /></feComponentTransfer>
        <feFlood floodColor="currentColor" />
        <feComposite in2="m" operator="in" />
      </filter>
    </svg>
  )
  /** b = blur radius in px, gain ≥ 1 (more ink while it is spread out); b < 0 = filter off */
  const set = (el: HTMLElement, b: number, gain: number) => {
    if (b < 0) { el.style.filter = ''; return }
    blur.current?.setAttribute('stdDeviation', b.toFixed(2))
    comp.current?.setAttribute('k2', gain.toFixed(3))
    el.style.filter = `url(#${id})`
  }
  return { svg, set }
}

/**
 * 半调 · text. 2.1s: the block appears as a wide, dark cloud of dots (blur 0.2em, extra ink ×2.3), which then
 * slowly draws in (ease-out) to sharp type. At the end the filter is removed (clean antialiased text).
 */
export function HalftoneText({ children, className, style, delay = 0, replay = 0, off, ms = 2100, spread = 0.2, onDone }: RevealProps & { ms?: number; spread?: number; onDone?: () => void }) {
  const el = useRef<HTMLDivElement>(null)
  const start = useStart(el, replay)
  const fs = useRef(16)
  useLayoutEffect(() => { if (el.current) fs.current = parseFloat(getComputedStyle(el.current).fontSize) || 16 }, [])
  const ht = useHalftone(7)
  useTween(start, ms, delay, t => {
    const s = el.current
    if (!s) return
    if (t >= 1) { ht.set(s, -1, 1); s.style.opacity = ''; onDone?.(); return }
    const e = 1 - (1 - t) ** 2.4
    ht.set(s, spread * fs.current * (1 - e) + 0.15, 1 + 1.3 * (1 - e))
    s.style.opacity = String(Math.min(0.999, t / 0.08)) // exactly 1 mid-run switches compositing for a frame (a blip)
  }, off)
  return (
    <div ref={el} className={`relative ${className ?? ''}`} style={{ ...style, opacity: 0 }}>
      {ht.svg}
      {children}
    </div>
  )
}

type NumProps = { value: number; className?: string; delay?: number; replay?: number; off?: boolean; duration?: number }

/**
 * 半调 · numbers: counts up (1.8s, ease-out-expo) as a cloud of dots — the faster it runs, the more it spreads —
 * then pulls together into sharp digits the moment it lands (0.35s). tabular-nums, fixed width.
 */
export function HalftoneNumber({ value, className, delay = 0, replay = 0, off = false, duration = 2150 }: NumProps) {
  const el = useRef<HTMLSpanElement>(null)
  const txt = useRef<HTMLSpanElement>(null)
  const start = useStart(el, replay)
  const ht = useHalftone(3)
  const RUN = duration * 1800 / 2150, LAND = duration - RUN
  useTween(start, RUN + LAND, delay, t => {
    const s = el.current
    if (!s || !txt.current) return
    const ms = t * (RUN + LAND)
    const u = clamp01(ms / RUN)
    txt.current.textContent = String(Math.round(value * (u >= 1 ? 1 : 1 - 2 ** (-10 * u))))
    if (t >= 1) { ht.set(s, -1, 1); return }
    const k = u < 1 ? 0.35 + 0.65 * Math.sqrt(2 ** (-10 * u)) : 0.35 * (1 - easeOut(clamp01((ms - RUN) / LAND)))
    ht.set(s, 4 * k + 0.1, 1 + 0.8 * k)
  }, off)
  return (
    <span ref={el} className={`num relative inline-block text-right ${className ?? ''}`} style={{ minWidth: `${String(value).length}ch` }}>
      <span ref={txt}>0</span>{ht.svg}
    </span>
  )
}

// ---------------------------------------------------------------- 2 · near blur

/**
 * Drives the [data-ch] letters: front position f (in letters) → per-letter blur / opacity.
 * Ahead of the front: not there yet; at the front: soft; behind it: sharpens over R letters.
 * `hover` (0..1 per letter) adds the pointer-proximity blur.
 */
function nearFrame(chars: HTMLElement[], sizes: number[], f: number, R: number, hover: number[] | null) {
  for (let i = 0; i < chars.length; i++) {
    const d = i - f
    let b = d >= 0 ? 1 : Math.max(0, 1 + d / R) ** 2
    const o = d <= 0 ? 1 : clamp01(1 - d / R)
    if (hover) b = Math.max(b, hover[i])
    const px = b * 0.14 * sizes[i]
    const s = chars[i].style
    const nf = px > 0.05 ? `blur(${px.toFixed(2)}px)` : ''
    if (s.filter !== nf) s.filter = nf
    const no = o >= 1 ? '' : o.toFixed(3)
    if (s.opacity !== no) s.opacity = no
  }
}

/**
 * 近焦模糊 · text. 1.8s: the front runs through the letters in reading order (both lines, then the small title);
 * after that, letters within ~1.2em of the pointer go soft (up to 0.07em) and come back when it leaves.
 * Children must contain [data-ch] letters (Headline split, or <Chars>).
 */
export function NearBlurText({ children, className, style, delay = 0, replay = 0, off, ms = 1800 }: RevealProps & { ms?: number }) {
  const el = useRef<HTMLDivElement>(null)
  const start = useStart(el, replay)
  const chars = useRef<HTMLElement[]>([])
  const sizes = useRef<number[]>([])
  const done = useRef(false)
  const reduced = useReducedMotion()
  useLayoutEffect(() => {
    const root = el.current
    if (!root) return
    chars.current = Array.from(root.querySelectorAll<HTMLElement>('[data-ch]'))
    sizes.current = chars.current.map(c => parseFloat(getComputedStyle(c).fontSize) || 16)
  }, [children])
  const R = 4
  useTween(start, ms, delay, t => {
    done.current = t >= 1
    const n = chars.current.length
    nearFrame(chars.current, sizes.current, -R + (n + 2 * R) * easeInOut(t), R, null)
  }, off)

  // pointer proximity after the entrance: eased per letter, rAF only while something is still moving
  useEffect(() => {
    if (reduced || off) return
    let px = -1e4, py = -1e4, raf = 0
    let cur: number[] = []
    const loop = () => {
      raf = 0
      if (!done.current) return
      const cs = chars.current
      let moving = false
      cur = cs.map((c, i) => {
        const r = c.getBoundingClientRect()
        const rad = sizes.current[i] * 1.2
        const dist = Math.hypot(px - (r.left + r.width / 2), py - (r.top + r.height / 2))
        const target = dist < rad ? 0.5 * (1 - dist / rad) ** 1.5 : 0
        const v0 = cur[i] ?? 0
        const v = Math.abs(target - v0) < 0.004 ? target : v0 + (target - v0) * 0.22
        if (v !== target) moving = true
        return v
      })
      nearFrame(cs, sizes.current, cs.length + R, R, cur)
      if (moving) raf = requestAnimationFrame(loop)
    }
    const move = (e: PointerEvent) => { px = e.clientX; py = e.clientY; if (!raf) raf = requestAnimationFrame(loop) }
    window.addEventListener('pointermove', move, { passive: true })
    return () => { window.removeEventListener('pointermove', move); cancelAnimationFrame(raf) }
  }, [reduced, off])

  return <div ref={el} className={className} style={style}>{children}</div>
}

/** Plain text split into [data-ch] letters, for NearBlurText. */
export function Chars({ text }: { text: string }) {
  return <>{Array.from(text).map((ch, i) => <span key={i} data-ch className="inline-block whitespace-pre">{ch}</span>)}</>
}

/**
 * 近焦模糊 · numbers: the same front, left → right over the digits (1.8s). Digits ahead of it are still running
 * (dim, soft), at the front they blur, behind it they are the final digit, sharp.
 */
export function NearBlurNumber({ value, className, delay = 0, replay = 0 }: NumProps) {
  const el = useRef<HTMLSpanElement>(null)
  const start = useStart(el, replay)
  const digits = String(value).split('')
  const R = 1.6
  useTween(start, 1800, delay, t => {
    const root = el.current
    if (!root) return
    const cs = Array.from(root.querySelectorAll<HTMLElement>('[data-ch]'))
    const f = -R + (cs.length + 2 * R) * easeOut(t)
    const run = String(Math.round(value * (1 - 2 ** (-10 * t)))).padStart(digits.length, '0')
    cs.forEach((c, i) => { c.textContent = i - f < -0.5 || t >= 1 ? digits[i] : run[i] })
    const size = parseFloat(getComputedStyle(root).fontSize) || 16
    nearFrame(cs, cs.map(() => size), f, R, null)
  })
  return (
    <span ref={el} className={`num inline-flex ${className ?? ''}`} aria-label={String(value)}>
      {digits.map((d, i) => <span key={i} data-ch aria-hidden className="inline-block w-[1ch] text-center">{d}</span>)}
    </span>
  )
}

// ---------------------------------------------------------------- 近焦 (after the entrance)

const isTouch = () => typeof matchMedia !== 'undefined' && matchMedia('(hover: none), (pointer: coarse)').matches

/**
 * 近焦 · the simplest form: one round frosted patch (backdrop-filter blur, soft radial mask edge, ~1.5em wide)
 * follows the pointer over the big title with a little spring lag; fades in on enter / out on leave (0.2s, CSS).
 * No copied text layer, no directional blur. Off on touch devices and with reduced motion.
 */
export function Fog({ on, children }: { on: boolean; children: ReactNode }) {
  const box = useRef<HTMLSpanElement>(null)
  const patch = useRef<HTMLSpanElement>(null)
  const reduced = useReducedMotion()
  const [touch] = useState(isTouch)
  const live = on && !reduced && !touch
  useEffect(() => {
    const b = box.current, p = patch.current
    if (!live || !b || !p) return
    let tx = 0, ty = 0, x = 0, y = 0, raf = 0
    const draw = () => { p.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translate(-50%, -50%)` }
    const loop = () => {
      x += (tx - x) * 0.28; y += (ty - y) * 0.28
      if (Math.abs(tx - x) + Math.abs(ty - y) < 0.3) { x = tx; y = ty; raf = 0 } else raf = requestAnimationFrame(loop)
      draw()
    }
    const aim = (e: PointerEvent) => { const r = b.getBoundingClientRect(); tx = e.clientX - r.left; ty = e.clientY - r.top }
    const move = (e: PointerEvent) => { aim(e); if (!raf) raf = requestAnimationFrame(loop) }
    const enter = (e: PointerEvent) => { aim(e); x = tx; y = ty; draw(); p.style.opacity = '1' }
    const leave = () => { p.style.opacity = '0' }
    b.addEventListener('pointerenter', enter)
    b.addEventListener('pointermove', move)
    b.addEventListener('pointerleave', leave)
    return () => {
      b.removeEventListener('pointerenter', enter); b.removeEventListener('pointermove', move); b.removeEventListener('pointerleave', leave)
      cancelAnimationFrame(raf); p.style.opacity = '0'
    }
  }, [live])
  return <span ref={box} className="relative block">{children}{live && <span ref={patch} aria-hidden className="hl-fog" />}</span>
}

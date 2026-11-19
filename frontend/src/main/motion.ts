/**
 * 全站动效只用这五档（第五轮统一）。CSS 变量和给 Motion / 手写循环用的常量都从这一个文件出：
 * 改这里的数，CSS（var(--mo-*)）和 JS 一起变。
 *
 * - 小反馈（悬停、按下、选中）：0.15s，ease-out
 * - 元素出场：0.6s，上浮 16px，cubic-bezier(0.22, 1, 0.36, 1)，同组错开 40ms，最多错开 8 个，后面的同时出现
 * - 浮层展开 / 收起（Dock、侧栏、胶囊、弹窗）：弹簧 stiffness 260、damping 28
 * - 页面切换：0.8s
 * - 拖拽跟手：弹簧 stiffness 1200、damping 80（过阻尼，不回弹）
 *
 * 例外（不走这五档）：主页筛选 / 排序菜单（照 Lusano 量的时长和错开，见 GlassMenu.tsx）、
 * CALENDAR 热力图（备忘写明“热力图的动效保留”）、平滑滚动 Lenis（这次不改）。
 */

type Bezier = readonly [number, number, number, number]

export const EASE_OUT: Bezier = [0, 0, 0.58, 1]
export const ENTER_EASE: Bezier = [0.22, 1, 0.36, 1]

export const FEEDBACK = { duration: 0.15, ease: EASE_OUT }
export const ENTER = { duration: 0.6, y: 16, ease: ENTER_EASE, step: 0.04, maxSteps: 8 }
export const OVERLAY = { type: 'spring' as const, stiffness: 260, damping: 28, mass: 1 }
export const PAGE = { duration: 0.8, ease: ENTER_EASE }
export const DRAG = { type: 'spring' as const, stiffness: 1200, damping: 80, mass: 1 }

/** 同组第 i 个的出场延迟（秒）：每个错开 40ms，第 8 个以后和第 8 个同时出现 */
export const enterDelay = (i: number) => Math.min(Math.max(0, i), ENTER.maxSteps - 1) * ENTER.step
/** 同上，CSS 用的毫秒字符串（给 --rv-d） */
export const enterDelayMs = (i: number) => `${Math.round(enterDelay(i) * 1000)}ms`

/** 弹簧一步（半隐式欧拉）：手写的 requestAnimationFrame 循环共用，保证和 Motion 那边同一组参数 */
export function springStep(x: number, v: number, target: number, spring: { stiffness: number; damping: number; mass?: number }, dt: number) {
  const m = spring.mass ?? 1
  const a = (-spring.stiffness * (x - target) - spring.damping * v) / m
  const nv = v + a * dt
  return { x: x + nv * dt, v: nv }
}

/**
 * 把弹簧从 0 走到 1 的过程采样成 CSS linear() 缓动，配合它停稳需要的时长。
 * CSS 过渡在中途反向时会从当前位置往回走（不会先播完），但带不过去速度；需要“顺势回弹”的浮层用 Motion 的弹簧。
 */
function springCurve(spring: { stiffness: number; damping: number; mass?: number }, samples = 48) {
  const dt = 1 / 1000
  let x = 0, v = 0, t = 0
  const xs: number[] = [0]
  while (t < 3) {
    ;({ x, v } = springStep(x, v, 1, spring, dt))
    t += dt
    xs.push(x)
    if (Math.abs(1 - x) < 0.001 && Math.abs(v) < 0.01) break
  }
  const out: string[] = []
  for (let i = 0; i <= samples; i++) {
    const k = Math.round((i / samples) * (xs.length - 1))
    out.push(i === samples ? '1' : String(Math.round(xs[k] * 10000) / 10000))
  }
  return { duration: Math.round(t * 1000), linear: `linear(${out.join(', ')})` }
}

const OVERLAY_CSS = springCurve(OVERLAY)
const DRAG_CSS = springCurve(DRAG)
const bezier = (b: Bezier) => `cubic-bezier(${b.join(', ')})`

/** 给 GSAP 之类要缓动函数的地方：cubic-bezier 求值 */
export function cubicBezier([x1, y1, x2, y2]: Bezier) {
  const bx = (t: number) => ((1 - 3 * x2 + 3 * x1) * t + (3 * x2 - 6 * x1)) * t * t + 3 * x1 * t
  const by = (t: number) => ((1 - 3 * y2 + 3 * y1) * t + (3 * y2 - 6 * y1)) * t * t + 3 * y1 * t
  return (x: number) => {
    if (x <= 0) return 0
    if (x >= 1) return 1
    let lo = 0, hi = 1, t = x
    for (let i = 0; i < 24; i++) { t = (lo + hi) / 2; if (bx(t) < x) lo = t; else hi = t }
    return by(t)
  }
}
/** 浮层弹簧的时长（秒）和缓动函数（给 GSAP 用；GSAP 新补间会从当前值接着走，可以中途打断） */
export const OVERLAY_TWEEN = {
  duration: OVERLAY_CSS.duration / 1000,
  ease: (() => {
    const pts = OVERLAY_CSS.linear.slice(7, -1).split(', ').map(Number)
    return (p: number) => {
      const f = Math.min(1, Math.max(0, p)) * (pts.length - 1)
      const i = Math.floor(f)
      return i >= pts.length - 1 ? 1 : pts[i] + (pts[i + 1] - pts[i]) * (f - i)
    }
  })(),
}

/** CSS 变量：装在 <html> 上，主界面所有样式（包括注入的 GlassFolder 样式）都能用 */
export const MOTION_VARS: Record<string, string> = {
  '--mo-fb-dur': `${FEEDBACK.duration * 1000}ms`,
  '--mo-fb-ease': bezier(FEEDBACK.ease),
  '--mo-in-dur': `${ENTER.duration * 1000}ms`,
  '--mo-in-y': `${ENTER.y}px`,
  '--mo-in-ease': bezier(ENTER.ease),
  '--mo-in-step': `${ENTER.step * 1000}ms`,
  '--mo-pop-dur': `${OVERLAY_CSS.duration}ms`,
  '--mo-pop-ease': OVERLAY_CSS.linear,
  '--mo-page-dur': `${PAGE.duration * 1000}ms`,
  '--mo-page-ease': bezier(PAGE.ease),
  '--mo-drag-dur': `${DRAG_CSS.duration}ms`,
  '--mo-drag-ease': DRAG_CSS.linear,
}

export function installMotionVars(root: HTMLElement | null = typeof document !== 'undefined' ? document.documentElement : null) {
  if (!root) return
  for (const [k, v] of Object.entries(MOTION_VARS)) root.style.setProperty(k, v)
}

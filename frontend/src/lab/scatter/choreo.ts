import { rng } from '../data'
import { FOCAL, type Card, type Strip } from './renderer'

/**
 * Choreographies for "散乱 → 整齐 → 收进胶囊". Everything is a pure function of scroll progress `p`
 * (0 = scattered, ~0.72 = neat) and a separate collect clock in seconds, so scrolling back replays exactly.
 *
 *  M (round 3, "混合"): B's layout in front (a few cards around the capsule, one or two in focus), A's full-screen
 *    scatter behind it, but washed out — very blurred, pale, low contrast. Scrolling, the background rises and
 *    forms B's wall together with the front cards; each card comes into focus and gets its colour back as it
 *    lands. No frosted strips. The collect is A's (falling in, reading order).
 *  A / B (round 2, kept unchanged for comparison, frosted strips included).
 */
export type Pose = {
  x: number; y: number; z: number; rx: number; ry: number; rz: number
  /** start state overrides (plan M): fixed blur instead of depth-based, washed-out amount */
  blur?: number; ghost?: number
  /** grid pose of a card that does not join the wall: it dissolves instead */
  gone?: boolean
}
type StripPlan = { x0: number; x1: number; y0: number; y1: number; blur: number; tint: number }
/** Image pool: indexes into the texture array. `face` is the portrait, placed at most once, never centre stage. */
export type Pool = { cards: number[]; face: number | null }
export type Layout = { scat: Pose[]; grid: Pose[]; order: number[]; collect: number[]; layers: number[] }
/** Capsule spring for the final "it's in" bounce. swell: scale up and settle; squash: dips like it caught something. */
export type Bounce = { key: string; label: string; stiffness: number; damping: number; amp: number; mode: 'swell' | 'squash' }

export type Plan = {
  key: 'M' | 'A' | 'B'
  count: number
  cardW: number
  cardH: number
  /** stagger of the collect, seconds between two cards */
  collectGap: number
  collectDur: number
  /** cards come into focus on arrival (M); otherwise blur follows depth only (A / B) */
  focusOnArrival: boolean
  layout: (W: number, H: number, pool: Pool, mobile?: boolean) => Layout
  strips: (W: number, H: number) => StripPlan[]
  /** 0..1 progress at which strip j starts clearing */
  stripOrder: (j: number, n: number) => number
  /** path into the capsule: position at t (0..1) from `from` to the capsule centre */
  path: (from: Pose, to: { x: number; y: number }, t: number, i: number) => { x: number; y: number; rz: number }
}

const D = Math.PI / 180
const clamp01 = (v: number) => Math.min(1, Math.max(0, v))
const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2)
const easeOut = (t: number) => 1 - (1 - t) ** 3
const easeIn = (t: number) => t * t * t
const lerp = (a: number, b: number, t: number) => a + (b - a) * t
const zero = (x: number, y: number): Pose => ({ x, y, z: 0, rx: 0, ry: 0, rz: 0 })
/** depth of field: focus plane at z = 0, far cards blur more than near ones */
const depthDof = (z: number) => (z < 0 ? Math.min(-z / 230, 3.6) : Math.min(z / 110, 2.2))

// Round 3 springs. Round 2 was 380/18 (ζ≈0.46, ~0.3s): too quick to read. Both new ones are softer and swing more.
export const BOUNCES: Bounce[] = [
  { key: 'swell', label: '弹跳 1 · 轻放大', stiffness: 170, damping: 9.5, amp: 0.075, mode: 'swell' }, // ζ≈0.36, ~0.48s period: peak ≈ +4%, then two visible rebounds
  { key: 'squash', label: '弹跳 2 · 接住下沉', stiffness: 120, damping: 7.5, amp: 1.5, mode: 'squash' }, // ζ≈0.34, ~0.57s period: dips ≈7px and squashes ≈4%, then springs back up and settles
]
export const BOUNCE_R2: Bounce = { key: 'r2', label: '第二轮', stiffness: 380, damping: 18, amp: 0.046, mode: 'swell' }

/** Cards keep clear of the capsule's resting band in the middle while scattered. */
function avoidCentre(p: Pose, W: number) {
  if (Math.abs(p.y) < 70 && Math.abs(p.x) < Math.min(360, W * 0.3)) p.y += p.y < 0 ? -110 : 110
  return p
}
/** A / B: plain repeat of the non-portrait images (the portrait is simply left out). */
const repeat = (n: number, pool: Pool) => Array.from({ length: n }, (_, i) => pool.cards[i % pool.cards.length] ?? 0)

// Strips (round 2): 5 vertical frosted bands across the stage, uneven widths, slightly different tops/bottoms,
// plus one that is broken in two.
function stripsFor(W: number, H: number, blurs: number[], tints: number[]): StripPlan[] {
  const cols = [[0.04, 0.215], [0.222, 0.43], [0.437, 0.6], [0.607, 0.8], [0.807, 0.96]]
  const tops = [0.09, 0.05, 0.14, 0.07, 0.11]
  const bottoms = [0.9, 0.95, 0.86, 0.93, 0.89]
  const out: StripPlan[] = cols.map(([a, b], j) => ({ x0: a * W, x1: b * W, y0: tops[j] * H, y1: bottoms[j] * H, blur: blurs[j], tint: tints[j] }))
  const s = out[3]
  out.splice(3, 1, { ...s, y1: H * 0.37 }, { ...s, y0: H * 0.38, blur: Math.max(0.6, s.blur - 1.4), tint: s.tint * 0.7 })
  return out
}

/** B's wall: staggered rows centred on the stage, `rows` cards per row. Row-major, i.e. reading order. */
function staggered(rows: number[], cw: number, ch: number, gx: number, gy: number, cy: number): Pose[] {
  const out: Pose[] = []
  rows.forEach((n, row) => {
    const w = n * cw + (n - 1) * gx
    for (let c = 0; c < n; c++) out.push(zero(-w / 2 + cw / 2 + c * (cw + gx), cy + (row - (rows.length - 1) / 2) * (ch + gy)))
  })
  return out
}
const centreOut = (g: Pose[]) => {
  const d = g.map(p => Math.hypot(p.x, p.y * 1.6))
  const m = Math.max(...d)
  return d.map(v => (v / m) * 0.85)
}

// --- A (round 2) ---
export const PLAN_A: Plan = {
  key: 'A', count: 16, cardW: 128, cardH: 171, collectGap: 0.055, collectDur: 0.8, focusOnArrival: false,
  layout(W, H, pool) {
    const r = rng(11)
    // stratified: one card per cell of a loose 6 x 3 field (two cells left empty), jittered, mixed depths
    const cells = Array.from({ length: 18 }, (_, i) => i).filter(i => i !== 8 && i !== 9)
    const scat = cells.map((cell, i) => avoidCentre({
      x: ((cell % 6) + 0.5 + (r() - 0.5) * 0.8) / 6 * W * 0.98 - W * 0.49,
      y: (Math.floor(cell / 6) + 0.5 + (r() - 0.5) * 0.7) / 3 * H * 0.96 - H * 0.48,
      z: [-560, -260, -40, 140][i % 4] + (r() - 0.5) * 160,
      rx: (r() - 0.5) * 30 * D, ry: (r() - 0.5) * 30 * D, rz: (r() - 0.5) * 24 * D,
    }, W))
    // 8 x 2, wide gutters: a calm band rather than a wall
    const cols = 8, gx = 46, gy = 56, w = cols * this.cardW + (cols - 1) * gx
    const grid = scat.map((_, i) => zero(-w / 2 + this.cardW / 2 + (i % cols) * (this.cardW + gx), -H * 0.075 + (Math.floor(i / cols) - 0.5) * (this.cardH + gy)))
    return {
      scat, grid,
      order: grid.map((_, i) => (i % 8) / 7 * 0.75 + Math.floor(i / 8) * 0.12), // left → right, top row a beat earlier
      collect: grid.map((_, i) => i), // reading order
      layers: repeat(this.count, pool),
    }
  },
  strips: (W, H) => stripsFor(W, H, [3.0, 0.9, 3.6, 2.0, 3.3], [0.34, 0.12, 0.42, 0.24, 0.3]),
  stripOrder: j => j / 5,
  path(from, to, t) {
    // drops into the capsule like water: eases in, slight arc
    const e = easeIn(t)
    return { x: lerp(from.x, to.x, easeInOut(t)), y: lerp(from.y, to.y, e) - Math.sin(Math.PI * t) * 26, rz: (to.x - from.x) * 0.0006 * Math.sin(Math.PI * t) }
  },
}

// --- B (round 2) ---
export const PLAN_B: Plan = {
  key: 'B', count: 20, cardW: 116, cardH: 155, collectGap: 0.045, collectDur: 0.9, focusOnArrival: false,
  layout(W, H, pool) {
    const r = rng(7)
    // a loose spiral around the capsule, going back into depth
    const scat = Array.from({ length: this.count }, (_, i) => {
      const a = i * 2.399963 + 0.4
      const k = (i + 1) / this.count
      const rad = lerp(0.1, 0.5, Math.sqrt(k))
      return avoidCentre({
        x: Math.cos(a) * rad * W * (0.9 + r() * 0.15), y: Math.sin(a) * rad * H * 0.95 * (0.9 + r() * 0.15),
        z: lerp(200, -620, k) + (r() - 0.5) * 120,
        rx: (r() - 0.5) * 24 * D, ry: Math.cos(a) * 15 * D, rz: Math.sin(a) * 12 * D,
      }, W)
    })
    const grid = staggered([7, 6, 7], this.cardW, this.cardH, 58, 34, -H * 0.045)
    return {
      scat, grid, order: centreOut(grid),
      // outside first, the centre last
      collect: grid.map((p, i) => [Math.hypot(p.x, p.y * 1.6), i]).sort((a, b) => b[0] - a[0]).map(([, i]) => i),
      layers: repeat(this.count, pool),
    }
  },
  strips: (W, H) => stripsFor(W, H, [3.4, 2.2, 1.0, 2.6, 3.6], [0.36, 0.26, 0.12, 0.28, 0.4]),
  stripOrder(j) {
    const centre = [2, 1, 0, 3, 3, 4][j] ?? j
    return [0.3, 0.15, 0, 0.15, 0.3][centre] ?? 0.3
  },
  path(from, to, t, i) {
    // swirl in: angle around the capsule closes as it goes
    const dx = from.x - to.x, dy = from.y - to.y
    const a0 = Math.atan2(dy, dx), r0 = Math.hypot(dx, dy)
    const turn = (i % 2 ? 1 : -1) * 0.9 * easeOut(t)
    const rr = r0 * (1 - easeInOut(t))
    return { x: to.x + Math.cos(a0 + turn) * rr, y: to.y + Math.sin(a0 + turn) * rr * 0.7, rz: turn * 0.25 }
  },
}

// --- M (round 3): B in front, A behind ---
// Front: 10 cards around the capsule, lots of air. Screen-space fractions of W / H from the centre (perspective is
// compensated below), depth, tilt. Only the first two are in focus. The top-left corner stays free for the title.
const FRONT: [number, number, number, number][] = [
  [0.125, -0.255, 0, -3], [0.27, 0.235, 0, 2.5], // the two in focus (round 4: the top one moved right, clear of the big title)
  [-0.025, -0.19, -260, 5], [0.3, -0.17, -380, -6], [-0.33, 0.035, -320, 7], [-0.15, 0.27, -200, -5],
  [0.405, 0.04, -480, 4], [-0.385, 0.3, -420, -3], [0.075, 0.31, -340, 6], [-0.075, -0.37, -520, -2],
]
const M_BACK = 30 // background crowd; the wall takes 26 - FRONT.length of them, the rest dissolve
const M_ROWS = [9, 8, 9]

export const PLAN_M: Plan = {
  key: 'M', count: FRONT.length + M_BACK, cardW: 104, cardH: 139, collectGap: 0.045, collectDur: 0.8, focusOnArrival: true,
  layout(W, H, pool) {
    const r = rng(23)
    const persp = (z: number) => FOCAL / (FOCAL - z) // screen scale of something at depth z
    const front: Pose[] = FRONT.map(([fx, fy, z, rz], i) => ({
      x: (fx * W) / persp(z), y: (fy * H) / persp(z), z,
      rx: i < 2 ? 0 : (r() - 0.5) * 18 * D, ry: i < 2 ? 0 : (r() - 0.5) * 18 * D, rz: rz * D,
      // the rest are clearly out of focus and a little faded, so the two in focus lead; still crisper than the crowd
      blur: i < 2 ? 0 : 2 + (-z / 520) * 0.9, ghost: i < 2 ? 0 : 0.3 + (-z / 520) * 0.15,
    }))
    // background: stratified over the whole screen (6 x 5 cells, jittered), far, very blurred, washed out
    const back: Pose[] = Array.from({ length: M_BACK }, (_, i) => {
      const z = -600 - r() * 300
      const sx = ((i % 6) + 0.5 + (r() - 0.5) * 0.9) / 6 - 0.5, sy = (Math.floor(i / 6) + 0.5 + (r() - 0.5) * 0.9) / 5 - 0.5
      return {
        x: (sx * W * 1.04) / persp(z), y: (sy * H * 1.04) / persp(z), z,
        rx: (r() - 0.5) * 30 * D, ry: (r() - 0.5) * 30 * D, rz: (r() - 0.5) * 30 * D,
        blur: 3.6 + r() * 0.8, ghost: 0.76 + r() * 0.1,
      }
    })
    const scat = [...front, ...back]

    // wall slots (reading order); the portrait gets an edge slot: last of the bottom row
    const slots = staggered(M_ROWS, this.cardW, this.cardH, 44, 30, -H * 0.06)
    const faceSlot = slots.length - 1
    const slotOf = new Array<number>(scat.length).fill(-1)
    const taken = new Set<number>()
    const screen = (p: Pose) => ({ x: p.x * persp(p.z), y: p.y * persp(p.z) })
    // greedy nearest matching in screen space: front cards first (they are already "found"), then the crowd
    const match = (cards: number[], free: (s: number) => boolean, limit: number) => {
      const pairs: [number, number, number][] = []
      for (const ci of cards) for (let s = 0; s < slots.length; s++) if (free(s)) {
        const a = screen(scat[ci])
        pairs.push([Math.hypot(a.x - slots[s].x, a.y - slots[s].y), ci, s])
      }
      pairs.sort((a, b) => a[0] - b[0])
      let n = 0
      for (const [, ci, s] of pairs) {
        if (n >= limit) break
        if (slotOf[ci] >= 0 || taken.has(s)) continue
        slotOf[ci] = s; taken.add(s); n++
      }
    }
    match(front.map((_, i) => i), s => s !== faceSlot, front.length)
    match(back.map((_, i) => front.length + i), () => true, slots.length - front.length)

    // images: neighbours differ (row offset 4 in a staggered wall); the portrait only in its edge slot
    const cards = pool.cards.length ? pool.cards : [0]
    const slotImg = slots.map((_, s) => {
      let row = 0, col = s
      while (col >= M_ROWS[row]) col -= M_ROWS[row++]
      return cards[(row * 4 + col) % cards.length]
    })
    if (pool.face !== null) slotImg[faceSlot] = pool.face

    const dOrder = centreOut(slots)
    const grid: Pose[] = [], order: number[] = [], layers: number[] = []
    scat.forEach((p, i) => {
      const s = slotOf[i]
      if (s >= 0) {
        grid.push(slots[s])
        order.push(Math.max(0, dOrder[s] - (i < front.length ? 0.12 : 0))) // front cards a beat earlier
        layers.push(slotImg[s])
      } else {
        // not needed for the wall: sinks back and dissolves into the paper
        grid.push({ ...p, x: p.x * 1.15, y: p.y * 1.15, z: p.z - 400, gone: true })
        order.push(r() * 0.25)
        layers.push(cards[Math.floor(r() * cards.length)])
      }
    })
    const collect = scat.map((_, i) => i).filter(i => slotOf[i] >= 0).sort((a, b) => slotOf[a] - slotOf[b])
    return { scat, grid, order, collect, layers }
  },
  strips: () => [],
  stripOrder: () => 0,
  path: PLAN_A.path, // A's "drops in like water" — the one the user liked
}

// Scroll windows (fractions of the pinned scroll)
export const P_START = 0.06 // hold scattered a moment
export const P_NEAT = 0.72 // everything neat and clear
export const P_COLLECT = 0.84 // past this, cards flow into the capsule (time-based, staggered)
const FLY = 0.42 // each card's own window inside [P_START, P_NEAT]
const ARRANGE_STAGGER = P_NEAT - P_START - FLY

export function matrixSettledProgress(layout: Layout) {
  return P_START + Math.max(...layout.order) * ARRANGE_STAGGER + FLY
}
const CLEAR = 0.3 // each strip's clearing window

/** One shared collect sampler. Intro ejection calls it with reversed time and reversed order. */
export function collectionPose(plan: Plan, from: Pose, to: { x: number; y: number }, ct: number, i: number) {
  const q = plan.path(from, to, ct, i)
  return { ...q, scale: lerp(1, 0.12, easeIn(ct)), alpha: 1-clamp01((ct-0.5)/0.45) }
}

export type Frame = { cards: Card[]; strips: Strip[]; capsuleY: number; neat: number }

/**
 * @param p      smoothed scroll progress 0..1
 * @param clock  collect clock, seconds since collect started (0 when not collecting)
 * The capsule sits at the stage centre while scattered and settles lower as the grid forms; cards in the
 * collect fly to wherever it is.
 */
export function frame(plan: Plan, H: number, lay: Layout, stripPlan: StripPlan[], p: number, clock: number, out: Frame, /** 排好后胶囊中心离舞台中线多远（× H）；手机首屏胶囊钉在 73%，传 .23 */ capsuleDrop = 0.33): Frame {
  const { scat, grid, order, collect, layers } = lay
  const neat = easeInOut(clamp01((p - P_START) / (P_NEAT - P_START)))
  out.capsuleY = lerp(0, H * capsuleDrop, neat)
  out.neat = neat
  const capsule = { x: 0, y: out.capsuleY }
  const rank = new Array<number>(scat.length).fill(-1)
  collect.forEach((ci, k) => (rank[ci] = k))
  for (let i = 0; i < scat.length; i++) {
    const s = scat[i], g = grid[i]
    const u = clamp01((p - (P_START + order[i] * ARRANGE_STAGGER)) / FLY)
    const t = easeInOut(u)
    const lift = g.gone ? 0 : Math.sin(Math.PI * t) * 90 // a little towards the viewer mid-flight
    let x = lerp(s.x, g.x, t), y = lerp(s.y, g.y, t)
    const z = lerp(s.z, g.z, t) + lift
    let rz = lerp(s.rz, g.rz, t), scale = 1, alpha = 1
    let dof = depthDof(z), ghost = 0
    if (plan.focusOnArrival) {
      // stays out of focus (and pale) for most of the trip, snaps into focus as it lands: "found it"
      const f = g.gone ? 0 : easeOut(clamp01((u - 0.45) / 0.55))
      dof = lerp(s.blur ?? depthDof(s.z), dof, f)
      ghost = (s.ghost ?? 0) * (1 - f)
      if (g.gone) { alpha = 1 - easeInOut(clamp01(u * 1.6)); ghost = lerp(ghost, 1, u); dof = Math.min(5, dof + u) }
    }
    const ct = rank[i] >= 0 ? clamp01((clock - rank[i] * plan.collectGap) / plan.collectDur) : 0
    if (ct > 0) {
      const q = collectionPose(plan, g, capsule, ct, i)
      x = q.x; y = q.y; rz = q.rz
      scale = q.scale
      alpha = q.alpha
      dof = 0; ghost = 0
    }
    const c = out.cards[i] ?? (out.cards[i] = { x: 0, y: 0, z: 0, rx: 0, ry: 0, rz: 0, alpha: 1, dof: 0, scale: 1, layer: 0, ghost: 0 })
    c.x = x; c.y = y; c.z = z; c.rx = lerp(s.rx, g.rx, t); c.ry = lerp(s.ry, g.ry, t); c.rz = rz
    c.alpha = alpha; c.dof = dof; c.scale = scale; c.layer = layers[i]; c.ghost = ghost
  }
  out.cards.length = scat.length

  // (A / B) strips clear one by one; they also drift a few px with the scroll
  const n = stripPlan.length
  stripPlan.forEach((sp, j) => {
    const t0 = P_START + 0.04 + plan.stripOrder(j, n) * (P_NEAT - P_START - CLEAR - 0.06)
    const k = 1 - easeInOut(clamp01((p - t0) / CLEAR))
    const drift = (j % 2 ? -1 : 1) * 18 * (1 - k)
    const s = out.strips[j] ?? (out.strips[j] = { x0: 0, y0: 0, x1: 0, y1: 0, blur: 0, tint: 0, seam: 0 })
    Object.assign(s, { x0: sp.x0 + drift, x1: sp.x1 + drift, y0: sp.y0, y1: sp.y1, blur: sp.blur * k, tint: sp.tint * k, seam: Math.min(1, k * 1.6) })
  })
  out.strips.length = n
  return out
}

export function collectLength(plan: Plan, lay: Layout) {
  return (lay.collect.length - 1) * plan.collectGap + plan.collectDur
}

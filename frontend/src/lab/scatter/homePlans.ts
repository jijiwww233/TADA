import { PLAN_A, PLAN_B, type Plan, type Pose } from './choreo'

export type HomeDirection = 'cover' | 'premotio' | 'book'
export const COVER_FRONT = [2, 9]
export const INITIAL_CARD_COUNT = 16
export const MOBILE_INITIAL_CARD_COUNT = 8

/** Home-only adapters of pure A. No mixed-plan background, strips or invented card metadata. */
export function homePlan(direction: HomeDirection): Plan {
  return {
    ...PLAN_A,
    cardW: 148, cardH: 148 * 4 / 3,
    strips: () => [],
    layout(W, H, pool, mobile = false) {
      const a = PLAN_A.layout(W, H, pool)
      const screenPose = (p: Pose, x: number, y: number, z = p.z): Pose => {
        const perspective = (1100 - z) / 1100
        return { ...p, x: (x - 0.5) * W * perspective, y: (y - 0.5) * H * perspective, z }
      }
      if (mobile) {
        const positions = [
          [.16, .17], [.39, .21], [.63, .15], [.85, .22],
          [.15, .66], [.39, .69], [.63, .65], [.85, .70],
        ]
        a.scat = a.scat.map((p, i) => {
          const [x, y] = positions[i % MOBILE_INITIAL_CARD_COUNT]
          return screenPose(p, x, y, [-180, -280, -100, -220][i % 4])
        })
      } else if (direction === 'cover') {
        const positions = [
          [.08, .12], [.30, .10], [.39, .20], [.78, .16], [.96, .30], [.76, .47],
          [.92, .70], [.13, .96], [.44, .88], [.44, .61], [.66, .82], [.90, .94],
          [.08, .49], [.62, .07], [.73, .96], [.98, .50],
        ]
        a.scat = a.scat.map((p, i) => screenPose(p, positions[i][0], positions[i][1], COVER_FRONT.includes(i) ? 70 : p.z))
      } else if (direction === 'premotio') {
        a.scat = a.scat.map((p, i) => screenPose(p,
          (i % 6 + .45) / 6 + Math.sin(i * 2.3) * .025,
          .08 + Math.floor(i / 6) * .18 + Math.cos(i * 1.7) * .035))
      } else {
        a.scat = a.scat.map((p, i) => screenPose(p, .57 + (i % 4) * .135 + Math.sin(i) * .012, .09 + Math.floor(i / 4) * .245, [-180, 40, -340, -80][i % 4]))
      }
      // The first sixteen scatter poses/layers above are exactly 9bc3f96.
      // Four additional cards only enter after scrolling, to complete B's 7 / 6 / 7 wall.
      const b = PLAN_B.layout(W, H, pool)
      a.scat.push(...b.grid.slice(INITIAL_CARD_COUNT).map(p => ({ ...p, z: -500 })))
      a.grid = b.grid; a.order = b.order
      a.layers.push(...b.layers.slice(INITIAL_CARD_COUNT))
      a.collect = b.grid.map((_, i) => i)
      if (mobile) {
        const rows = [4, 4, 4, 4, 4]
        a.grid = rows.flatMap((n, row) => Array.from({ length: n }, (_, col) => ({
          x: -(n * PLAN_B.cardW + (n - 1) * 58) / 2 + PLAN_B.cardW / 2 + col * (PLAN_B.cardW + 58),
          y: -H * .10 + (row - (rows.length - 1) / 2) * Math.min(PLAN_B.cardH + 34, H * .138),
          z: 0, rx: 0, ry: 0, rz: 0,
        })))
      }
      return a
    },
  }
}

import { PLAN_A, PLAN_B, type Plan, type Pose } from './choreo'

export type HomeDirection = 'cover' | 'premotio' | 'book'
export const COVER_FRONT = [2, 9]
export const INITIAL_CARD_COUNT = 16
export const MOBILE_INITIAL_CARD_COUNT = 8
/**
 * 手机引导首屏（/intro，宽 < 600）：按电脑版 1440×900 的比例换算出来的散卡（换算过程见 PHONE_SCATTER 的注释）。
 * 首屏只露这 7 张，其余几张从屏幕下方飞进来组墙。
 */
export const PHONE_INITIAL_CARD_COUNT = 7
/** 手机引导首屏图片墙：12 张（电脑版 20 张的 60%），3 列 × 4 行 */
export const PHONE_WALL_COUNT = 12
const PHONE_WALL_COLS = 3
const PHONE_WALL_ROWS = 4
/**
 * 电脑版 1440×900（premotio）实算：k = 0.9，卡片基准宽 148×0.9 = 133px，深度 z 取 -560 / -260 / -40 / 140 四档，
 * 透视后宽 88～153px，平均约 120px（屏宽 8.3%），高 160px；每行 6 张，列距 240px（卡宽:空隙 ≈ 1:1）；
 * 行距 0.18H = 162px（≈ 一张卡高）；第一行中心在 0.08H = 72px（≈ 0.45 张卡高），所以上沿出血到屏幕顶外。
 * 手机 390×844：卡片平均宽取现状（约 70px）的 4/3 ≈ 93px（屏宽 24%），高 124px；保持卡宽:空隙 ≈ 1:1，
 * 一行放 2 张（中心 0.26W / 0.74W，间距 187px），中间一行 3 张、左右两张一半出血；行距 = 一张卡高 124px = 0.147H；
 * 第一行中心 = 0.45 张卡高 ≈ 56px = 0.066H，同样出血到顶边外。抖动按卡片尺寸等比换算。
 * 实测（6eb493d）卡宽约 96、高 128；因为上下抖动 ±25px，第一行写 0.04H（落在 30～60px，两张都出血），
 * 行距按实测卡高 128 ≈ 0.15H。这些是屏幕上的真实位置（透视差值在 ScatterStage 里补）。
 * （试过再补两张填缝卡把中间那条空白填掉，覆盖率 63%，老师选了不补的这版，就留在 7 张。）
 */
const PHONE_SCATTER: [number, number][] = [
  [.26, .04], [.74, .04],
  [-.02, .19], [.5, .19], [1.02, .19],
  [.26, .34], [.74, .34],
]

/** Home-only adapters of pure A. No mixed-plan background, strips or invented card metadata. */
export function homePlan(direction: HomeDirection, phoneIntro = false): Plan {
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
      if (mobile && !phoneIntro) {
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
        a.scat = a.scat.map((p, i) => {
          const x = (i % 6 + .45) / 6 + Math.sin(i * 2.3) * .025
          const row = Math.floor(i / 6)
          const drift = Math.cos(i * 1.7)
          if (phoneIntro) {
            // 首屏 7 张按电脑比例散开（深度四档和电脑一样）；其余的放在屏幕下方外面，组墙时才飞进来
            const spot = PHONE_SCATTER[i]
            return spot
              // 中间一行左右两张要稳定露一半，不加横向抖动
              ? screenPose(p, spot[0] + (spot[0] < 0 || spot[0] > 1 ? 0 : Math.sin(i * 2.3) * .05), spot[1] + drift * .03)
              : screenPose(p, .2 + (i % 4) * .2, 1.18 + (i % 2) * .08)
          }
          return screenPose(p, x, .08 + row * .18 + drift * .035)
        })
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
      if (mobile && phoneIntro) {
        // 手机引导首屏的图片墙：电脑版 20 张，手机只排 12 张（60%），3 列 × 4 行，列距行距都放宽，保持散开的感觉。
        // 整面墙放在顶栏下方（.09H）到胶囊上方之间：胶囊被 intro-phone.css 钉在 73%、高 52（半高 26px ≈ 47 单位），
        // 再留约 24px（≈ 43 单位）空白 → 墙底 = .73H − 90 单位。原来 5 行 × 4 张、行距按屏宽算，
        // 屏幕矮一点（如 390×750）最后一行就压到胶囊底下。
        // 卡片尺寸：ScatterStage 排墙时卡宽 = PLAN_B.cardW × .78（单位和这里一致）。
        // 第 13～20 张仍保留在数组里（环/层号等都按 20 张对齐），但 ScatterStage 在手机上把它们藏掉、也不收进胶囊。
        const cardH = PLAN_B.cardH * .78, cardW = PLAN_B.cardW * .78
        const top = H * .09, bottom = H * .73 - 90
        const pitch = Math.max(cardH * 1.2, Math.min((bottom - top - cardH) / (PHONE_WALL_ROWS - 1), cardH * 2))
        const firstY = (top + bottom) / 2 - pitch * (PHONE_WALL_ROWS - 1) / 2 - H / 2
        const colGap = Math.min(W * .3, cardW * 2.4)
        a.grid = a.grid.map((_, i) => {
          const slot = i % PHONE_WALL_COUNT, row = Math.floor(slot / PHONE_WALL_COLS), col = slot % PHONE_WALL_COLS
          return { x: (col - (PHONE_WALL_COLS - 1) / 2) * colGap, y: firstY + row * pitch, z: 0, rx: 0, ry: 0, rz: 0 }
        })
        a.collect = a.collect.filter(i => i < PHONE_WALL_COUNT)
      } else if (mobile) {
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

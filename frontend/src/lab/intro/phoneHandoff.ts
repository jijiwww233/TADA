import { take, type LabImage } from '../data'
import type { Card } from '../scatter/renderer'

/**
 * 手机引导页 P2 的两段衔接共用的「哪张图在哪」：
 *   选平台页卷轴 → 整理页卡片环 → 最后一页底部转盘，三处用同一组图，交接时每张卡的图都对得上。
 *
 * 整理页的环有 16 张卡，最后一页的转盘（IntroGallery）正好也是 take(cards, 16)，所以环上第 i 张 = 转盘第 i 张。
 * 卷轴停在「下一步」时（updateWheel 里 current 夹到 4 → at = 15 + 4×3 + 1 = 28，即第二份里的第 10 张）
 * 能看见中心上下各 5 张；把转盘前 11 张按「中心、下 1、上 1、下 2、上 2 …」填进这些位置，
 * 环上第 i 张就从卷轴上同一张图的位置卷进去。
 */
export const REEL_PER_COPY = 18
/** 卷轴停在「下一步」时中心那张的 data-reel-index（= motion.ts updateWheel 的 at，current 夹到 4） */
export const REEL_END_AT = 28
/** 环上第 i 张卡对应卷轴上「离中心几格」 */
export const REEL_OFFSETS = [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5]
export const RING_CARDS = 16
/**
 * 整理页 → 最后一页：环缩小、下沉成底部转盘那一排，用 0.9 秒缓入缓出（原来 0.55 秒，看起来是「卡巴」一下掉下去）。
 * gather 这一段比动画多留 0.1 秒，保证最后一帧确实落到终点再换页（不然 ready 接手时还差一点点，会跳一下）。
 * 之后（ready）：画布那排卡和转盘交叉淡化（画布 0.45～0.85 秒淡出，转盘 0.4 秒淡入），标题、胶囊、按钮 0.45 秒淡入上移。
 */
export const PHONE_GATHER_S = .9
export const PHONE_GATHER_MS = 1000
/** 手机换页（第一页 → Key 页、Key 页 → 选平台页）：旧页面淡出上移 0.26 秒，新页面淡入上移 0.34 秒，合计 0.6 秒 */
export const PHONE_LEAVE_MS = 260

const mod = (n: number, m: number) => ((n % m) + m) % m

/** 转盘（= 环）上 16 张卡各是 items 里的第几张（纹理层号就是 items 下标） */
export function galleryLayers(items: LabImage[], cards: LabImage[]): number[] {
  return take(cards, RING_CARDS).map(card => Math.max(0, items.findIndex(item => item.src === card.src)))
}

/** 卷轴一份 18 张，各是 items 里的第几张 */
export function reelLayers(items: LabImage[], cards: LabImage[]): number[] {
  const ring = galleryLayers(items, cards)
  if (!ring.length) return Array.from({ length: REEL_PER_COPY }, (_, k) => k % Math.max(1, items.length))
  const out: number[] = Array(REEL_PER_COPY).fill(-1)
  const center = REEL_END_AT % REEL_PER_COPY
  REEL_OFFSETS.forEach((off, i) => { out[mod(center + off, REEL_PER_COPY)] = ring[i % ring.length] })
  let next = REEL_OFFSETS.length
  return out.map(layer => layer >= 0 ? layer : ring[next++ % ring.length])
}

/**
 * 最后一页底部转盘的一排卡在画布坐标里的位置（相对舞台中心），照 CircularGallery 的算法抄：
 *   容器 = intro-phone.css 里 bottom −24px、高 min(34dvh, 350px)；卡高 = 0.6 × 容器高 × itemSize(.89)，
 *   宽 = 高 × .78，卡距 = 宽 × 1.26；弧：depth = bend(2.4) × 容器高 / 16.57。整个转盘 opacity .86（intro.css）。
 */
export function galleryRow(W: number, H: number, ringCardW: number) {
  const gh = Math.min(.34 * H, 350)
  const h0 = .6 * gh * .89, w0 = h0 * .78
  const w = w0 * Math.min(1, W * .82 / w0)
  const depth = 2.4 * gh / 16.57, half = Math.max(W, gh * 1.9) / 2
  return { y: H / 2 + 24 - gh / 2, spacing: w * 1.26, scale: w / ringCardW, alpha: .86, seconds: PHONE_GATHER_S, radius: (half * half + depth * depth) / (2 * depth), half }
}

/** 卷轴里一张卡在画布坐标里的样子（相对舞台中心，像素） */
export type ReelShot = { x: number; y: number; w: number; alpha: number; blur: number }

/** 读卷轴里每张可见卡的屏幕位置（3D 变换后的包围盒），按 data-reel-index 存 */
export function snapReel(root: HTMLElement | null, canvas: HTMLElement): Map<number, ReelShot> {
  const shots = new Map<number, ReelShot>()
  if (!root) return shots
  const frame = canvas.getBoundingClientRect()
  const cx = frame.left + frame.width / 2, cy = frame.top + frame.height / 2
  root.querySelectorAll<HTMLElement>('.intro-phone-reel-track img').forEach(img => {
    if (img.style.visibility === 'hidden') return
    const box = img.getBoundingClientRect()
    if (!box.width) return
    const blur = Number(/blur\(([\d.]+)px\)/.exec(img.style.filter)?.[1] ?? 0)
    shots.set(Number(img.dataset.reelIndex), {
      x: box.left + box.width / 2 - cx, y: box.top + box.height / 2 - cy,
      w: box.width, alpha: Number(img.style.opacity || 1), blur,
    })
  })
  return shots
}

/**
 * 环上 16 张卡的起点：前 11 张取卷轴上对应那张的位置/大小/透明度/模糊，
 * 后 5 张卷轴上没露出来，从最外两张的位置透明地出发。
 * @param cardW 整理页环上卡片的基准宽（renderer.cardW），scale = 卷轴卡宽 / cardW
 */
export function seedFromReel(shots: Map<number, ReelShot>, layers: number[], cardW: number): Card[] | null {
  const center = shots.get(REEL_END_AT)
  if (!center || !layers.length) return null
  const dofOf = (blur: number) => Math.min(3.4, blur * .35)
  const edge = [shots.get(REEL_END_AT + 5) ?? center, shots.get(REEL_END_AT - 5) ?? center]
  return Array.from({ length: RING_CARDS }, (_, i) => {
    const off = REEL_OFFSETS[i]
    const shot = off === undefined ? undefined : shots.get(REEL_END_AT + off)
    const from = shot ?? edge[i % 2]
    return {
      x: from.x, y: from.y, z: 0, rx: 0, ry: 0, rz: 0,
      scale: (shot ? from.w : from.w * .6) / cardW,
      alpha: shot ? from.alpha : 0,
      layer: layers[i % layers.length], dof: dofOf(from.blur), ghost: 0,
    } as Card
  })
}

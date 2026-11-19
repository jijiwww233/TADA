import { FOCAL, type Card } from '../scatter/renderer'
import { galleryRow } from './phoneHandoff'
import { REEL_ARC, REEL_SPREAD } from './motion'
import { RING_SECONDS } from './ringGeometry'

/**
 * 手机引导页「一镜到底」开关。
 *   false（默认）：完全走现在线上的版本，这个文件里的代码一行都不会执行，相关 CSS 也不生效（靠 data-one-take 属性）。
 *   true：第一页 → 收进胶囊 → Key 页和原版一样（Key 页没有卡片）；从选平台页开始，画布上同一组卡片接着变队形：
 *         淡入成右栏卷轴 → 卷成整理页的环 → 环散开、一张接一张沿弧线飞进最后一页底部转盘（落定后交给原版的 CircularGallery）。
 * 只影响手机（宽 < 600 的 /intro）；电脑版不经过这里。
 */
export const PHONE_ONE_TAKE: boolean = true

/**
 * 基准尺寸 S = 原版（一镜到底之前）整理页环上正前方那张卡的屏幕宽度。按原版代码算：
 *   环上卡片基准宽 = .46 × .412W（ScatterStage 的 ringCardW），环半径 .412W、倾角 −8°，
 *   正前方那张 z = cos 8° × .412W，透视放大 1100 / (1100 − z)。
 *   390×844：基准宽 73.9px，z = 159.1，放大 1.169 → S = 86.4 × 115.2px。
 */
export const ringBase = (W: number) => .46 * .412 * W
export const oneTakeS = (W: number) => ringBase(W) * FOCAL / (FOCAL - Math.cos(8 * Math.PI / 180) * .412 * W)

/** 第一页图片墙：按 S 排 4 行时行间只剩约 10px（太挤），改成 3 列 × 3 行 = 9 张，行距 = 列距 */
export const ONE_TAKE_WALL_CARDS = 9
export const ONE_TAKE_WALL_ROWS = 3
/**
 * 第一页卡宽 = PLAN_B.cardW × k × 这个数（homePlans 的墙用布局单位，拿不到真实屏宽，所以用一个常数逼近 S）：
 * 390×844 → 86.6px（1.002S），375×667 → 83.3px（1.008S），430×932 → 95.5px（0.985S）
 */
export const ONE_TAKE_WALL_SCALE = 1.34

/** 整理页 → 最后一页：gather 只留文字淡出的 0.3 秒，卡片飞行跨过 gather 和 ready 两段（整段 1.1 秒），最后一页的文字在飞行中途淡入 */
export const ONE_TAKE_GATHER_MS = 300
/** 最后一页转盘（CircularGallery）挂上后先停多久再转：要等画布上那排卡淡完（见下面的时间线） */
export const ONE_TAKE_GALLERY_HOLD_MS = 1800
/** 最后一页转盘的巡航速度：原来 −0.35，快 1.5 倍 */
export const ONE_TAKE_GALLERY_SPEED = -.35 * 1.5
/** 转盘卡宽 = S：CircularGallery 卡高 = 0.6 × 容器高 × itemSize，卡宽 = 卡高 × .78；容器高 = min(34dvh, 350px)（intro-phone.css） */
export const oneTakeItemSize = (W: number, H: number) => oneTakeS(W) / (.6 * Math.min(.34 * H, 350) * .78)
/** 环散开时正前方那张是环上第几张：最后一页的转盘从这一张开始排（IntroGallery 挂上时读），落点和图对得上 */
export const oneTakeGallery = { offset: 0 }

/** 环：和原版整理页一样 16 张、半径 .412W、倾角 −8°、40 秒一圈、环心 .0395H − 88 */
const RING_N = 16
const STEP = Math.PI * 2 / RING_N
const RING_TILT = -8 * Math.PI / 180
/** 卷轴上 12 张（第 0～11 张，从上到下），环上第 j 张 = 卷轴第 (j + 6) mod 16 张：
 *  12 张以正前方为中心左右对称排开（卷轴上面的去左边、下面的去右边），第 12～15 张卷轴上没有，在环背面淡入 */
const REEL_N = 12
const RING_ANCHOR = 6
/** 队形变换：单张 0.7 秒，16 张错开 0.02 秒 → 整段 1.0 秒 */
const MORPH_S = .7
const STAGGER_S = .02
/**
 * 环 → 底部转盘（整段 1.1 秒，从 gather 开始算）：
 *   正前方那张和左右各 3 张（转盘上第 −3～3 格）飞过去，顺序 0、−1、1、−2、2、−3、3，每张错开 40ms，每张飞 0.86 秒；
 *   其余 9 张在环背面，0.45 秒内跟着环转、淡出。
 *   1.15 秒转盘（CircularGallery）开始 0.4 秒淡入（intro-phone.css 的延迟 850ms，从 ready 挂上算），
 *   1.6～2.0 秒画布这排卡淡出，2.1 秒转盘开始转（ONE_TAKE_GALLERY_HOLD_MS，从挂上算）。
 */
const FLY_REACH = 3
const FLY_STAGGER = .04
const FLY_S = 1.1 - FLY_STAGGER * FLY_REACH * 2
const BACK_FADE_S = .45
const ROW_FADE_FROM = 1.6
const ROW_FADE_S = .4

export type Formation = 'none' | 'reel' | 'ring' | 'carousel'

const clamp01 = (n: number) => Math.max(0, Math.min(1, n))
const mix = (a: number, b: number, t: number) => a + (b - a) * t
const smooth = (n: number) => { const t = clamp01(n); return t * t * (3 - 2 * t) }
const easeInOut = (n: number) => { const t = clamp01(n); return t < .5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2 }
const wrap = (a: number) => Math.atan2(Math.sin(a), Math.cos(a))
const mod = (n: number, m: number) => ((n % m) + m) % m
/** CSS cubic-bezier 的同款求值 */
function bezierEase(x1: number, y1: number, x2: number, y2: number) {
  return (x: number) => {
    if (x <= 0) return 0
    if (x >= 1) return 1
    let t = x
    for (let i = 0; i < 8; i++) {
      const fx = 3 * x1 * t * (1 - t) ** 2 + 3 * x2 * t * t * (1 - t) + t ** 3 - x
      const dx = 3 * x1 * (1 - t) ** 2 + 6 * (x2 - x1) * t * (1 - t) + 3 * (1 - x2) * t * t
      if (Math.abs(dx) < 1e-6) break
      t = clamp01(t - fx / dx)
    }
    return 3 * y1 * t * (1 - t) ** 2 + 3 * y2 * t * t * (1 - t) + t ** 3
  }
}
/** 飞进转盘的缓出曲线：起步稍微柔一点（不从环上猛地弹出去），后段长长地减速落位 */
const flyEase = bezierEase(.3, 0, .15, 1)

/** 屏幕坐标（相对舞台中心）→ 渲染器坐标：渲染器会把 x/y/大小都除以 (FOCAL − z)/FOCAL，这里先乘回去。rel = 屏幕上的卡宽 / 渲染器基准卡宽 */
function place(sx: number, sy: number, z: number, rel: number, extra: Partial<Card>, layer: number): Card {
  const w = (FOCAL - z) / FOCAL
  return { x: sx * w, y: sy * w, z, rx: 0, ry: 0, rz: 0, alpha: 1, dof: 0, ghost: 0, scale: rel * w, layer, ...extra }
}

/** 选平台页 DOM 卷轴（motion.ts updateWheel 手机段）同一套「梯子」档位，按中心卡 = S 重新换算 */
const SIZE = [1, .88, .78, .69, .61, .54, .48, .43]
const FADE = [1, .9, .75, .6, .45, .3, .18, .1]
const BLUR = [0, 1, 2, 3.5, 5, 7, 9, 11]
const ZS = [0, -32, -68, -108, -152, -200, -252, -308]
const PERSP = 620
const tier = (v: number[], a: number) => { const i = Math.min(v.length - 1, Math.floor(a)); return i >= v.length - 1 ? v[i] : v[i] + (v[i + 1] - v[i]) * (a - i) }

/** 卷轴中心对着第几张：平台转盘位置 current（0..5）→ 每格走 2 张，从第 1 张走到第 11 张 */
export const reelAt = (current: number) => 1 + 2 * Math.max(0, Math.min(5, current))

export type OneTakeInput = {
  formation: Formation
  now: number
  W: number
  H: number
  /** 渲染器基准卡宽（= 原版环上卡片基准宽 ringBase(W)） */
  base: number
  /** 基准尺寸 S（px） */
  S: number
  reduced: boolean
  /** 环上 16 张各用哪张图（= 最后一页转盘的 16 张，phoneHandoff.galleryLayers） */
  layers: number[]
  /** 平台转盘的连续位置 */
  current: number
  /** 选平台页右栏卷轴中心（相对舞台中心）；页面还没挂上时为 null，用估算值 */
  reel: { x: number; y: number } | null
}

/**
 * 一镜到底的「镜头」（选平台页以后）：每换一个队形，把上一帧画出来的样子存成起点，逐张缓入缓出地走到新队形
 * （目标逐帧重算，所以环的转动、卷轴的滚动在过渡中也是连续的）。上一帧没有的卡（透明）原地淡入并从下方轻轻上移。
 * 卡片编号 j = 环上第 j 张 = 转盘第 j 张图。
 */
export class OneTake {
  cards: Card[] = []
  private from: Card[] = []
  private formation: Formation = 'none'
  private started = 0
  /** 环开始转的时刻（成环动画结束时） */
  private spinFrom = 0
  /** 环散开时正前方那张（转盘第 0 格） */
  private front = 0
  /** 这次进转盘是不是从环飞过去的（直接跳页、减少动效时不飞，画布不画，转盘直接出来） */
  private fromRing = false

  draw(o: OneTakeInput): Card[] {
    const { formation, now, reduced } = o
    if (formation !== this.formation) {
      const previous = this.formation
      this.from = this.cards.map(c => ({ ...c }))
      this.formation = formation
      this.started = now
      if (formation === 'ring') this.spinFrom = now + (MORPH_S + STAGGER_S * (RING_N - 1)) * 1000
      if (formation === 'carousel') {
        this.fromRing = previous === 'ring' && !reduced
        this.front = this.fromRing ? mod(Math.round(this.spinAt(now, reduced) / STEP), RING_N) : 0
        oneTakeGallery.offset = this.front
      }
    }
    if (formation === 'none') { this.cards.length = 0; return this.cards }
    if (formation === 'carousel') return this.carousel(o)
    const elapsed = (now - this.started) / 1000
    const spin = this.spinAt(now, reduced)
    for (let j = 0; j < RING_N; j++) {
      const layer = o.layers[j % Math.max(1, o.layers.length)] ?? 0
      const rank = mod(j + RING_ANCHOR, RING_N)
      const target = formation === 'reel' ? this.reelPose(rank, o, layer) : this.ringPose(j, spin, o.W, o.H, layer)
      const old = this.from[j]
      if (reduced) { this.cards[j] = target; continue }
      // 卷轴：离中心近的先出来；卷成环：卷轴从上到下依次走，背面那 4 张最后淡入
      const order = formation === 'reel' ? Math.abs(rank - reelAt(o.current)) : rank
      const e = easeInOut((elapsed - order * STAGGER_S) / MORPH_S)
      if (!old || old.alpha < .01) {
        // 上一个队形里没有这张：在新位置淡入，从下方 14px 轻轻上移（和页面文字的淡入一致）
        this.cards[j] = { ...target, y: target.y + 14 * (1 - e) * (FOCAL - target.z) / FOCAL, alpha: target.alpha * e }
        continue
      }
      this.cards[j] = {
        x: mix(old.x, target.x, e), y: mix(old.y, target.y, e), z: mix(old.z, target.z, e),
        rx: mix(old.rx, target.rx, e), ry: mix(wrap(old.ry), target.ry, e), rz: mix(old.rz, target.rz, e),
        scale: mix(old.scale, target.scale, e), alpha: mix(old.alpha, target.alpha, e),
        dof: mix(old.dof, target.dof, e), ghost: mix(old.ghost ?? 0, target.ghost ?? 0, e), layer,
      }
    }
    this.cards.length = RING_N
    return this.cards
  }

  private spinAt(time: number, reduced: boolean) {
    return reduced ? 0 : Math.max(0, time - this.spinFrom) / 1000 / RING_SECONDS * Math.PI * 2
  }

  /** 卷轴：和 DOM 卷轴同一套梯子（尺寸/透明度/模糊/后退/倾斜/圆弧往右收），中心那张 = S。第 12～15 张不在卷轴上（透明） */
  private reelPose(rank: number, o: OneTakeInput, layer: number): Card {
    const h = o.S * 4 / 3, k = h / 159 // DOM 卷轴按卡高 159 定的档位，按比例换到 S
    const center = o.reel ?? this.reelGuess(o.W, o.H)
    const d = rank - reelAt(o.current), a = Math.min(7, Math.abs(d)), sg = Math.sign(d)
    const TILT = Math.PI / 30, tilt = (n: number) => Math.min(n * TILT, Math.PI / 6)
    const proj = SIZE.map((s, n) => h * s * Math.cos(tilt(n)) * PERSP / (PERSP - ZS[n] * k))
    const YS = [0]; for (let n = 1; n < SIZE.length; n++) YS[n] = YS[n - 1] + proj[n - 1] / 2 + proj[n] * REEL_SPREAD
    const RC = h * REEL_ARC
    const ys = sg * tier(YS, a), yy = Math.min(Math.abs(ys), RC * .95)
    const xs = RC - Math.sqrt(RC * RC - yy * yy)
    const size = tier(SIZE, a) * PERSP / (PERSP - tier(ZS, a) * k)
    // z 只用来排前后（中心最前），大小已经在 size 里，place 会抵消 z 带来的透视
    return place(center.x + xs, center.y + ys, -a * 6, size * o.S / o.base, {
      rx: -sg * tilt(a), rz: -Math.asin(sg * yy / RC) * .5,
      alpha: rank < REEL_N ? tier(FADE, a) : 0, dof: Math.min(3.4, tier(BLUR, a) * .35 / k),
    }, layer)
  }

  /**
   * 选平台页还没挂上（换页的淡出期间卡片就开始出来了）时，按 intro-phone.css 估右栏卷轴中心：
   * 内容宽 = W − 2 × 页边距（clamp(16, 5vw, 28)），右栏 34%；竖直方向是两栏区域的中点。页面挂上后改用实测位置。
   */
  private reelGuess(W: number, H: number) {
    const g = Math.max(16, Math.min(28, W * .05)), col = (W - 2 * g) * .34
    const top = 236, bottom = H - 112
    return { x: W / 2 - g - col / 2, y: (top + Math.min(bottom, top + 560)) / 2 - H / 2 }
  }

  /** 原版整理页的环（flowScene reveal 段，原样搬过来）：第 j 张在 j × 22.5° − 转过的角度 */
  private ringPose(j: number, spin: number, W: number, H: number, layer: number): Card {
    const R = W * .412, a = j * STEP - spin
    const depth = Math.cos(a) * R, facing = (1 + Math.cos(a)) / 2
    return {
      x: Math.sin(a) * R, y: H * .0395 - 88 - Math.sin(RING_TILT) * depth, z: Math.cos(RING_TILT) * depth,
      rx: RING_TILT, ry: wrap(a), rz: 0, scale: .95 + .05 * facing, alpha: 1,
      dof: .48 * (1 - facing) ** 2, ghost: 0, layer,
    }
  }

  /** 环散开 → 底部转盘（时间线见 FLY_* 常数的注释） */
  private carousel(o: OneTakeInput): Card[] {
    const { now, W, H, base } = o
    if (!this.fromRing) { this.cards.length = 0; return this.cards }
    const t = (now - this.started) / 1000
    const fade = 1 - smooth((t - ROW_FADE_FROM) / ROW_FADE_S)
    if (fade <= 0) { this.cards.length = 0; return this.cards }
    // 转盘那一排（照 CircularGallery 的算法，卡宽 = S）：第 slot 格在 x = slot × 卡距，两边沿弧往下弯、顺着弧转一点
    const row = galleryRow(W, H, base, oneTakeItemSize(W, H))
    const spin = this.spinAt(now, false)
    for (let j = 0; j < RING_N; j++) {
      const layer = o.layers[j % Math.max(1, o.layers.length)] ?? 0
      const slot = mod(j - this.front + 7, RING_N) - 7
      let card: Card
      if (Math.abs(slot) > FLY_REACH) {
        // 环背面那几张：跟着环接着转，悄悄淡出
        card = this.ringPose(j, spin, W, H, layer)
        card.alpha *= 1 - smooth(t / BACK_FADE_S)
      } else {
        const depart = (Math.abs(slot) * 2 - (slot < 0 ? 1 : 0)) * FLY_STAGGER
        if (t < depart) card = this.ringPose(j, spin, W, H, layer)
        else {
          const u = flyEase((t - depart) / FLY_S)
          // 起点：离开那一刻在环上的样子（屏幕坐标、屏幕上的大小）
          const spin0 = this.spinAt(this.started + depart * 1000, false), a0 = j * STEP - spin0
          const s0 = this.ringPose(j, spin0, W, H, layer)
          const w0 = (FOCAL - s0.z) / FOCAL
          const x0 = s0.x / w0, y0 = s0.y / w0, rel0 = s0.scale / w0
          // 终点：转盘上第 slot 格
          const x1 = slot * row.spacing, reach = Math.min(Math.abs(x1), row.half, row.radius * .999)
          const y1 = row.y + row.radius - Math.sqrt(row.radius * row.radius - reach * reach)
          const turn = Math.sign(x1) * Math.asin(reach / row.radius)
          // 弧线：起步方向顺着环转动的方向（环往左转，正前方那张先往左带一点），再弯下去落到格子里
          const dist = Math.hypot(x1 - x0, y1 - y0)
          const cx = x0 - Math.cos(a0) * dist * .35, cy = y0 + (y1 - y0) * .1
          const bx = (1 - u) ** 2 * x0 + 2 * u * (1 - u) * cx + u * u * x1
          const by = (1 - u) ** 2 * y0 + 2 * u * (1 - u) * cy + u * u * y1
          card = place(bx, by, mix(s0.z, 0, u), mix(rel0, row.scale, u), {
            rx: mix(RING_TILT, 0, u), ry: mix(wrap(a0), 0, u), rz: mix(0, turn, u),
            alpha: mix(1, row.alpha, u), dof: mix(s0.dof, 0, u),
          }, layer)
        }
      }
      card.alpha *= fade
      this.cards[j] = card
    }
    this.cards.length = RING_N
    return this.cards
  }
}

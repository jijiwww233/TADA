import type { CSSProperties } from 'react'
import { FOCAL, type Card } from '../scatter/renderer'
import { REEL_ARC, REEL_SPREAD } from './motion'
import { RING_SECONDS } from './ringGeometry'

/**
 * 手机引导页「一镜到底」开关。
 *   false（默认）：完全走现在线上的版本，这个文件里的代码一行都不会执行，相关 CSS 也不生效（靠 data-one-take 属性）。
 *   true：第一页 → 收进胶囊 → Key 页和原版一样（Key 页没有卡片）；从选平台页开始，画布上同一组卡片接着变队形：
 *         淡入成右栏卷轴 → 卷成整理页的环；整理页 → 最后一页是普通的整页淡出 / 淡入（最后一页底部转盘是原版的 CircularGallery）。
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

/**
 * 一镜到底（手机）的统一转场：页面之间的切换（旧页淡出、新页淡入，含整理页 → 最后一页）、卡片队形变换（卷轴 / 环），
 * 全部用这一个时长和这一条缓入缓出曲线（= CSS 的 ease-in-out）。
 * JS 里直接引用；CSS 通过舞台上的 --intro-tr-dur / --intro-tr-ease 引用（ScatterStage 把 ONE_TAKE_TRANSITION_VARS 挂在舞台上）。
 * 要改转场快慢，只改这里。
 */
export const ONE_TAKE_TRANSITION = { ms: 500, ease: [.42, 0, .58, 1] as const }
export const ONE_TAKE_TRANSITION_VARS = {
  '--intro-tr-dur': `${ONE_TAKE_TRANSITION.ms}ms`,
  '--intro-tr-ease': `cubic-bezier(${ONE_TAKE_TRANSITION.ease.join(', ')})`,
} as CSSProperties
const TR_S = ONE_TAKE_TRANSITION.ms / 1000
/** 整理页「卡片围成环转圈」那一页停多久：真机反馈再砍一半（useIntroFlow 的 reveal 停留时间 × 这个数） */
export const ONE_TAKE_REVEAL_SCALE = .5

/**
 * 整理页 → 最后一页：和其他页面一样的普通转场（真机反馈，不再做「环散开飞进转盘」的连续卡片动画）。
 *   gather = 整理页整体淡出（文字、胶囊、画布上的环一起，统一转场时长，intro-phone.css）；
 *   ready = 最后一页整体淡入（标题、胶囊、按钮、底部转盘，同一个时长）
 */
export const ONE_TAKE_GATHER_MS = ONE_TAKE_TRANSITION.ms
/** 最后一页转盘的巡航速度：原来 −0.35，快 1.5 倍 */
export const ONE_TAKE_GALLERY_SPEED = -.35 * 1.5
/** 转盘卡宽 = S：CircularGallery 卡高 = 0.6 × 容器高 × itemSize，卡宽 = 卡高 × .78；容器高 = min(34dvh, 350px)（intro-phone.css） */
export const oneTakeItemSize = (W: number, H: number) => oneTakeS(W) / (.6 * Math.min(.34 * H, 350) * .78)

/** 环：和原版整理页一样 16 张、半径 .412W、倾角 −8°、40 秒一圈、环心 .0395H − 88 */
const RING_N = 16
const STEP = Math.PI * 2 / RING_N
const RING_TILT = -8 * Math.PI / 180
/** 卷轴上 12 张（第 0～11 张，从上到下），环上第 j 张 = 卷轴第 (j + 6) mod 16 张：
 *  12 张以正前方为中心左右对称排开（卷轴上面的去左边、下面的去右边），第 12～15 张卷轴上没有，在环背面淡入 */
const REEL_N = 12
const RING_ANCHOR = 6
/**
 * 队形变换（卷轴、卷成环）：16 张同时走，每张都是统一转场（TR_S，缓入缓出），整段 = TR_S。
 *   改前：卷轴单张 0.7 秒 + 错开 0.02 秒 → 整段 1.0 秒；卷成环单张 0.35 秒 + 错开 0.01 秒 → 整段 0.5 秒。
 *   错开会让整段比统一时长长，所以去掉了。
 * 整理页以后（gather、ready）队形一直是环：环原地接着转，由 CSS 跟着整页一起淡出。
 */

export type Formation = 'none' | 'reel' | 'ring'

const clamp01 = (n: number) => Math.max(0, Math.min(1, n))
const mix = (a: number, b: number, t: number) => a + (b - a) * t
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
/** 统一转场曲线（和 CSS 里 --intro-tr-ease 同一条）：队形变换用它 */
const trEase = bezierEase(...ONE_TAKE_TRANSITION.ease)

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

  draw(o: OneTakeInput): Card[] {
    const { formation, now, reduced } = o
    if (formation !== this.formation) {
      this.from = this.cards.map(c => ({ ...c }))
      this.formation = formation
      this.started = now
      if (formation === 'ring') this.spinFrom = now + TR_S * 1000
    }
    if (formation === 'none') { this.cards.length = 0; return this.cards }
    const elapsed = (now - this.started) / 1000
    const spin = this.spinAt(now, reduced)
    for (let j = 0; j < RING_N; j++) {
      const layer = o.layers[j % Math.max(1, o.layers.length)] ?? 0
      const rank = mod(j + RING_ANCHOR, RING_N)
      const target = formation === 'reel' ? this.reelPose(rank, o, layer) : this.ringPose(j, spin, o.W, o.H, layer)
      const old = this.from[j]
      if (reduced) { this.cards[j] = target; continue }
      // 统一转场：16 张同时走，TR_S 走完
      const e = trEase(elapsed / TR_S)
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

}

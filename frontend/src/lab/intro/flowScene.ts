import type { Card } from '../scatter/renderer'
import { collectionPose, type Plan, type Pose } from '../scatter/choreo'
import type { IntroState } from './useIntroFlow'
import { RING_COUNT, RING_SECONDS, ringAngle } from './ringGeometry'
import { arc, platformLayout } from './motion'
import { StarField } from './starField'

const clamp = (n: number) => Math.max(0, Math.min(1, n))
const mix = (a: number, b: number, t: number) => a + (b - a) * t
const ease = (n: number) => { const t = clamp(n); return t * t * (3 - 2 * t) }
/** 缓入缓出（三次）：手机「环 → 转盘」用 */
const easeInOut = (n: number) => { const t = clamp(n); return t < .5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2 }
export type CapsulePose = { x: number; y: number; width: number; height: number; radius: number }
export function capsuleTarget(state: IntroState, W: number, H: number, advanced: boolean): CapsulePose {
  const normal = { x: 0, y: 0, width: Math.min(620, W * .9), height: 60, radius: 30 }
  if (state.phase === 'key') {
    if (['contracting','checking','verified'].includes(state.keyStatus)) return {...normal,width:Math.min(400,W*.9)}
    const height=(W <= 700 ? 296 : 284)+(advanced?124:0)+(state.error?30:0)
    const top=H*(W<=700?.32:.36)
    return {x:0,y:top+height/2-H/2,width:Math.min(420,W <= 700 ? W - 32 : W*.9),height,radius:16}
  }
  // “全部” moves the capsule now, in parallel with the number reveal, not after it.
  if(state.phase==='ready')return {...normal,width:Math.min(620,W<=700?W-32:W*.42)}
  if (state.phase === 'reveal' || state.phase === 'gather') return normal
  const l=platformLayout(W,H)
  // 手机：那条「已连接 0/5」胶囊铺满内容宽（两条边距和页面其它页一致），电脑版仍是左侧 400 宽
  if (W<=700) return {x:0,y:l.top+l.capTop+30-H/2,width:W*.9,height:60,radius:30}
  return {x:l.left+l.width/2-W/2,y:l.top+l.capTop+30-H/2,width:l.width,height:60,radius:30}

}

/** Original home poses and dimensions are kept. Only the field's position is mapped to the right-hand arc. */
export class FlowScene {
  cards: Card[] = []
  private stars=new StarField()
  private phase = ''
  private started = 0
  private ejectStarted: number | null = null
  private ejectOrigin: {x:number;y:number} | null = null
  private clarity = Array(RING_COUNT).fill(0)
  private connectedAt = Array(RING_COUNT).fill(0)
  private previous: Card[] = []
  private ringClock = 0
  private highlight = Array(RING_COUNT).fill(0)
  /** 手机：卷轴卷进环时每张卡的起点（phoneHandoff.seedFromReel）；电脑版一直是 null */
  seed: Card[] | null = null
  /** 手机：环上 16 张卡用哪几张图（= 最后一页转盘的 16 张）；null = 用 layers */
  ringLayers: number[] | null = null
  /** 手机：最后一页底部转盘的一排卡（相对舞台中心的 y、卡距、缩放）；null = 电脑版那样收进胶囊 */
  row: { y: number; spacing: number; scale: number; alpha: number; seconds: number; radius: number; half: number } | null = null
  private ringEnd: Card[] = []

  draw(state: IntroState, W: number, H: number, now: number, dt: number, layers: number[], capsule: CapsulePose, reduced: boolean, home: Pose[], plan: Plan, collect: number[], current=state.active, starfield=false, ringYShift=0, ringRadius=.2) {
    if (state.phase !== this.phase) {
      this.previous = this.cards.map(c => ({ ...c })); this.phase = state.phase; this.started = now; this.ejectOrigin = null; this.ejectStarted = null
      if (state.phase === 'reveal') this.ringClock = now + 1500
      // 手机：环从卷轴上那几张卡的位置出发（不是从画布里那些散卡）
      if (state.phase === 'reveal' && this.seed) this.previous = this.seed.map(c => ({ ...c }))
    }
    if(state.phase==='unlock'&&state.platformEntry==='cards'&&this.ejectStarted===null)this.ejectStarted=now
    const elapsed = (now - this.started) / 1000, mobile = W <= 700
    // 手机 clearing（只有一两帧）：先原样画出卷轴那几张卡，接上 DOM 卷轴的最后一帧
    if (state.phase === 'clearing' && this.seed) {
      this.seed.forEach((c, i) => { this.cards[i] = { ...c } })
      this.cards.length = this.seed.length
      return this.cards
    }
    // 手机最后一页：转盘（DOM 里的 CircularGallery）刚挂上、纹理可能还没到，画布上那排卡先垫着，
    // 0.45～0.85 秒慢慢淡掉，和转盘的 0.4 秒淡入交叉（intro-phone.css 里画布 0.9 秒后隐藏）
    if (state.phase === 'ready' && this.row && this.ringEnd.length && !reduced) {
      const fade = 1 - ease((elapsed - .45) / .4)
      this.ringEnd.forEach((c, i) => { this.cards[i] = { ...c, alpha: c.alpha * fade } })
      this.cards.length = fade > 0 ? this.ringEnd.length : 0
      return this.cards
    }
    if (state.phase === 'home' || state.phase === 'key' || state.phase === 'ready') { this.cards.length = 0; return this.cards }
    const targets = home.slice(0, RING_COUNT).map(s => {
      const perspective = (1100 - s.z) / 1100
      const sy = mobile ? H * .02 + s.y / perspective * .80 : H * .30 + s.y / perspective * 1.65
      const curve = arc(sy / (H * .45), H * .45, 180)
      const sx = mobile ? s.x / perspective * .70 : W * .25 + s.x / perspective * .43 + curve.x
      return { ...s, x: sx * perspective, y: (mobile ? sy : curve.y) * perspective }
    })
    const stars=starfield&&['unlock','platforms','clearing'].includes(state.phase)
    if (stars) this.stars.advance(current, reduced)
    const ejectOrder = state.phase === 'unlock' ? collect.filter(i => i < RING_COUNT).reverse() : []
    for (let i = 0; i < RING_COUNT; i++) {
      const owner = i % 5, connected = state.platforms[owner] === 'connected'
      let target=targets[i]
      if (connected && !this.connectedAt[i]) this.connectedAt[i] = now
      if (!connected) this.connectedAt[i] = 0
      // Same-platform cards focus strictly by horizontal screen-space rank, never by diagonal array order.
      const rank = stars ? Math.floor(i / 5) : targets
        .map((pose, index) => ({ pose, index }))
        .filter(card => card.index % 5 === owner)
        .sort((a, b) => a.pose.x / (1100 - a.pose.z) - b.pose.x / (1100 - b.pose.z))
        .findIndex(card => card.index === i)
      const lit = connected && (reduced || now - this.connectedAt[i] >= rank * 180) ? 1 : 0
      this.clarity[i] += (lit - this.clarity[i]) * Math.min(1, dt * 4)
      const clarity = reduced ? Number(connected) : this.clarity[i]
      this.highlight[i] += ((state.active === owner ? 1 : 0) - this.highlight[i]) * Math.min(1, dt * 3)
      const star=stars?this.stars.pose(target,i,W,H,current,now,clarity,reduced):null
      if(star)target=star
      let { x, y, z, rx, ry, rz } = target
      let dof=(1-clarity)*3.4,ghost=(1-clarity)*.78
      let scale = star?.scale??1, alpha = star?star.opacity:.70 + clarity * .27 + this.highlight[i] * .03
      if (star) {
        dof = star.dof
        ghost = star.ghost
      }
      if (state.phase === 'unlock') {
        const rank = ejectOrder.indexOf(i)
        const ejectElapsed = this.ejectStarted === null ? -1 : (now - this.ejectStarted) / 1000
        const time = ejectElapsed - rank * plan.collectGap * .9
        if(!this.ejectOrigin && this.ejectStarted!==null) this.ejectOrigin={x:capsule.x+capsule.width/2-12,y:capsule.y}
        const flight = reduced ? 1 : clamp(time / (plan.collectDur * 1.1))
        const u = flight + flight * flight * (1 - flight)
        const landing = clamp((flight - .8) / .2)
        const rebound = 2.6 * Math.sin(Math.PI * landing) * (1 - landing) ** 2
        // Sample the shared arc in screen space so depth cannot pull the origin away from the capsule.
        const perspective=(1100-target.z)/1100
        const projected={...target,x:target.x/perspective,y:target.y/perspective}
        const q=collectionPose(plan,projected,this.ejectOrigin??capsule,1-u,i)
        z=mix(0,target.z,u)
        const depth=(1100-z)/1100
        x=q.x*depth;y=(q.y+rebound)*depth;rz=mix(q.rz,target.rz,u)
        rx=target.rx*u;ry=target.ry*u;scale=q.scale*(star?.scale??1);alpha*=reduced?1:time<=0?0:clamp(u/.12)
      }
      if (state.phase === 'reveal' || state.phase === 'gather') {
        // 手机从卷轴卷进来：按离卷轴中心的远近依次出发（第 i 张 = 离中心第 i 近），间隔拉开一点才看得出「依次」
        const u=clamp((elapsed-i*(this.seed?.05:.03))/1.15)
        const t=reduced?1:u<.5?4*u*u*u:1-(-2*u+2)**3/2
        const angle=ringAngle(i)*Math.PI/180-Math.max(0,now-this.ringClock)/1000/RING_SECONDS*Math.PI*2
        // ringRadius：环的半径占屏宽多少（电脑 0.2；手机引导页按「卡宽:半径」同比例换算后另传）
        const radius=W*ringRadius,tilt=-8*Math.PI/180,depth=Math.cos(angle)*radius
        const centerX=0,centerY=ringYShift
        const ringX=centerX+Math.sin(angle)*radius,ringY=centerY-Math.sin(tilt)*depth,ringZ=Math.cos(tilt)*depth
        const old = this.previous[i]
        const facing = (1 + Math.cos(angle)) / 2
        const ringScale = .95 + .05 * facing
        const ringDof = .48 * (1 - facing) ** 2
        if(state.phase==='reveal') {
          // 手机从卷轴卷进来（有 seed）：直接从卷轴上的位置平滑走到环上的位置。
          // 原来两处会让卡先往左绕：① 偶数张路上被故意往左甩 45px、往上拱 60px（电脑版散卡用的弧线）；
          // ② 绕竖轴从 0 转到 ringAngle，第 15 张要转 337.5° 将近一整圈，中途翻到背面像先跑开再出来。
          // 手机：不加弧线，转角取最短的那一段（折回 (−π, π]，和 gather 里的 facingAngle 一样）。电脑版不变。
          const seeded=!!this.seed
          const bend=seeded?0:Math.sin(Math.PI*t)
          const ringRy=seeded?Math.atan2(Math.sin(angle),Math.cos(angle)):angle
          x=mix(old?.x??target.x,ringX,t)+bend*(i%2?45:-45)
          y=mix(old?.y??target.y,ringY,t)-bend*60
          z=mix(old?.z??target.z,ringZ,t);rx=mix(old?.rx??target.rx,tilt,t);ry=mix(old?.ry??target.ry,ringRy,t)
          rz = mix(old?.rz ?? target.rz, target.rz, t)
          scale = mix(old?.scale ?? 1, ringScale, t)
          // Keep the shadow's existing alpha, blur and contrast inputs continuous at the cut.
          const handoff = reduced ? 1 : ease(elapsed / .45)
          const ringAlpha = mix(.7, 1, reduced ? 1 : ease((elapsed - i * .03) / 2))
          alpha = mix(old?.alpha ?? alpha, ringAlpha, handoff)
          dof = mix(old?.dof ?? dof, ringDof, handoff)
          ghost = mix(old?.ghost ?? ghost, 0, handoff)
        } else if (this.row) {
          // 手机：环整体往下滑到底部，每张卡转正、落到转盘上同一张图的位置（第 i 张 → 第 i 格；后半圈的放到左边，
          // 第 8 格以外都在屏幕外），大小变成转盘卡的大小。缓入缓出，0.55 秒。
          const row=this.row,g=reduced?1:easeInOut(elapsed/row.seconds)
          const slot=i<RING_COUNT/2?i:i-RING_COUNT
          // 环一直在转，angle 会累积到好几圈；先折回 (−π, π]，转正时只转最短的那一段
          const facingAngle=Math.atan2(Math.sin(angle),Math.cos(angle))
          // 转盘本身是一条往下弯的弧（CircularGallery 的 bend）：两边的卡低一点、顺着弧转一点
          const sx=slot*row.spacing,reach=Math.min(Math.abs(sx),row.half,row.radius*.999)
          const sag=row.radius-Math.sqrt(row.radius*row.radius-reach*reach),turn=Math.sign(sx)*Math.asin(reach/row.radius)
          x=mix(ringX,sx,g);y=mix(ringY,row.y+sag,g);z=mix(ringZ,0,g)
          rx=mix(tilt,0,g);ry=mix(facingAngle,0,g);rz=mix(rz,turn,g)
          scale=mix(ringScale,row.scale,g);alpha=mix(1,row.alpha,g);dof=ringDof*(1-g);ghost=0
        } else {
          const g=reduced?1:ease(elapsed/1.1)
          x=mix(ringX,capsule.x,g);y=mix(ringY,capsule.y,g);z=mix(ringZ,0,g)
          rx=mix(tilt,0,g);ry=mix(angle,0,g);scale=ringScale*(1-g*.98);alpha=1*(1-g);dof=ringDof*(1-g);ghost=0
        }
      }
      const c = this.cards[i] ?? (this.cards[i] = {} as Card)
      const ringed = this.ringLayers && (state.phase === 'reveal' || state.phase === 'gather')
      Object.assign(c, { x, y, z, rx, ry, rz, scale, alpha, layer: ringed ? this.ringLayers![i % this.ringLayers!.length] : layers[i], dof, ghost })
    }
    this.cards.length = RING_COUNT
    if (state.phase === 'gather' && this.row) this.ringEnd = this.cards.map(c => ({ ...c }))
    return this.cards
  }
}

import type { Card } from '../scatter/renderer'
import { collectionPose, type Plan, type Pose } from '../scatter/choreo'
import type { IntroState } from './useIntroFlow'
import { RING_COUNT, RING_SECONDS, ringAngle } from './ringGeometry'
import { arc, platformLayout } from './motion'
import { StarField } from './starField'

const clamp = (n: number) => Math.max(0, Math.min(1, n))
const mix = (a: number, b: number, t: number) => a + (b - a) * t
const ease = (n: number) => { const t = clamp(n); return t * t * (3 - 2 * t) }
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

  draw(state: IntroState, W: number, H: number, now: number, dt: number, layers: number[], capsule: CapsulePose, reduced: boolean, home: Pose[], plan: Plan, collect: number[], current=state.active, starfield=false) {
    if (state.phase !== this.phase) {
      this.previous = this.cards.map(c => ({ ...c })); this.phase = state.phase; this.started = now; this.ejectOrigin = null; this.ejectStarted = null
      if (state.phase === 'reveal') this.ringClock = now + 1500
    }
    if(state.phase==='unlock'&&state.platformEntry==='cards'&&this.ejectStarted===null)this.ejectStarted=now
    const elapsed = (now - this.started) / 1000, mobile = W <= 700
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
        const u=clamp((elapsed-i*.03)/1.15)
        const t=reduced?1:u<.5?4*u*u*u:1-(-2*u+2)**3/2
        const angle=ringAngle(i)*Math.PI/180-Math.max(0,now-this.ringClock)/1000/RING_SECONDS*Math.PI*2
        const radius=W*.2,tilt=-8*Math.PI/180,depth=Math.cos(angle)*radius
        const centerX=0,centerY=0
        const ringX=centerX+Math.sin(angle)*radius,ringY=centerY-Math.sin(tilt)*depth,ringZ=Math.cos(tilt)*depth
        const old = this.previous[i]
        const facing = (1 + Math.cos(angle)) / 2
        const ringScale = .95 + .05 * facing
        const ringDof = .48 * (1 - facing) ** 2
        if(state.phase==='reveal') {
          const bend=Math.sin(Math.PI*t)
          x=mix(old?.x??target.x,ringX,t)+bend*(i%2?45:-45)
          y=mix(old?.y??target.y,ringY,t)-bend*60
          z=mix(old?.z??target.z,ringZ,t);rx=mix(old?.rx??target.rx,tilt,t);ry=mix(old?.ry??target.ry,angle,t)
          rz = mix(old?.rz ?? target.rz, target.rz, t)
          scale = mix(old?.scale ?? 1, ringScale, t)
          // Keep the shadow's existing alpha, blur and contrast inputs continuous at the cut.
          const handoff = reduced ? 1 : ease(elapsed / .45)
          const ringAlpha = mix(.7, 1, reduced ? 1 : ease((elapsed - i * .03) / 2))
          alpha = mix(old?.alpha ?? alpha, ringAlpha, handoff)
          dof = mix(old?.dof ?? dof, ringDof, handoff)
          ghost = mix(old?.ghost ?? ghost, 0, handoff)
        } else {
          const g=reduced?1:ease(elapsed/1.1)
          x=mix(ringX,capsule.x,g);y=mix(ringY,capsule.y,g);z=mix(ringZ,0,g)
          rx=mix(tilt,0,g);ry=mix(angle,0,g);scale=ringScale*(1-g*.98);alpha=1*(1-g);dof=ringDof*(1-g);ghost=0
        }
      }
      const c = this.cards[i] ?? (this.cards[i] = {} as Card)
      Object.assign(c, { x, y, z, rx, ry, rz, scale, alpha, layer: layers[i], dof, ghost })
    }
    this.cards.length = RING_COUNT
    return this.cards
  }
}

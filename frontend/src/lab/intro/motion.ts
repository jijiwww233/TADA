import { INTRO_SPRING } from './copy'

/** Over-damped, bounded spring. A target can settle, but it cannot bounce past its final value. */
export function quietSpring(value: number, velocity: number, target: number, dt: number): [number, number] {
  const side = value - target
  for (let i = 0, n = Math.max(1, Math.ceil(dt / .004)); i < n; i++) {
    const h = dt / n
    velocity += (-INTRO_SPRING.stiffness * (value - target) - INTRO_SPRING.damping * velocity) * h
    value += velocity * h
  }
  if ((value - target) * side < 0 || (Math.abs(value - target) < .015 && Math.abs(velocity) < .1)) return [target, 0]
  return [value, velocity]
}
/** Shared arc for the picker and the collection field; no active-platform offsets are applied to cards. */
export function arc(distance: number, radius: number, depth: number) {
  const theta = Math.max(-1.22, Math.min(1.22, distance))
  return { y: Math.sin(theta) * radius, x: (1 - Math.cos(theta)) * radius * .10, z: -(1 - Math.cos(theta)) * depth, angle: -theta }
}
export function platformLayout(W:number,H:number) {
 const mobile=W<=700,wheelHeight=H*.55,height=wheelHeight+204
 const top=(H-height)/2,wheelTop=116
 const left=W*(mobile?.05:.04),width=Math.min(400,mobile?W-(W*.09+30)*2:W*.34)
 return {top,height,wheelTop,wheelHeight,wheelCenter:top+wheelTop+wheelHeight/2,capTop:height-60,left,width}
}
export function wheelDistance(index:number,current:number) {
 let d=index-current
 while(d>3)d-=6
 while(d< -3)d+=6
 return d
}
export function updateWheel(root: HTMLElement | null, current: number, W: number, H: number) {
 const l=platformLayout(W,H),radius=l.wheelHeight*.62,depth=W<=700?90:180
 root?.querySelectorAll<HTMLElement>('[data-platform-index],[data-status-index]').forEach(row=>{
  const status=row.hasAttribute('data-status-index'),index=Number(status?row.dataset.statusIndex:row.dataset.platformIndex)
  const d=wheelDistance(index,current),a=arc(d*.34,radius,depth),scale=1/(1+Math.abs(d)*.17)
  row.style.transform=`translate3d(${status?0:a.x.toFixed(2)}px,calc(-50% + ${a.y.toFixed(2)}px),${a.z.toFixed(2)}px) rotateX(${a.angle.toFixed(4)}rad) scale(${scale.toFixed(4)})`
  row.style.opacity=String(Math.max(0,1-Math.abs(d)*.24))
  row.style.pointerEvents=status&&Math.abs(d)>.05?'none':'auto'
  const name=row.querySelector<HTMLElement>('.intro-platform-name')
  if(name){name.style.fontWeight=String(300+300*Math.max(0,1-Math.abs(d)));name.style.filter=Math.abs(d)<.5?'':Math.abs(d)<1.5?'blur(.5px)':'blur(1px)'}
 })
 root?.querySelectorAll<HTMLElement>('[data-ruler-index]').forEach(tick=>{
  const d=(Number(tick.dataset.rulerIndex)-15-(current-Math.round(current))*6)/6,a=arc(d*.34,radius,depth),scale=1/(1+Math.abs(d)*.17)
  tick.style.transform=`translate3d(${a.x.toFixed(2)}px,${a.y.toFixed(2)}px,${a.z.toFixed(2)}px) rotateX(${a.angle.toFixed(4)}rad) scale(${scale.toFixed(4)})`
  tick.style.opacity=String(Math.max(0,.55-Math.abs(d)*.14));tick.classList.toggle('is-current',Math.abs(d)<.17)
 })
}

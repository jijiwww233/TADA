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
/** 选平台页手机卷轴（右栏那叠卡）：每往外一档，下一张从上一张后面露出多少——
 *  REEL_SPREAD 越大越松（0.05 = 原来约露出 55%，0.25 ≈ 75%）；REEL_ARC 是弧线半径（卡高的倍数），
 *  越大越直，边缘的卡不会被圆弧推到屏幕右边缘外 */
export const REEL_SPREAD = .25
export const REEL_ARC = 7
export function updateWheel(root: HTMLElement | null, current: number, W: number, H: number) {
 // 电脑版：半径由轮盘高度决定；手机版：半径按屏高取一个能放下约 5 行的值，
 // 其余递减比例（scale 1/(1+|d|·.17)、透明度 1-|d|·.24、字重、模糊档位、刻度透明度）和电脑版同一套数值
 const mobile=W<=700
 const l=platformLayout(W,H),radius=mobile?Math.max(150,H*.34):l.wheelHeight*.62,depth=mobile?90:180
 root?.querySelectorAll<HTMLElement>('[data-platform-index],[data-status-index]').forEach(row=>{
  const status=row.hasAttribute('data-status-index'),index=Number(status?row.dataset.statusIndex:row.dataset.platformIndex)
  const d=wheelDistance(index,current),a=arc(d*.34,radius,depth)
  // 手机转盘行：选中行原来靠 .is-current 把字号从 30 跳到 36，拖过半格就「咔」一下变大；
  // 改成按离中心的距离连续放大（正中 1.2 倍 = 36/30），箭头和模糊也连续变化，拖动时整盘是顺滑的
  const phoneRow=row.classList.contains('intro-phone-wheel-row'),near=Math.max(0,1-Math.abs(d))
  const scale=1/(1+Math.abs(d)*.17)*(phoneRow?1+.2*near*near*(3-2*near):1)
  row.style.transform=`translate3d(${status?0:a.x.toFixed(2)}px,calc(-50% + ${a.y.toFixed(2)}px),${a.z.toFixed(2)}px) rotateX(${a.angle.toFixed(4)}rad) scale(${scale.toFixed(4)})`
  row.style.opacity=String(Math.max(0,1-Math.abs(d)*.24))
  row.style.pointerEvents=status&&Math.abs(d)>.05?'none':'auto'
  if(mobile&&!status)row.classList.toggle('is-current',Math.abs(d)<.5)
  const name=row.querySelector<HTMLElement>('.intro-platform-name,.intro-phone-wheel-name')
  if(name){name.style.fontWeight=String(300+300*near);name.style.filter=phoneRow?(Math.abs(d)<.3?'':`blur(${Math.min(1,(Math.abs(d)-.3)*.8).toFixed(2)}px)`):Math.abs(d)<.5?'':Math.abs(d)<1.5?'blur(.5px)':'blur(1px)'}
  const arrow=phoneRow?row.querySelector<HTMLElement>('.intro-current-arrow'):null
  if(arrow)arrow.style.opacity=Math.max(0,1-Math.abs(d)*2).toFixed(2)
 })
 root?.querySelectorAll<HTMLElement>('[data-ruler-index]').forEach(tick=>{
  const d=(Number(tick.dataset.rulerIndex)-15-(current-Math.round(current))*6)/6,a=arc(d*.34,radius,depth),scale=1/(1+Math.abs(d)*.17)
  tick.style.transform=`translate3d(${a.x.toFixed(2)}px,${a.y.toFixed(2)}px,${a.z.toFixed(2)}px) rotateX(${a.angle.toFixed(4)}rad) scale(${scale.toFixed(4)})`
  tick.style.opacity=String(Math.max(0,.55-Math.abs(d)*.14));tick.classList.toggle('is-current',Math.abs(d)<.17)
 })
 // 手机选平台页：右边是绕横轴转的卡片滚筒（和左边转盘同一个 current，拖动时按小数连续滚）。
 // 位置/转角/景深/模糊/透明度全部按「离中心几格」算：中间那张正对屏幕（rotateX 0、blur 0、opacity 1），
 // 上下依次后倒（translateZ 负）、变小、糊掉、变淡——就是电脑版那种「近实远虚」。
 // 档位见下面「第二版」注释：8 档逐步变化，格与格之间线性过渡（拖动时连续），超过 5 格隐藏。
 if(mobile){
  const reel=root?.querySelector<HTMLElement>('.intro-phone-reel')
  const track=reel?.querySelector<HTMLElement>('.intro-phone-reel-track')
  const imgs=track?.querySelectorAll<HTMLElement>('img')
  const first=imgs?.[0]
  if(reel&&track&&imgs&&first&&imgs.length){
   // 第二版（梯子 + 圆弧）：不再用「20°/格 的圆筒」反推位置（那样只有 3 档，第 1 格就从 1 掉到 .40，断崖）。
   // 改成逐档直接排：每往外一格，尺寸/透明度/模糊都只变一小步，下一张从上一张后面露出约 55% 的投影高度，
   // 一张压一张排成梯子；同时卡片中心沿一段圆弧往右收（x = R − √(R²−y²)），并按切线方向轻轻偏转，
   // 单帧就能看出是一段弧。中间那张不动：119×159、op 1、blur 0、rotate 0。
   // 档位（离中心 0..7 格）：
   //   尺寸 1 / .88 / .78 / .69 / .61 / .54 / .48 / .43
   //   透明度 1 / .9 / .75 / .6 / .45 / .3 / .18 / .1
   //   模糊 0 / 1 / 2 / 3.5 / 5 / 7 / 9 / 11 px
   //   后退 z 0 / −32 / −68 / −108 / −152 / −200 / −252 / −308（逐档加深，让相邻两张倾斜后的边在 3D 里也不会互相插穿）
   //   绕横轴 6°/格（最多 30°）
   // 390×844、卡高 159 时算出的投影中心距：86 / 158 / 217 / 265（第 4 格已在渐隐带里）；
   // 圆弧半径 R = 4.75 卡高 ≈ 756 → 往右收 5 / 17 / 32 / 48px，切线偏转取一半（第 3 格约 8°）。
   const h=first.offsetHeight||0
   const at=15+Math.max(0,Math.min(4,current))*3+1 // 每个平台 3 张卡，中间那张 = 选中平台的卡
   const PERSP=620 // = intro-phone.css 里 .intro-phone-reel 的 perspective
   const SIZE=[1,.88,.78,.69,.61,.54,.48,.43],FADE=[1,.9,.75,.6,.45,.3,.18,.1],BLUR=[0,1,2,3.5,5,7,9,11]
   const Z=[0,-32,-68,-108,-152,-200,-252,-308],TILT=Math.PI/30 // 6°/格
   const tilt=(a:number)=>Math.min(a*TILT,Math.PI/6)
   const proj=SIZE.map((s,a)=>h*s*Math.cos(tilt(a))*PERSP/(PERSP-Z[a]))
   const YS=[0];for(let a=1;a<SIZE.length;a++)YS[a]=YS[a-1]+proj[a-1]/2+proj[a]*REEL_SPREAD
   const RC=h*REEL_ARC
   const tier=(v:number[],a:number)=>{const i=Math.min(v.length-1,Math.floor(a));return i>=v.length-1?v[i]:v[i]+(v[i+1]-v[i])*(a-i)}
   imgs.forEach((img,i)=>{
    const d=i-at,a=Math.abs(d),sg=Math.sign(d)
    if(a>5){img.style.opacity='0';img.style.visibility='hidden';return}
    img.style.visibility=''
    const z=tier(Z,a),p=PERSP/(PERSP-z)
    const ys=sg*tier(YS,a),yy=Math.min(Math.abs(ys),RC*.95)
    const xs=RC-Math.sqrt(RC*RC-yy*yy)
    const rz=-Math.asin(sg*yy/RC)*.5,rx=-sg*tilt(a)
    // translate3d 的 x/y 在深度 z 处会被透视放大 p 倍，所以先除掉，让屏幕上的位置正好是 xs / ys
    img.style.transform=`translate(-50%,-50%) translate3d(${(xs/p).toFixed(1)}px,${(ys/p).toFixed(1)}px,${z.toFixed(1)}px) rotateZ(${rz.toFixed(4)}rad) rotateX(${rx.toFixed(4)}rad) scale(${tier(SIZE,a).toFixed(3)})`
    img.style.opacity=tier(FADE,a).toFixed(2)
    const blur=tier(BLUR,a)
    img.style.filter=blur<.05?'':`blur(${blur.toFixed(2)}px)`
    // 卡片底部投影和电脑版一样跟着景深走：近的那张投得实（offset/模糊/浓度都大），越远越浅越小。
    // 电脑版画布里每张卡下面那层 shadow 的浓度乘了 (1−ghost) 和 alpha²，远处几乎看不见；这里用同一趋势：
    // 两层：贴边的一层（0 6 11 -2 /.40）+ 散开的一层（0 18 34 -10 /.42），整体再乘 1/(1+a*.95)。
    // 所以中心卡最清楚的那张影子最实，上一张约 .21、再外约 .13，越远越浅。
    const sd=1+a*.95
    img.style.boxShadow=`0 ${(6/(1+a*.55)).toFixed(1)}px ${(11/(1+a*.5)).toFixed(1)}px -2px rgb(56 40 30 / ${(.40/sd).toFixed(3)}), 0 ${(18/(1+a*.55)).toFixed(1)}px ${(34/(1+a*.5)).toFixed(1)}px -${(10/(1+a*.45)).toFixed(1)}px rgb(56 40 30 / ${(.42/sd).toFixed(3)})`
   })
  }
 }
}

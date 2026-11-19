import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useReducedMotion } from 'motion/react'
import { oklchToHex, take, useImages } from '../data'
import { Capsule } from '../Capsule'
import { useIntroFlow } from '../intro/useIntroFlow'
import { FlowScene, capsuleTarget, type CapsulePose } from '../intro/flowScene'
import { IntroPanels, introPresentation } from '../intro/IntroPanels'
import { DEMO_MS, INTRO_COPY, PLATFORMS } from '../intro/copy'
import { quietSpring, updateWheel, platformLayout } from '../intro/motion'
import '../intro/intro.css'
import '../intro/fable.css'
import { COVER_FRONT, INITIAL_CARD_COUNT, MOBILE_INITIAL_CARD_COUNT, PHONE_INITIAL_CARD_COUNT, PHONE_WALL_COUNT, type HomeDirection } from './homePlans'
import { HomeHeadline } from './HomeHeadline'
import { SoftText } from '../intro/SoftText'
import { REFINE } from '../../main/refine'
import '../intro/intro-refine.css'
import '../intro/intro-phone.css'
import { ScatterRenderer, type Card, type Strip } from './renderer'
import { cardThumbnailUrl, loadImage } from '../intro/cardThumbnail'
import { galleryLayers, galleryRow, seedFromReel, snapReel, type ReelShot } from '../intro/phoneHandoff'

/** 手机整理结果页：胶囊中心在舞台中线下方多少 px（推导见 tick 里 target.y 的注释；和 intro-phone.css 的 --phone-result-top 成对） */
const PHONE_RESULT_CAP_Y = 103.4
import { collectLength, frame, matrixSettledProgress, P_COLLECT, P_NEAT, P_START, PLAN_B, type Bounce, type Frame, type Layout, type Plan, type Pool, type Pose } from './choreo'

/** 手机平台转盘的吸附弹簧：临界阻尼，ω = 9.5（约 0.5 秒停稳），比电脑版 quietSpring（ω≈14.5）软 */
const PHONE_WHEEL_OMEGA = 9.5
function phoneWheelSpring(value: number, velocity: number, target: number, dt: number): [number, number] {
  for (let i = 0, n = Math.max(1, Math.ceil(dt / .004)); i < n; i++) {
    const h = dt / n
    velocity += (-PHONE_WHEEL_OMEGA * PHONE_WHEEL_OMEGA * (value - target) - 2 * PHONE_WHEEL_OMEGA * velocity) * h
    value += velocity * h
  }
  if (Math.abs(value - target) < .002 && Math.abs(velocity) < .02) return [target, 0]
  return [value, velocity]
}

/** 手机点「开始」后：散卡排成墙 1.5 秒（电脑版自动播放 3.6 秒）、排好停 0.45 秒、接住后 0.5 秒进 Key 页 */
const PHONE_ARRANGE_MS = 1500
const PHONE_MATRIX_HOLD_MS = 450
const PHONE_CATCH_MS = 500

const OPENING_MS = 750
export const TYPING = '那张参考图……到底收藏在哪了'
/** Stage paper = the page colour (warm white), as linear-ish 0..1 RGB for the renderer. */
const PAPER = oklchToHex(0.984, 0.0045, 85)
const rgb01 = (hex: string) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number]

/**
 * Pinned scroll stage: the section is 420vh tall, the inner screen sticks; scroll progress drives the
 * choreography, the collect into the capsule runs on its own clock (staggered, reversible).
 * `controls` render inside the pinned screen (plan / bounce switches).
 */
export default function ScatterStage({ plan, bounce, reduced, controls, direction, english, clean = false, phone = false, autoplay = false, startPage }: { plan: Plan; bounce: Bounce; reduced: boolean; controls?: ReactNode; direction: HomeDirection; english: boolean; /** /intro：不带调试开关的干净舞台 */ clean?: boolean; phone?: boolean; /** 进来就自动从头播放 */ autoplay?: boolean; /** 直接从某一页开始（排练用） */ startPage?: 'key' | 'platforms' | 'ready' }) {
  const { items, cards: galleryCards } = useImages()
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const capsule = useRef<HTMLDivElement>(null)
  const labels = useRef<HTMLDivElement>(null)
  const foreground = useRef<HTMLDivElement>(null)
  const focusVeil = useRef<HTMLDivElement>(null)
  const headlineDoneAt = useRef<number | null>(null)
  const bounceRef = useRef(bounce)
  bounceRef.current = bounce
  const [error, setError] = useState<string | null>(null)
  /** WebGL 不可用 / 纹理失败时的普通图片兜底；绝不留在黑块上 */
  const [fallback, setFallback] = useState(false)
  const [homeQuery, setHomeQuery] = useState('')
  const [thumbs, setThumbs] = useState<string[]>(() => items.map(item => item.src))
  const [starfield,setStarfield]=useState(true)
  const starfieldRef=useRef(starfield);starfieldRef.current=starfield
  const [topbar,setTopbar]=useState<'A'|'B'|'C'>('C')
  const [typed, setTyped] = useState(0) // remount key for the typing line once the stage is on screen
  const systemReduced = useReducedMotion()
  const still = reduced || !!systemReduced
  const [headlineReady,setHeadlineReady]=useState(false)
  useEffect(()=>{
    setHeadlineReady(false)
    if(!typed)return
    const timer=setTimeout(()=>setHeadlineReady(true),still?0:500)
    return()=>clearTimeout(timer)
  },[typed,still])
  // WebGL 兜底时跳过开场近焦：取消 blur 动画并把标题直接显示出来，否则普通图片也会被糊住。
  useEffect(() => {
    if (!fallback) return
    const veil = focusVeil.current
    if (veil) {
      veil.getAnimations().forEach(animation => animation.cancel())
      veil.style.filter = 'none'
    }
    setTyped(1)
    setHeadlineReady(true)
  }, [fallback])
  const flow = useIntroFlow(still, phone)
  useEffect(() => {
    // 手机用不到 thumbs（兜底图直接用原图），别在一打开就把所有图都拉一遍
    if (phone) return
    let live = true
    Promise.all(items.map(item => cardThumbnailUrl(item.src))).then(urls => {
      if (live) setThumbs(urls)
    }, () => {
      // 缩略图都生成不了时也不要黑块：保留原图地址，WebGL 失败会切到普通图片。
      if (live) setThumbs(items.map(item => item.src))
    })
    return () => { live = false }
  }, [items, phone])
  const flowRef = useRef(flow); flowRef.current = flow
  /** 手机第一页的胶囊只是展示：不能输入，箭头点了也没反应；只有最后一页的胶囊能搜 */
  const phoneHomeCapsule = phone && flow.state.phase === 'home'
  /** 手机点「开始」：先播一遍电脑版「卡片排好 → 收进胶囊」，播完再进 Key 页（值 = 点下去的时刻，0 = 没点） */
  const startTap = useRef(0)
  const [starting, setStarting] = useState(false)
  useEffect(() => { startTap.current = 0; setStarting(false) }, [flow.state.run])
  const start = () => {
    if (!phone || still) { flow.enterKey(); return }
    if (startTap.current) return
    startTap.current = performance.now(); setStarting(true)
  }
  /** 手机选平台页：转盘的连续位置（拖动时每帧写入，松手后由弹簧回吸到整数档） */
  const phoneDrag = useRef({ dragging: false, current: 0, startY: 0, startCurrent: 0, velocity: 0, lastT: 0, moved: false, suppressClick: false, release: null as number | null })
  useEffect(() => { if (startPage) flowRef.current.jump(startPage); else if (autoplay) flowRef.current.setDemo(true) }, [autoplay, startPage])
  useEffect(() => {
    if (flow.state.phase === 'key' || flow.state.run > 0) wrap.current?.scrollIntoView({ block: 'start', behavior: 'instant' })
  }, [flow.state.run, flow.state.phase])
  /** 首页示例搜索框：输入后直接进素材库搜索；空着点箭头就进素材库。 */
  const goSearch = () => {
    const params = new URLSearchParams()
    const mode = new URLSearchParams(location.search).get('data')
    if (mode) params.set('data', mode)
    if (!REFINE) params.set('refine', '0')
    const query = homeQuery.trim()
    if (query) params.set('q', query)
    window.location.href = '/main' + (params.size ? '?' + params.toString() : '')
  }
  useEffect(() => {
    if (phone) return
    const el = wrap.current
    let lastInput=0,lockedUntil=0,amount=0,used=false,run=-1
    const wheel=(e:WheelEvent)=>{
      const f=flowRef.current
      if(e.target instanceof Element&&e.target.closest('.intro-topbar'))return
      if(f.state.phase==='home'||f.state.phase==='ready')return
      e.preventDefault()
      if(f.state.phase!=='platforms')return
      const now=performance.now()
      if(run!==f.state.run){run=f.state.run;used=false;amount=0;lockedUntil=0;lastInput=0}
      if(now-lastInput>220&&now>=lockedUntil){used=false;amount=0}
      lastInput=now
      if(used||now<lockedUntil)return
      amount+=e.deltaY*(e.deltaMode===1?18:1)
      if(Math.abs(amount)>=65){
        const next=amount>0?1:-1
        if(f.state.demo)f.setDemo(false)
        f.select(Math.max(0,Math.min(5,f.state.active+next)))
        amount=0;used=true;lockedUntil=now+350
      }
    }
    el?.addEventListener('wheel', wheel, { passive: false })
    return () => el?.removeEventListener('wheel', wheel)
  }, [phone])

  // 手机选平台页：在左栏上下拖 = 转动转盘。iOS 选择器手感：拖动时连续跟手（不按格跳），
  // 松手后按甩的速度带一段惯性（最多 3 格），再由 tick 里的柔和弹簧平滑吸附到最近一格
  useEffect(() => {
    if (!phone) return
    const root = wrap.current
    if (!root) return
    const d = phoneDrag.current
    const step = () => Math.max(54, (root.clientHeight || 844) * .09)
    const last = PLATFORMS.length
    const clamp = (n: number) => Math.max(0, Math.min(last, n))
    // 拖出两头：橡皮筋，越拉越拉不动，最多多出 0.35 格
    const rubber = (n: number) => {
      const over = n < 0 ? n : n > last ? n - last : 0
      return clamp(n) + Math.sign(over) * .35 * (1 - 1 / (1 + Math.abs(over) * 1.4))
    }
    let surface: HTMLElement | null = null
    const down = (e: PointerEvent) => {
      const target = e.target as Element | null
      const wheel = target?.closest?.('.intro-phone-wheel') as HTMLElement | null
      if (!wheel || target?.closest?.('.intro-connect, .intro-link')) return
      if (flowRef.current.state.phase !== 'platforms') return
      surface = wheel
      d.dragging = true; d.moved = false; d.release = null
      d.startY = e.clientY; d.lastT = performance.now(); d.velocity = 0
      // 起点 = 转盘此刻的连续位置：还在惯性滑动时按住就地停住，接着拖（不会先跳回某一格）
      d.startCurrent = d.current
      wheel.classList.add('is-dragging')
    }
    const move = (e: PointerEvent) => {
      if (!d.dragging) return
      const dy = e.clientY - d.startY
      if (Math.abs(dy) > 4) d.moved = true
      const now = performance.now(), dt = Math.max(1, now - d.lastT)
      const previous = d.current
      // 完全跟手：手指走一格的距离，转盘就走一格（小数连续变化）
      d.current = rubber(d.startCurrent - dy / step())
      // 速度（格/秒）做一点平滑，避免最后一个事件的抖动决定惯性
      const instant = (d.current - previous) / (dt / 1000)
      d.velocity = dt > 100 ? instant : d.velocity * .5 + instant * .5
      d.lastT = now
      if (e.cancelable) e.preventDefault()
    }
    const up = (e: PointerEvent) => {
      if (!d.dragging) return
      d.dragging = false
      surface?.classList.remove('is-dragging')
      if (!d.moved) {
        // 轻点：按点击位置找最近的一行（3D 旋转后的行用包围盒中心近似）
        const rows = Array.from(root.querySelectorAll<HTMLElement>('.intro-phone-wheel-row'))
        let best = { index: -1, distance: 1e9 }
        for (const row of rows) {
          const box = row.getBoundingClientRect()
          const distance = Math.abs((box.top + box.bottom) / 2 - e.clientY)
          if (distance < best.distance) best = { index: Number(row.dataset.platformIndex), distance }
        }
        if (best.index >= 0) {
          d.suppressClick = true
          setTimeout(() => { d.suppressClick = false }, 400)
          if (flowRef.current.state.demo) flowRef.current.setDemo(false)
          flowRef.current.select(best.index)
        }
        return
      }
      // 惯性：按松手时的速度往前多滑一段（速度 × 0.3 秒），落点取最近的整格；
      // 快甩最多比松手处多走 3 格，不会飞走。手指停住超过 0.1 秒再松手 = 没有惯性，就近吸附。
      const v = performance.now() - d.lastT > 100 ? 0 : d.velocity
      const from = clamp(d.current)
      const reach = Math.max(-3, Math.min(3, v * .3))
      const target = clamp(Math.round(from + reach))
      if (flowRef.current.state.demo) flowRef.current.setDemo(false)
      flowRef.current.select(target)
      d.suppressClick = true
      setTimeout(() => { d.suppressClick = false }, 400)
      // 松手速度交给 tick 里的弹簧当初速度：转盘接着往前滑、慢慢减速停到 target（不是从 0 速度重新起步）
      d.release = v
    }
    const click = (e: MouseEvent) => {
      if (!d.suppressClick) return
      d.suppressClick = false
      e.preventDefault(); e.stopPropagation()
    }
    root.addEventListener('pointerdown', down)
    window.addEventListener('pointermove', move, { passive: false })
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
    root.addEventListener('click', click, true)
    return () => {
      root.removeEventListener('pointerdown', down)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
      root.removeEventListener('click', click, true)
      d.dragging = false
    }
  }, [phone])

  useEffect(() => {
    const el = canvas.current, box = wrap.current
    if (!el || !box || !items.length) return
    let r: ScatterRenderer
    setFallback(false)
    // texture layers = items; the portrait is handed to the plan separately so it can place it (or leave it out)
    const face = items.findIndex(it => it.face)
    const pool: Pool = { cards: items.flatMap((it, i) => (it.face ? [] : [i])), face: face >= 0 ? face : null }
    // 手机：一打开只等第一屏那 7 张卡的图，其余的等第一屏画出来以后在后台补（层号只取决于 pool，跟屏幕尺寸无关）
    const firstLayers = phone ? plan.layout(1000, 1000, pool, true).layers.slice(0, PHONE_INITIAL_CARD_COUNT) : undefined
    try { r = new ScatterRenderer(el, items.map(i => i.src), firstLayers); setError(null) }
    catch (e) { setError(String(e)); setFallback(true); return }
    r.bg = rgb01(PAPER)
    const contextLost = (event: Event) => { event.preventDefault(); setFallback(true); setError('WebGL 上下文丢失') }
    el.addEventListener('webglcontextlost', contextLost)

    let W = 0, H = 0, k = 1
    let lay: Layout = { scat: [], grid: [], order: [], collect: [], layers: [] }
    let strips = plan.strips(1, 1)
    const out: Frame = { cards: [] as Card[], strips: [] as Strip[], capsuleY: 0, neat: 0 }
    let settledProgress = P_NEAT
    const layout = () => {
      W = el.clientWidth; H = el.clientHeight
      // one scale for the whole composition, so the neat grid keeps its margins on smaller screens
      k = W <= 700 ? Math.min(W / 700, H / 1100) : Math.min(W / 1600, H / 900, 1.15)
      const sc = (p: Pose): Pose => ({ ...p, x: p.x * k, y: p.y * k, z: p.z * k })
      const l = plan.layout(W / k, H / k, pool, W <= 700)
      // 手机引导首屏：homePlans 按 (1100−z)/1100 预先抵消透视，但上面把 z 也乘了 k，渲染时实际透视是 (1100−kz)/1100，
      // 两者不相消，远处的卡会被往外推（k≈0.56 时 z≈−560 的卡偏出 18%，中间一行右边那张整张出屏）。这里补回差值，
      // 让散卡落在 PHONE_SCATTER 写的屏幕位置上；卡片大小（z）不变。电脑版不走这里。
      const fit = (p: Pose): Pose => { const f = (1100 - k * p.z) / (1100 - p.z); return { ...p, x: p.x * f, y: p.y * f } }
      lay = { ...l, scat: (phone ? l.scat.map(fit) : l.scat).map(sc), grid: l.grid.map(sc) }
      settledProgress = matrixSettledProgress(lay)
      strips = plan.strips(W, H)
      r.cardW = plan.cardW * k; r.cardH = plan.cardH * k; r.radius = 10 * k
      r.resize(W, H, Math.min(devicePixelRatio || 1, W <= 700 ? 1.5 : 2))
      const group=platformLayout(W,H)
      const vars:Record<string,number>={'--platform-group-top':group.top,'--platform-group-height':group.height,'--platform-wheel-top':group.wheelTop,'--platform-wheel-height':group.wheelHeight,'--platform-cap-top':group.capTop,'--platform-ruler-left':group.left,'--platform-list-left':group.left+30,'--platform-text-left':group.left,'--platform-cap-width':group.width,'--platform-actions-left':group.left+group.width}
      Object.entries(vars).forEach(([key,value])=>box.style.setProperty(key,`${value}px`))
      dirty = true
    }

    let P = 0, clock = 0, dirty = true, visible = false, raf = 0, last = performance.now()
    let focusStarted=-1, focusFinished=false, focusAnimation:Animation|undefined, resourcesReady=false
    let scene = new FlowScene(), observedRun = -1, matrixAt = 0, caughtAt = 0, wheelCurrent = 0, wheelVelocity = 0, wheelPhase = ''
    // 手机 P2：卷轴最后一帧的位置、环/转盘共用的 16 张图、环上卡宽（电脑 0.2W 半径时卡宽:半径 = .46，手机半径 .412W）
    let reelShots = new Map<number, ReelShot>()
    const ringLayers = phone ? galleryLayers(items, galleryCards) : null
    const ringCardW = () => .46 * W * .412
    const pose: CapsulePose = { x: 0, y: 0, width: 620, height: 60, radius: 30 }
    const velocity: CapsulePose = { x: 0, y: 0, width: 0, height: 0, radius: 0 }
    // capsule spring (q): swell = scale offset; squash = dip / squash amount
    let q = 0, qv = 0, bounced = false
    // 收完之后胶囊从墙下面升回正中（0..1），往回滚再落回去
    let rise = 0
    let startP = 0
    const progress = () => {
      const rect = box.getBoundingClientRect()
      return Math.min(1, Math.max(0, -rect.top / Math.max(1, box.offsetHeight - innerHeight)))
    }

    const tick = (now: number) => {
      raf = 0
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      let moving = false
      const f = flowRef.current, state = f.state
      if (state.run !== observedRun) {
        observedRun = state.run; rise = 0; startP = 0; headlineDoneAt.current = null; focusStarted=-1;focusFinished=false;focusAnimation?.cancel();if(focusVeil.current)focusVeil.current.style.filter='';setTyped(0); P = 0; clock = 0; q = 0; qv = 0; bounced = false; matrixAt = 0; caughtAt = 0; dirty = true; scene = new FlowScene()
      }
      if (state.phase !== 'home') {
        if(focusAnimation){focusAnimation.cancel();focusAnimation=undefined}
        q = 0; qv = 0 // the home catch is the only under-damped motion in this page
        const target = capsuleTarget(state, W, H, f.advanced)
        if (phone && (state.phase === 'reveal' || state.phase === 'gather')) {
          // 整理结果页这一组的胶囊中心，必须和 intro-phone.css 的 --phone-result-top（= 24.37%H + 196）同步：
          // 环顶 170、环底 309.5（最靠下的零星像素到 321.5），胶囊顶取 321.5 + 50 = 371.7 → 中心 401.7（844 高）。
          // 这里同时是画布里 gather 的收进口：不改的话卡片会朝原来的 510.6（旧的胶囊位置）收，从现在的胶囊下面钻过去。
          // 第二版：胶囊再下移 40（顶 411.7），离环底 90.2 / 102.2；和 CSS 的 24.37% + 236px 成对改
          // 第三版（P2-8）：「环 + 胶囊 + 说明」整组在屏幕上垂直居中，组内距离不变。
          //   原来整组：环顶 0.2437H − 36.8，说明底 ≈ 胶囊中心 + 30 + 16 + 一行字 20 = 0.2437H + 302 → 组中心 0.2437H + 132.6
          //   要放到 H/2，整组下移 D = 0.2563H − 132.6（844 高时 83.7）
          //   → 胶囊中心 = H(.2437 − .5) + 236 + D = 中线下 103.4（和 H 无关）；环心 = −.2168H + D = .0395H − 132.6
          //   844 高时：环顶 253.7、胶囊 495.4–555.4、说明顶 571.4、说明底约 591 → 组中心 422.5 ≈ 422
          // 和 intro-phone.css 的 --phone-result-top: calc(50% + 103.4px) 成对改
          target.y = PHONE_RESULT_CAP_Y
        }
        if(state.phase==='ready'){const y=parseFloat(box.style.getPropertyValue('--intro-ready-cap-y'));if(Number.isFinite(y))target.y=y}
        for (const axis of ['x', 'y', 'width', 'height', 'radius'] as const) {
          if (still) { pose[axis] = target[axis]; velocity[axis] = 0 }
          else [pose[axis], velocity[axis]] = quietSpring(pose[axis], velocity[axis], target[axis], state.phase==='unlock'?dt/1.3:dt)
        }
        const drag = phoneDrag.current
        // 手机：刚进选平台页时转盘直接停在当前那格（上一轮停在「下一步」时，wheelCurrent 还留着 5，会自己滑一大段回来）
        if (phone && state.phase === 'platforms' && wheelPhase !== 'platforms') { wheelCurrent = state.active; wheelVelocity = 0; drag.current = state.active }
        // 手机 P2-7：选平台页一离开，就用卷轴最后一帧的位置给环上 16 张卡当起点（卷轴 DOM 这时已经卸掉了，所以用上一帧存的）
        // （手机 clearing 是 0 毫秒，这一帧可能已经直接是 reveal 了，所以两种都认）
        if (phone && (state.phase === 'clearing' || state.phase === 'reveal') && wheelPhase === 'platforms') scene.seed = seedFromReel(reelShots, ringLayers ?? [], ringCardW())
        if (phone) { scene.ringLayers = ringLayers; scene.row = galleryRow(W, H, ringCardW()) }
        wheelPhase = state.phase
        if (phone && drag.dragging) { wheelCurrent = drag.current; wheelVelocity = 0 }
        else if (still) { wheelCurrent = state.active; wheelVelocity = 0 }
        else if (phone) {
          // 手机：松手那一刻接上手指的速度，再用一个更软的临界阻尼弹簧（约 0.5 秒停稳、不回弹）吸附到目标格
          if (drag.release !== null) {
            const gap = state.active - wheelCurrent
            // 初速度不超过 ω·距离：临界阻尼下这样不会冲过目标再弹回来
            wheelVelocity = Math.sign(drag.release) === Math.sign(gap) ? Math.sign(gap) * Math.min(Math.abs(drag.release), PHONE_WHEEL_OMEGA * Math.abs(gap) * .95) : 0
            drag.release = null
          }
          [wheelCurrent, wheelVelocity] = phoneWheelSpring(wheelCurrent, wheelVelocity, state.active, dt)
        }
        else [wheelCurrent, wheelVelocity] = quietSpring(wheelCurrent, wheelVelocity, state.active, dt)
        if (phone && state.phase === 'platforms' && !drag.dragging) drag.current = wheelCurrent
        updateWheel(wrap.current, wheelCurrent, W, H)
        if (phone && state.phase === 'platforms') reelShots = snapReel(wrap.current, el)
        r.cardW = plan.cardW * k; r.cardH = plan.cardH * k
        // 手机整理结果页（重做）：电脑版 1440 宽时环半径 0.2W = 288、卡宽 133（卡宽:环半径 = .46，每张卡占弧长 113 ≈ .85 卡宽）。
        // 手机 390 按同一个「卡宽:环半径」比例放大：环半径 .412W ≈ 160.6（实测卡片像素左右边界 28.0 ~ 363.5 = 335.5，屏宽 86%），
        // 卡宽跟着同比例 = .46 × 160.6 ≈ 73.9（原来的 .78 倍是 64.3，环变宽后就不合比例了）。
        // 环心从 -H*.07 上移到 -H*.2168，整圈最高像素落在 y = 170（实测，见 dsh-tmp/p3c/measure.cjs + scan2.py）。
        // 卷轴 → 环（clearing）和 环 → 转盘（ready 开头那 0.4 秒）也用环上的卡宽，起点/终点的缩放都是按它算的
        const phoneRing = phone && ['clearing', 'reveal', 'gather', 'ready'].includes(state.phase)
        if (phoneRing) { r.cardW = ringCardW(); r.cardH = r.cardW * plan.cardH / plan.cardW }
        // 环心：P2-8 整组垂直居中后 = .0395H − 132.6（原来 −.2168H），推导见上面 target.y 的注释。
        // 实测环低了约 15px，常数改成 −150（环上移 17.4px，844 高时环顶约 236.3）；胶囊和说明文字不动
        // （所以环底到胶囊的距离从 90.2 / 102.2 变成约 107.6 / 119.6）
        // 再改（以这一条为准，上面「−150」那条作废）：以 −132.6 那版为基准，环底到胶囊的空白减半 →
        // 90.2 / 102.2 的一半约 45 / 51，环比那版下移 44.6，常数 −132.6 → −88；
        // 844 高时环顶约 298.3，环底到胶囊约 45.6（零星像素）/ 57.6（卡片主体），胶囊和说明仍不动
        // 手机选平台页画布是藏着的：画空白，免得卷轴卸掉、画布刚露出来的那一帧闪一下旧的散卡
        if (phone && (state.phase === 'platforms' || state.phase === 'unlock')) r.draw([], [])
        else r.draw(scene.draw(state, W, H, now, dt, lay.layers, pose, still, lay.scat, plan, lay.collect,
          wheelCurrent, starfieldRef.current, phone ? (phoneRing ? H * .0395 - 88 : -H * .075) : 0, phoneRing ? .412 : .2), [])
        if (capsule.current) {
          capsule.current.style.width = `${Math.min(W * .96, pose.width).toFixed(2)}px`
          capsule.current.style.transform = `translate(calc(-50% + ${pose.x.toFixed(2)}px), calc(-50% + ${pose.y.toFixed(2)}px))`
          const surface = capsule.current.querySelector<HTMLElement>('.capsule')
          if (surface) { surface.style.height = `${pose.height.toFixed(2)}px`; surface.style.borderRadius = `${pose.radius.toFixed(2)}px` }
        }
        foreground.current?.querySelectorAll('img').forEach(img => { img.style.opacity = '0' })
        if (visible) raf = requestAnimationFrame(tick)
        return
      }
      const mobile = W <= 700
      const openingDuration = mobile ? OPENING_MS * .65 : OPENING_MS
      if(visible&&resourcesReady&&focusStarted<0){
        focusStarted=now
        if(!still && !phone)focusAnimation=focusVeil.current?.animate([
          {filter:mobile?'blur(22px)':'blur(40px)',offset:0},
          {filter:mobile?'blur(22px)':'blur(40px)',offset:1500/6500},
          {filter:'blur(0px)',offset:1},
        ],{duration:openingDuration,easing:'linear',fill:'both'})
      }
      const focus=still||phone?1:focusStarted<0?0:Math.min(1,Math.max(0,(now-focusStarted)/openingDuration))
      if(focus===1&&!focusFinished){
        focusFinished=true;moving=true;setTyped(1)
        if(focusVeil.current)focusVeil.current.style.filter='none'
        focusAnimation?.cancel()
      }
      // 手机标题不打字、一挂上就算写完；但「自动播放 / 重播」换 run 时标题不会重新挂载，onDone 不再触发，
      // 之前自动播放就卡在这里（sinceTitle 永远是 0，卡片不排）。手机上直接从这一帧起算。
      if (phone && headlineDoneAt.current === null) headlineDoneAt.current = now
      const sinceTitle = headlineDoneAt.current === null ? 0 : now - headlineDoneAt.current
      const L = collectLength(plan, lay)
      const b = bounceRef.current
      // 手机点了「开始」：和自动播放走同一段「排好 → 收进胶囊」，只是从点下去那一刻起算、不等标题
      const tapped = phone && startTap.current > 0
      const auto = state.demo || tapped
      if (!still) {
        let p = 0
        if (auto) {
          // Start the hold only once the smoothed matrix has settled.
          if (P >= settledProgress && !matrixAt) matrixAt = now
          const arrangeElapsed = state.demo ? sinceTitle - DEMO_MS.homeTitleHold : now - startTap.current
          // 手机：原来「开始 → 排成墙」用电脑版自动播放的 3.6 秒，慢悠悠的；手机改 1.5 秒排好、停 0.45 秒再收
          if (matrixAt) p = now - matrixAt >= (phone ? PHONE_MATRIX_HOLD_MS : DEMO_MS.matrixHold) ? .96 : P_NEAT
          else if (arrangeElapsed > 0) {
            // The scroll choreography is static before P_START.
            P = Math.max(P, P_START)
            p = Math.min(P_NEAT, P_START + arrangeElapsed / (phone ? PHONE_ARRANGE_MS : DEMO_MS.homeArrange))
          }
        } else if (focus === 1) {
          p = progress()
        }
        // Scrub smoothing lets fast wheel flicks still read as continuous motion.
        const matrixIsSettled = auto && matrixAt > 0
        const nP = matrixIsSettled || Math.abs(p - P) < 1e-4
          ? p
          : P + (p - P) * (1 - Math.exp(-dt / (auto ? (phone ? 0.16 : 0.28) : 0.1))) // 手动滚动要跟手；自动播放保留原来的柔和（手机跟得紧一点，不拖尾）
        if (nP !== P) { P = nP; moving = true }
        const dir = P >= P_COLLECT ? 1 : P < P_COLLECT - 0.03 ? -1 : 0
        const nc = Math.min(L, Math.max(0, clock + dir * dt * (dir < 0 ? 1.6 : 1)))
        if (nc !== clock) { clock = nc; moving = true }
        // the last card is in: kick the spring (v0 = amplitude · ω)
        if (clock >= L && !bounced) { qv = b.amp * Math.sqrt(b.stiffness); bounced = true }
        if (clock < L * 0.5) bounced = false
        if (qv !== 0 || q !== 0) {
          // semi-implicit Euler in small steps: soft springs ring for ~1.5s, keep them stable at any frame rate
          for (let s = 0, n = Math.ceil(dt / 0.004); s < n; s++) {
            const h = dt / n
            qv += (-b.stiffness * q - b.damping * qv) * h
            q += qv * h
          }
          if (Math.abs(q) < 1e-4 * b.amp * 20 && Math.abs(qv) < 1e-3 * b.amp * 20) { q = 0; qv = 0 }
          moving = true
        }
      } else if (state.demo && sinceTitle >= DEMO_MS.homeTitleHold) { P = .96; clock = L; bounced = true; moving = true }
      // 自动播放时不升：胶囊马上就要展开成钥匙卡片，先升再变是多余的一跳（“开始”也会跟着窜上去）
      const nr = Math.min(1, Math.max(0, rise + (!auto && clock >= L && caughtAt && now - caughtAt > 260 ? dt / .7 : clock < L || auto ? -dt / .35 : 0)))
      if (nr !== rise) { rise = still && clock >= L ? 1 : nr; moving = true }
      // 按钮自带的 150ms CSS 过渡和这里的逐帧更新会打架（先拖在后面慢慢挪，最后猛地窜到位），所以在标签上关掉了过渡
      // “开始”有自己的时钟：胶囊停稳以后才慢慢浮上来（约 0.9 秒），往回滚时很快收掉
      const ns = Math.min(1, Math.max(0, startP + (!auto && rise >= 1 && clock >= L ? dt / .9 : -dt / .2)))
      if (ns !== startP) { startP = ns; moving = true }
      if (clock >= L && P > P_COLLECT) {
        if (!caughtAt) caughtAt = now
        // 手机：胶囊接住最后一张卡、回弹停下后（约 0.5 秒）就进 Key 页；enterKey 在手机上先播 0.26 秒的退场再换页
        if (auto && (still || now - caughtAt > (phone ? PHONE_CATCH_MS : DEMO_MS.homeCatch))) f.enterKey()
      } else caughtAt = 0
      // Reduced motion otherwise keeps the first composition readable, without the 420vh travel.
      if (moving || dirty) {
        // 手机首屏的胶囊被 intro-phone.css 钉在 top: 73%（不跟 cy 走），卡片要收进这个位置，所以收进口用 .23H
        frame(plan, H, lay, strips, P, clock, out, phone ? .23 : undefined)
        // Preserve 9bc3f96's first-screen size/count; B's size and four extra cards belong to the wall only.
        r.cardW = (plan.cardW + (PLAN_B.cardW - plan.cardW) * out.neat) * k
        r.cardH = (plan.cardH + (PLAN_B.cardH - plan.cardH) * out.neat) * k
        if (mobile) {
          // 手机引导首屏：散开时卡片按电脑比例换算（平均宽约 93px，是原来 0.78 倍时的 4/3），排成墙时回到原来的 0.78
          const scale = phone ? 1.27 + (.78 - 1.27) * out.neat : .9
          r.cardW *= scale
          r.cardH *= scale
        }
        const enter = Math.min(1, Math.max(0, (P - 0.06) / 0.25))
        for (let i = mobile ? (phone ? PHONE_INITIAL_CARD_COUNT : MOBILE_INITIAL_CARD_COUNT) : INITIAL_CARD_COUNT; i < out.cards.length; i++) {
          out.cards[i].alpha *= enter
          out.cards[i].scale *= enter
        }
        // 手机图片墙只要 12 张（homePlans 的 PHONE_WALL_COUNT）：第 13 张以后一直不画
        if (phone) for (let i = PHONE_WALL_COUNT; i < out.cards.length; i++) { out.cards[i].alpha = 0; out.cards[i].scale = 0 }
        const cx = (mobile ? 0 : direction === 'premotio' ? W * .17 : -W * .23) * (1 - out.neat)
        const lift = rise * rise * (3 - 2 * rise)
        const cy = out.capsuleY * (1 - lift) + (mobile ? H * .29 : direction === 'premotio' ? H * .14 : H * .29) * (1 - out.neat)
        pose.x = cx; pose.y = cy; pose.width = Math.min(620, mobile ? W - 32 : W * .42); pose.height = 60; pose.radius = 30
        if (capsule.current) {
          capsule.current.style.width = `${pose.width}px`
          const surface = capsule.current.querySelector<HTMLElement>('.capsule')
          if (surface) { surface.style.height = '60px'; surface.style.borderRadius = '30px' }
        }
        r.draw(out.cards, out.strips)
        // Two copies cross the type plane in the cover only. The actual card stays in the GL batch,
        // so the wall and collection remain one continuous, reversible A choreography.
        foreground.current?.querySelectorAll<HTMLImageElement>('img').forEach((img, j) => {
          const c = out.cards[COVER_FRONT[j]]
          const w = (1100 - c.z) / 1100
          img.src = items[c.layer].src
          img.style.width = `${r.cardW / w}px`
          img.style.opacity = String(direction === 'cover' && !mobile ? Math.max(0, 1 - out.neat * 3) : 0)
          img.style.transform = `translate(-50%, -50%) translate(${c.x / w}px, ${c.y / w}px) rotate(${c.rz}rad)`
        })
        if (capsule.current) {
          const t = b.mode === 'swell'
            ? `scale(${(1 + q).toFixed(4)})`
            : `translateY(${(q * 8).toFixed(2)}px) scale(${(1 + q * 0.045).toFixed(4)}, ${(1 - q * 0.05).toFixed(4)})`
          capsule.current.style.transform = `translate(calc(-50% + ${cx.toFixed(1)}px), calc(-50% + ${cy.toFixed(1)}px)) ${t}`
        }
        if (labels.current) labels.current.style.setProperty('--neat', out.neat.toFixed(3))
        const send=capsule.current?.querySelector<HTMLElement>('.capsule-send')
        if(send&&labels.current){
          const sendRight=send.getBoundingClientRect().right-box.getBoundingClientRect().left
          labels.current.style.setProperty('--home-copy-right',`${W-sendRight}px`)
          box.style.setProperty('--home-send-right',`${sendRight}px`)
        }

        dirty = false
      }
      if (visible) raf = requestAnimationFrame(tick)
    }
    const kick = () => { if (!raf && visible) { last = performance.now(); raf = requestAnimationFrame(tick) } }

    const ro = new ResizeObserver(() => { layout(); kick() })
    ro.observe(el)
    const io = new IntersectionObserver(([e]) => {
      visible = e.isIntersecting
      if (visible) { kick() }
    }, { threshold: 0 })
    io.observe(box)
    layout()
    let disposed=false
    Promise.all([r.ready,document.fonts.ready]).then(()=>{
      // Render the uploaded texture once before starting the timed compositor effect.
      if(disposed)return
      dirty=true;kick()
      requestAnimationFrame(()=>requestAnimationFrame(()=>{
        if(disposed)return
        resourcesReady=true;kick()
        if(phone){
          // 第一屏已经画出来了：在后台补齐其余卡片图和后几页 <img> 用的原图
          void r.rest.then(()=>{
            const queue=items.map(item=>item.src)
            const next=():void=>{const src=queue.shift();if(src&&!disposed)void loadImage(src).catch(()=>{}).then(next)}
            next();next()
            // 最后一页转盘用的 16 张缩略图也提前备好，转盘挂上时不用再等
            take(galleryCards,16).forEach(card=>{void cardThumbnailUrl(card.src).catch(()=>{})})
          })
        }
      }))
    },()=>{
      // 任一纹理加载/上传失败都退回普通图片，绝不把黑块留在页面上。
      if(!disposed){setError('纹理加载失败');setFallback(true)}
    })
    return () => { disposed=true;el.removeEventListener('webglcontextlost',contextLost);ro.disconnect(); io.disconnect(); cancelAnimationFrame(raf); focusAnimation?.cancel(); r.dispose() }
  }, [plan, items, still, direction, phone])

  return (
    <div ref={wrap} className={`relative home-stage intro-stage home-${direction} ${english ? 'home-english' : ''}`} data-home-stage data-clean={clean ? '' : undefined} data-phone={phone ? '' : undefined} data-focusing={!typed && !phone} data-phase={flow.state.phase} data-leaving={phone && flow.leaving ? '' : undefined} data-topbar={topbar} data-key-state={flow.state.keyStatus} data-reduced={still}>
      <div ref={focusVeil} className="home-screen sticky top-0 overflow-hidden" style={{ background: PAPER }}>
        {/* new canvas per plan: the old renderer gives its context back (loseContext) when the plan changes */}
        <canvas key={`${direction}-${still}`} ref={canvas} className="home-card-stage absolute inset-0 size-full" hidden={fallback} />
        {fallback && <div className="home-fallback" aria-hidden="true">
          <div className="home-fallback-grid">
            {(phone ? take(items, 16) : items.slice(0, 8)).map((item, i) => <img key={i} src={phone ? item.src : thumbs[i] ?? item.src} alt="" loading={i < 6 ? 'eager' : 'lazy'} />)}
          </div>
        </div>}
        {error && <p className="sr-only" role="status">3D 卡片暂不可用，已改用普通图片显示</p>}

        <div ref={labels} className="home-type pointer-events-none absolute inset-0">
          <p className="home-copy"><span>{INTRO_COPY.homeIntro}</span></p>
          <div className="home-heading">{(phone || (typed>0 && headlineReady)) && <HomeHeadline english={english} reduced={still || phone} onDone={() => { headlineDoneAt.current ??= performance.now() }} />}</div>

        </div>

        <div ref={foreground} className="home-foreground" aria-hidden hidden={fallback}><img alt="" /><img alt="" /></div>

        <div ref={capsule} className="home-capsule absolute left-1/2 top-1/2" style={{ transform: 'translate(-50%,-50%)' }}>
          <Capsule intro={flow.state.phase !== 'home' && flow.state.phase !== 'ready'} presentation={introPresentation(flow, still, phone)} reduced={still}
            onSend={phoneHomeCapsule ? undefined : goSearch} sendDisabled={phoneHomeCapsule}
            sendLabel={phoneHomeCapsule ? '示例搜索框' : phone || homeQuery.trim() ? '搜索素材库' : '进入素材库'}>
            {phoneHomeCapsule ? (
              <span className="home-search-input home-search-static" aria-hidden="true">那张图在哪？</span>
            ) : (flow.state.phase === 'home' || (phone && flow.state.phase === 'ready')) ? (
              <input className="home-search-input" aria-label="搜索收藏" value={homeQuery} placeholder={phone ? '那张图在哪？' : TYPING}
                onChange={event => setHomeQuery(event.target.value)}
                onKeyDown={event => {
                  if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                    event.preventDefault()
                    goSearch()
                  }
                }} />
            ) : flow.state.phase === 'ready' || typed ? (
              <SoftText key={typed}>{TYPING}</SoftText>
            ) : <span className="text-[16px] text-transparent">{TYPING}</span>}
          </Capsule>
        </div>
        {/* 首屏就给一个明确的主按钮：不要求用户先滚动，也不再用“往下滚动 / 点一下”的提示 */}
        {flow.state.phase === 'home' && <button type="button" className={`lab-btn-primary home-start-cta${starting ? ' is-starting' : ''}`} disabled={starting} onClick={start}><SoftText>{INTRO_COPY.start}</SoftText><span aria-hidden="true"> →</span></button>}
        <IntroPanels phone={phone} clean={clean} starfield={starfield} setStarfield={setStarfield} flow={flow} reduced={still} topbar={topbar} setTopbar={setTopbar} controls={controls} />
      </div>
    </div>
  )
}

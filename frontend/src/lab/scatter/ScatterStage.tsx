import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useReducedMotion } from 'motion/react'
import { oklchToHex, useImages } from '../data'
import { Capsule } from '../Capsule'
import { useIntroFlow } from '../intro/useIntroFlow'
import { FlowScene, capsuleTarget, type CapsulePose } from '../intro/flowScene'
import { IntroPanels, introPresentation } from '../intro/IntroPanels'
import { DEMO_MS, INTRO_COPY } from '../intro/copy'
import { quietSpring, updateWheel, platformLayout } from '../intro/motion'
import '../intro/intro.css'
import '../intro/fable.css'
import { COVER_FRONT, INITIAL_CARD_COUNT, MOBILE_INITIAL_CARD_COUNT, type HomeDirection } from './homePlans'
import { HomeHeadline } from './HomeHeadline'
import { SoftText } from '../intro/SoftText'
import { REFINE } from '../../main/refine'
import '../intro/intro-refine.css'
import { ScatterRenderer, type Card, type Strip } from './renderer'
import { collectLength, frame, matrixSettledProgress, P_COLLECT, P_NEAT, P_START, PLAN_B, type Bounce, type Frame, type Layout, type Plan, type Pool, type Pose } from './choreo'

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
export default function ScatterStage({ plan, bounce, reduced, controls, direction, english, clean = false, autoplay = false, startPage }: { plan: Plan; bounce: Bounce; reduced: boolean; controls?: ReactNode; direction: HomeDirection; english: boolean; /** /intro：不带调试开关的干净舞台 */ clean?: boolean; /** 进来就自动从头播放 */ autoplay?: boolean; /** 直接从某一页开始（排练用） */ startPage?: 'key' | 'platforms' | 'ready' }) {
  const { items } = useImages()
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const capsule = useRef<HTMLDivElement>(null)
  const start = useRef<HTMLButtonElement>(null)
  const labels = useRef<HTMLDivElement>(null)
  const foreground = useRef<HTMLDivElement>(null)
  const focusVeil = useRef<HTMLDivElement>(null)
  /** 引导版：点一下画面后自动往下滚的那段动画；用户自己一滚就停 */
  const autoScroll = useRef<(() => void) | null>(null)
  const headlineDoneAt = useRef<number | null>(null)
  const bounceRef = useRef(bounce)
  bounceRef.current = bounce
  const [error, setError] = useState<string | null>(null)
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
  // 离开页面时停掉还在跑的自动滚动
  useEffect(() => () => autoScroll.current?.(), [])
  const flow = useIntroFlow(still)
  const flowRef = useRef(flow); flowRef.current = flow
  useEffect(() => { if (startPage) flowRef.current.jump(startPage); else if (autoplay) flowRef.current.setDemo(true) }, [autoplay, startPage])
  useEffect(() => {
    if (flow.state.phase === 'key' || flow.state.run > 0) wrap.current?.scrollIntoView({ block: 'start', behavior: 'instant' })
  }, [flow.state.run, flow.state.phase])
  useEffect(() => {
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
  }, [])

  useEffect(() => {
    const el = canvas.current, box = wrap.current
    if (!el || !box || !items.length) return
    let r: ScatterRenderer
    try { r = new ScatterRenderer(el, items.map(i => i.src)); setError(null) } catch (e) { setError(String(e)); return }
    r.bg = rgb01(PAPER)
    // texture layers = items; the portrait is handed to the plan separately so it can place it (or leave it out)
    const face = items.findIndex(it => it.face)
    const pool: Pool = { cards: items.flatMap((it, i) => (it.face ? [] : [i])), face: face >= 0 ? face : null }

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
      lay = { ...l, scat: l.scat.map(sc), grid: l.grid.map(sc) }
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
    let scene = new FlowScene(), observedRun = -1, matrixAt = 0, caughtAt = 0, wheelCurrent = 0, wheelVelocity = 0
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
        if(state.phase==='ready'){const y=parseFloat(box.style.getPropertyValue('--intro-ready-cap-y'));if(Number.isFinite(y))target.y=y}
        for (const axis of ['x', 'y', 'width', 'height', 'radius'] as const) {
          if (still) { pose[axis] = target[axis]; velocity[axis] = 0 }
          else [pose[axis], velocity[axis]] = quietSpring(pose[axis], velocity[axis], target[axis], state.phase==='unlock'?dt/1.3:dt)
        }
        if (still) { wheelCurrent = state.active; wheelVelocity = 0 }
        else [wheelCurrent, wheelVelocity] = quietSpring(wheelCurrent, wheelVelocity, state.active, dt)
        updateWheel(wrap.current, wheelCurrent, W, H)
        r.cardW = plan.cardW * k; r.cardH = plan.cardH * k
        r.draw(scene.draw(state, W, H, now, dt, lay.layers, pose, still, lay.scat, plan, lay.collect, wheelCurrent, starfieldRef.current), [])
        if (capsule.current) {
          capsule.current.style.width = `${Math.min(W * .96, pose.width).toFixed(2)}px`
          capsule.current.style.transform = `translate(calc(-50% + ${pose.x.toFixed(2)}px), calc(-50% + ${pose.y.toFixed(2)}px))`
          const surface = capsule.current.querySelector<HTMLElement>('.capsule')
          if (surface) { surface.style.height = `${pose.height.toFixed(2)}px`; surface.style.borderRadius = `${pose.radius.toFixed(2)}px` }
        }
        if (start.current) { start.current.style.opacity = '0'; start.current.style.pointerEvents = 'none' }
        foreground.current?.querySelectorAll('img').forEach(img => { img.style.opacity = '0' })
        if (visible) raf = requestAnimationFrame(tick)
        return
      }
      const mobile = W <= 700
      const openingDuration = mobile ? OPENING_MS * .65 : OPENING_MS
      if(visible&&resourcesReady&&focusStarted<0){
        focusStarted=now
        if(!still)focusAnimation=focusVeil.current?.animate([
          {filter:mobile?'blur(22px)':'blur(40px)',offset:0},
          {filter:mobile?'blur(22px)':'blur(40px)',offset:1500/6500},
          {filter:'blur(0px)',offset:1},
        ],{duration:openingDuration,easing:'linear',fill:'both'})
      }
      const focus=still?1:focusStarted<0?0:Math.min(1,Math.max(0,(now-focusStarted)/openingDuration))
      if(focus===1&&!focusFinished){
        focusFinished=true;moving=true;setTyped(1)
        if(focusVeil.current)focusVeil.current.style.filter='none'
        focusAnimation?.cancel()
      }
      const sinceTitle = headlineDoneAt.current === null ? 0 : now - headlineDoneAt.current
      const L = collectLength(plan, lay)
      const b = bounceRef.current
      if (!still) {
        let p = 0
        if (state.demo) {
          // Start the hold only once the smoothed matrix has settled.
          if (P >= settledProgress && !matrixAt) matrixAt = now
          const arrangeElapsed = sinceTitle - DEMO_MS.homeTitleHold
          if (matrixAt) p = now - matrixAt >= DEMO_MS.matrixHold ? .96 : P_NEAT
          else if (arrangeElapsed > 0) {
            // The scroll choreography is static before P_START.
            P = Math.max(P, P_START)
            p = Math.min(P_NEAT, P_START + arrangeElapsed / DEMO_MS.homeArrange)
          }
        } else if (focus === 1) {
          p = progress()
        }
        // Scrub smoothing lets fast wheel flicks still read as continuous motion.
        const matrixIsSettled = state.demo && matrixAt > 0
        const nP = matrixIsSettled || Math.abs(p - P) < 1e-4
          ? p
          : P + (p - P) * (1 - Math.exp(-dt / (state.demo ? 0.28 : 0.1))) // 手动滚动要跟手；自动播放保留原来的柔和
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
      const nr = Math.min(1, Math.max(0, rise + (!state.demo && clock >= L && caughtAt && now - caughtAt > 260 ? dt / .7 : clock < L || state.demo ? -dt / .35 : 0)))
      if (nr !== rise) { rise = still && clock >= L ? 1 : nr; moving = true }
      // 按钮自带的 150ms CSS 过渡和这里的逐帧更新会打架（先拖在后面慢慢挪，最后猛地窜到位），所以在标签上关掉了过渡
      // “开始”有自己的时钟：胶囊停稳以后才慢慢浮上来（约 0.9 秒），往回滚时很快收掉
      const ns = Math.min(1, Math.max(0, startP + (!state.demo && rise >= 1 && clock >= L ? dt / .9 : -dt / .2)))
      if (ns !== startP) { startP = ns; moving = true }
      if (clock >= L && P > P_COLLECT) {
        if (!caughtAt) caughtAt = now
        if (state.demo && (still || now - caughtAt > DEMO_MS.homeCatch)) f.enterKey()
      } else caughtAt = 0
      // Reduced motion otherwise keeps the first composition readable, without the 420vh travel.
      if (moving || dirty) {
        frame(plan, H, lay, strips, P, clock, out)
        // Preserve 9bc3f96's first-screen size/count; B's size and four extra cards belong to the wall only.
        r.cardW = (plan.cardW + (PLAN_B.cardW - plan.cardW) * out.neat) * k
        r.cardH = (plan.cardH + (PLAN_B.cardH - plan.cardH) * out.neat) * k
        if (mobile) {
          r.cardW *= .9
          r.cardH *= .9
        }
        const enter = Math.min(1, Math.max(0, (P - 0.06) / 0.25))
        for (let i = mobile ? MOBILE_INITIAL_CARD_COUNT : INITIAL_CARD_COUNT; i < out.cards.length; i++) {
          out.cards[i].alpha *= enter
          out.cards[i].scale *= enter
        }
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
        if (start.current) {
          // 手动滚动时，“开始”等胶囊升到正中停稳了才出现，不跟着胶囊一起窜
          const o = state.demo ? Math.min(1, Math.max(0, (clock - L) / 0.7 + 1)) * (clock > 0 ? 1 : 0) : 1 - (1 - startP) ** 3
          start.current.style.opacity = String(still ? 1 : o)
          start.current.style.transform = `translate(calc(-50% + ${cx.toFixed(1)}px), calc(${cy.toFixed(1)}px + ${((1 - o) * 24).toFixed(2)}px))`
          start.current.style.pointerEvents = o > 0.5 || still ? 'auto' : 'none'
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
      requestAnimationFrame(()=>requestAnimationFrame(()=>{if(!disposed){resourcesReady=true;kick()}}))
    },()=>{if(!disposed)setError('图片没加载出来')})
    return () => { disposed=true;ro.disconnect(); io.disconnect(); cancelAnimationFrame(raf); focusAnimation?.cancel(); r.dispose() }
  }, [plan, items, still, direction])

  return (
    <div ref={wrap} className={`relative home-stage intro-stage home-${direction} ${english ? 'home-english' : ''}`} data-home-stage data-clean={clean ? '' : undefined} data-focusing={!typed} data-phase={flow.state.phase} data-topbar={topbar} data-key-state={flow.state.keyStatus} data-reduced={still}>
      <div ref={focusVeil} className="home-screen sticky top-0 overflow-hidden" style={{ background: PAPER }}
        data-click-advance={REFINE && flow.state.phase === 'home' && !flow.state.demo ? '' : undefined}
        onClick={event => {
          // 引导版：开场是滚动驱动的；点一下画面空白处，替你用 2.6 秒匀速滚到底，编排照原样播完
          const f = flowRef.current
          if (!REFINE || f.state.phase !== 'home' || f.state.demo || autoScroll.current) return
          if (event.target instanceof Element && event.target.closest('button, a, input, label, select, .intro-topbar')) return
          const box = wrap.current
          if (!box) return
          const from = window.scrollY
          const to = box.getBoundingClientRect().top + window.scrollY + box.offsetHeight - window.innerHeight
          if (to - from < 4) return
          const duration = still ? 0 : 2600
          const began = performance.now()
          let raf = 0
          const stop = () => {
            cancelAnimationFrame(raf)
            window.removeEventListener('wheel', stop)
            window.removeEventListener('touchstart', stop)
            window.removeEventListener('keydown', stop)
            autoScroll.current = null
          }
          const step = (now: number) => {
            const t = duration ? Math.min(1, (now - began) / duration) : 1
            const eased = t < .5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2
            window.scrollTo(0, from + (to - from) * eased)
            if (t < 1) raf = requestAnimationFrame(step)
            else stop()
          }
          window.addEventListener('wheel', stop, { passive: true })
          window.addEventListener('touchstart', stop, { passive: true })
          window.addEventListener('keydown', stop)
          autoScroll.current = stop
          raf = requestAnimationFrame(step)
        }}>
        {/* new canvas per plan: the old renderer gives its context back (loseContext) when the plan changes */}
        <canvas key={`${direction}-${still}`} ref={canvas} className="absolute inset-0 size-full" />
        {error && <p className="absolute inset-0 [display:grid] place-items-center text-[var(--ink-2)]">WebGL2 不可用：{error}</p>}

        <div ref={labels} className="home-type pointer-events-none absolute inset-0">
          <p className="home-copy"><span>{INTRO_COPY.homeIntro}</span></p>
          <div className="home-heading">{typed>0&&headlineReady&&<HomeHeadline english={english} reduced={still} onDone={() => { headlineDoneAt.current ??= performance.now() }} />}</div>

        </div>

        <div ref={foreground} className="home-foreground" aria-hidden><img alt="" /><img alt="" /></div>

        <div ref={capsule} className="home-capsule absolute left-1/2 top-1/2" style={{ transform: 'translate(-50%,-50%)' }}>
          <Capsule intro={flow.state.phase !== 'home' && flow.state.phase !== 'ready'} presentation={introPresentation(flow, still)} reduced={still}>
            {flow.state.phase === 'home' ? <span>{TYPING}</span> : flow.state.phase === 'ready' || typed ? (
              <SoftText key={typed}>{TYPING}</SoftText>
            ) : <span className="text-[16px] text-transparent">{TYPING}</span>}
          </Capsule>
        </div>
        <button ref={start} onClick={flow.enterKey} type="button" className="lab-btn-primary absolute left-1/2 top-[calc(50%+52px)]" style={{ opacity: 0, transform: 'translate(-50%, 8px)', transition: 'none' }}><SoftText>{INTRO_COPY.start}</SoftText></button>
        <IntroPanels clean={clean} starfield={starfield} setStarfield={setStarfield} flow={flow} reduced={still} topbar={topbar} setTopbar={setTopbar} controls={controls} />
      </div>
    </div>
  )
}

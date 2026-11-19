import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { LabImage } from '../lab/data'
import { thumbnailSrc } from '../imageThumbnail'
import { motion, MotionConfig } from 'motion/react'
import { OptionWheel } from './OptionWheel'
import { ThumbnailStrip } from './ThumbnailStrip'
import { useMobileLayout } from './useMobileLayout'
import './project-browse.css'
import { OVERLAY, PAGE, enterDelay, enterDelayMs } from './motion'

/** created：创建时间（毫秒），格子上的 (01) 就是按它排的真实顺序 */
export type Project = { id: string; name: string; note: string; items: LabImage[]; created: number }

/** 一个项目一块台面：暖纸色正方形，封面缩到约 55% 宽居中；下面是名字和张数。 */
function ProjectTable({ project, no, index, onEnter, focus, tableRef }: {
  project: Project
  /** 按创建时间的序号（从 1 起） */
  no: number
  index: number
  onEnter: () => void
  focus: 'on' | 'dim' | null
  tableRef: (el: HTMLElement | null) => void
}) {
  const cover = project.items[0]
  return (
    // data-reveal 放在外层：按钮的 className 会随转盘高亮变，React 改 className 会把 reveal 加上的 .is-in 抹掉，格子就消失了
    <div className="m-proj-cell" data-reveal style={{ '--rv-d': enterDelayMs(index) } as React.CSSProperties}>
    <button type="button" className={`m-proj${focus ? ` is-${focus}` : ''}`} onClick={onEnter} data-project={project.id}>
      <span className="m-proj-table" ref={tableRef}>
        {cover && <img src={thumbnailSrc(cover.src)} alt="" width={cover.w} height={cover.h} loading="lazy" draggable={false} />}
      </span>
      <span className="m-proj-cap">
        <i className="m-proj-no">({String(no).padStart(2, '0')})</i>
        <span className="m-proj-name">{project.name}</span>
        <span className="m-proj-n">· {project.items.length} 张</span>
      </span>
    </button>
    </div>
  )
}

type Rect = { x: number; y: number; w: number; h: number }
const rectOf = (el: Element): Rect => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height } }
function ProjectBrowse({ project, onEnter, active }: {
  project: Project
  onEnter: (index: number) => void
  active: boolean
}) {
  const [index, setIndex] = useState(0)
  const wheelPixels = useRef(0)
  const gallery = useRef<HTMLDivElement>(null)
  const select = (next: number) => {
    wheelPixels.current = 0
    setIndex(next)
  }

  useEffect(() => {
    if (!active) return
    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.defaultPrevented) return
      if (event.target instanceof Element && event.target.closest('.m-pwheel, input, textarea, select')) return
      event.preventDefault()
      const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16
        : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? window.innerHeight : 1
      const delta = event.deltaY * unit
      if (!delta) return
      if (Math.sign(delta) !== Math.sign(wheelPixels.current)) wheelPixels.current = 0
      wheelPixels.current += delta
      const threshold = Math.max(44, gallery.current?.querySelector('.m-ld-strip-item[aria-current]')?.getBoundingClientRect().width ?? 72)
      if (Math.abs(wheelPixels.current) < threshold) return
      const direction = Math.sign(wheelPixels.current)
      wheelPixels.current %= threshold
      setIndex(current => Math.max(0, Math.min(project.items.length - 1, current + direction)))
    }
    window.addEventListener('wheel', onWheel, { passive: false, capture: true })
    return () => window.removeEventListener('wheel', onWheel, true)
  }, [active, project.items.length])

  const item = project.items[index]
  const number = (n: number) => String(n).padStart(3, '0')
  if (!item) return <p className="m-project-browse-empty">这个项目还没有图片</p>
  return (
    <div className="m-project-browse" ref={gallery}>
      <ThumbnailStrip list={project.items} index={index} onSelect={select} active={active} />
      <div className="m-project-browse-body">
        <button type="button" className="m-project-browse-image" onClick={() => onEnter(index)}
          aria-label={`打开第 ${index + 1} 张详情`}>
          <img src={thumbnailSrc(item.src)} width={item.w} height={item.h} alt="" />
        </button>
        <aside className="m-project-browse-index" aria-live="polite">
          <span>{number(index + 1)}</span>
          <small>{number(index + 1)} – {number(project.items.length)}</small>
        </aside>
      </div>
    </div>
  )
}

const EDGE = 24 // 指针进到窗口左边这么近，转盘滑出
const LINGER = 500 // 指针离开转盘后多久收起

/**
 * 左侧项目转盘：平时完全隐藏，指针贴到窗口左边缘才滑出；离开 0.5 秒收起。
 * 转到正中的项目在网格里高亮、其余封面变淡；点正中的名字或回车进入。
 */
function ProjectWheel({ projects, focus, onFocus, onEnter, onOpenChange }: {
  projects: Project[]
  focus: number
  onFocus: (i: number) => void
  onEnter: (i: number) => void
  onOpenChange: (open: boolean) => void
}) {
  const mobile = useMobileLayout()
  const [open, setOpen] = useState(false)
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => { onOpenChange(open) }, [open, onOpenChange])

  useEffect(() => {
    if (window.matchMedia('(pointer: coarse)').matches) return
    let timer = 0
    const stay = () => { window.clearTimeout(timer); timer = 0 }
    const leaveSoon = () => { if (!timer) timer = window.setTimeout(() => { timer = 0; setOpen(false) }, LINGER) }
    const onMove = (e: PointerEvent) => {
      if (e.pointerType === 'touch') return
      if (e.clientX <= EDGE) { stay(); setOpen(true); return }
      const box = panel.current?.getBoundingClientRect()
      const over = !!box && e.clientX >= box.left && e.clientX <= box.right && e.clientY >= box.top && e.clientY <= box.bottom
      if (over) stay(); else leaveSoon()
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    document.documentElement.addEventListener('pointerleave', leaveSoon)
    return () => {
      stay()
      window.removeEventListener('pointermove', onMove)
      document.documentElement.removeEventListener('pointerleave', leaveSoon)
    }
  }, [])

  // 滑出后让转盘拿到焦点，回车就能进项目
  useEffect(() => {
    if (!open) return
    // 滑出那一帧 visibility 还没切过来，focus() 会被忽略；稍等一下再聚焦
    const t = window.setTimeout(() => panel.current?.querySelector<HTMLElement>('.m-ow')?.focus({ preventScroll: true }), 80)
    return () => window.clearTimeout(t)
  }, [open])

  return (
    <MotionConfig reducedMotion="user">
      <button type="button" className="m-pwheel-touch" aria-expanded={open}
        onClick={() => setOpen(!open)}>切换项目</button>
      {/* 滑进滑出用浮层弹簧：滑到一半指针回来，会带着当前速度顺势滑回去 */}
      <motion.div ref={panel} className={`m-pwheel m-frost${open ? ' is-open' : ''}`} inert={open ? undefined : true} data-lenis-prevent
        initial={false} animate={{ x: open ? '0%' : '-100%', opacity: open ? 1 : 0 }} transition={OVERLAY}>
        <button type="button" className="m-pwheel-touch m-pwheel-dismiss" onClick={() => setOpen(false)}>关闭</button>
        <OptionWheel items={projects.map(p => p.name)} defaultSelected={focus} label="项目"
          rowHeight={32} inset={28} onChange={i => onFocus(i)} onConfirm={i => { if (mobile) setOpen(false); onEnter(i) }} />
      </motion.div>
    </MotionConfig>
  )
}

/** 顶栏右侧要能切「翻看 / 网格」，切换时机和飞行动画留在页面里（这里只把开关交出去） */
export type ProjectsHandle = {
  setLayout: (next: 'grid' | 'feed') => void
  getCurrentProject: () => Project | undefined
}

/** 项目页：另一种状态——在做事，不是在找东西。 */
export const Projects = forwardRef<ProjectsHandle, {
  /** 从“要参考”存进来的项目（排在最前） */
  saved: Project[]
  onOpen: (project: Project, index: number) => void
  active: boolean
  initialLayout: 'grid' | 'feed'
  /** 布局切到哪了（顶栏那颗字要跟着点亮） */
  onLayoutChange?: (next: 'grid' | 'feed') => void
}>(function Projects({ onOpen, saved, onLayoutChange, active, initialLayout }, ref) {
  const projects = useMemo(() => [...saved].sort((a, b) => a.created - b.created), [saved])
  const [wheelOpen, setWheelOpen] = useState(false)
  const [focus, setFocus] = useState(0)
  const [layout, setLayout] = useState(initialLayout)
  const currentProject = projects[focus] ?? projects[0]
  // 切换翻看 / 网格：先记下每块台面现在在哪，换完布局再从旧位置飞到新位置（一块块错开出发）
  const tables = useRef(new Map<string, HTMLElement>())
  const before = useRef<Map<string, Rect> | null>(null)
  const tableRef = (id: string) => (el: HTMLElement | null) => { if (el) tables.current.set(id, el); else tables.current.delete(id) }
  const switchLayout = (next: 'grid' | 'feed') => {
    if (next === layout) return
    before.current = new Map([...tables.current].map(([id, el]) => [id, rectOf(el)]))
    setLayout(next)
    onLayoutChange?.(next)
  }
  // 顶栏右侧的「翻看 | 网格」通过这个把手切布局，飞行动画仍然留在这里
  useImperativeHandle(ref, () => ({
    setLayout: switchLayout,
    getCurrentProject: () => layout === 'feed' ? currentProject : undefined,
  }), [layout, currentProject])
  useLayoutEffect(() => {
    const prev = before.current
    before.current = null
    if (!prev || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    projects.forEach((p, i) => {
      const el = tables.current.get(p.id)
      const from = prev.get(p.id)
      if (!el || !from) return
      const to = rectOf(el)
      if (!to.w || !to.h) return
      el.animate([
        { transformOrigin: '0 0', transform: `translate(${from.x - to.x}px, ${from.y - to.y}px) scale(${from.w / to.w}, ${from.h / to.h})` },
        { transformOrigin: '0 0', transform: 'none' },
      ], { duration: PAGE.duration * 1000, delay: enterDelay(i) * 1000, easing: `cubic-bezier(${PAGE.ease.join(', ')})`, fill: 'backwards' })
    })
    // 只在切布局时跑
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout])


  // 进项目 = 用详情页从第一张往下翻这个项目的图；空项目没有可看的
  const enter = (p: Project, index = 0) => { if (p.items[index]) onOpen(p, index) }


  return (
    <section className="m-page m-projects" data-layout={layout} data-wheel={wheelOpen ? 'open' : undefined}>
      <ProjectWheel projects={projects} focus={focus} onFocus={setFocus} onOpenChange={setWheelOpen}
        onEnter={i => { const p = projects[i]; if (p) enter(p) }} />
      {layout === 'grid' && <div className="m-proj-head">
        <div className="m-proj-row2" data-reveal>
          <i className="m-proj-total">({projects.length})</i>
          <span className="m-spacer" />
          <span className="m-proj-all">全部项目</span>
        </div>
      </div>}

      {layout === 'grid' ? (
        <div className="m-proj-grid">
          {projects.map((p, i) => (
            <ProjectTable key={p.id} project={p} no={i + 1} index={i} onEnter={() => enter(p)} tableRef={tableRef(p.id)}
              focus={wheelOpen ? (i === focus ? 'on' : 'dim') : null} />
          ))}
        </div>
      ) : (
        currentProject && <ProjectBrowse key={currentProject.id} project={currentProject} active={active}
          onEnter={index => enter(currentProject, index)} />
      )}


    </section>
  )
})

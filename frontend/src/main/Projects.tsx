import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { LabImage } from '../lab/data'
import { thumbnailSrc } from '../imageThumbnail'
import { motion, MotionConfig } from 'motion/react'
import { OptionWheel } from './OptionWheel'
import { ThumbnailStrip } from './ThumbnailStrip'
import { useMobileLayout, usePhoneLayout } from './useMobileLayout'
import './project-browse.css'
import { OVERLAY, PAGE, enterDelay, enterDelayMs } from './motion'
import { ActionMenu, ConfirmDialog, RenameDialog, useCardPress, type CardPress, type MenuPoint } from './CardActions'
import { Menu } from 'lucide'
import { Icon } from '../lab/Icon'

/** created：创建时间（毫秒），格子上的 (01) 就是按它排的真实顺序 */
export type Project = { id: string; name: string; note: string; items: LabImage[]; created: number }

/** 一个项目一块台面：暖纸色正方形，封面缩到约 55% 宽居中；下面是名字和张数。 */
function ProjectTable({ project, no, index, onEnter, onMenu, press, focus, tableRef, arranging = false, dragging = false, onHandle }: {
  /** 手机「整理」模式：卡片轻晃、右上角换成拖动把手 ≡，点卡片不进项目 */
  arranging?: boolean
  /** 这张正被拖着 */
  dragging?: boolean
  /** 按下把手：立刻开始拖，不用长按（移动 / 松手由 Projects 挂在 window 上接） */
  onHandle?: (event: React.PointerEvent<HTMLElement>) => void
  project: Project
  /** 按创建时间的序号（从 1 起） */
  no: number
  index: number
  onEnter: () => void
  /** 长按 / 右键 / 「…」三种入口都打开同一个项目小菜单 */
  onMenu: (point: MenuPoint) => void
  press: CardPress
  focus: 'on' | 'dim' | null
  tableRef: (el: HTMLElement | null) => void
}) {
  const cover = project.items[0]
  return (
    // data-reveal 放在外层：按钮的 className 会随转盘高亮变，React 改 className 会把 reveal 加上的 .is-in 抹掉，格子就消失了。
    // 所以这一层的 className 永远不变：整理 / 拖动的状态用 data-* 属性表示（之前用类名，点「整理」后 .is-in 被抹掉，整页一片白）
    <div className="m-proj-cell" data-reveal data-cell={project.id}
      data-arranging={arranging ? '' : undefined} data-dragging={dragging ? '' : undefined}
      style={{ '--rv-d': enterDelayMs(index) } as React.CSSProperties}>
    <button type="button" className={`m-proj${focus ? ` is-${focus}` : ''}`} data-project={project.id}
      {...press.handlers} aria-disabled={arranging || undefined}
      onClick={() => { if (arranging || press.swallowClick()) return; onEnter() }}>
      <span className="m-proj-table" ref={tableRef}>
        {cover && <img src={thumbnailSrc(cover.src)} alt="" width={cover.w} height={cover.h} loading="lazy" draggable={false} />}
      </span>
      <span className="m-proj-cap">
        <i className="m-proj-no">({String(no).padStart(2, '0')})</i>
        <span className="m-proj-name">{project.name}</span>
        <span className="m-proj-n">· {project.items.length} 张</span>
      </span>
    </button>
    {/* 整理模式：右上角是拖动把手（44×44），「…」暂时收起 */}
    {arranging && onHandle ? (
      <button type="button" className="m-proj-handle" aria-label={`拖动「${project.name}」换位置`}
        onPointerDown={onHandle} onContextMenu={event => event.preventDefault()}>
        <Icon icon={Menu} size={20} />
      </button>
    ) : (
      // 电脑上悬停才出现，触屏上一直显示（和长按、右键打开同一个菜单）
      <button type="button" className="m-proj-more" aria-label={`${project.name} 的操作`} aria-haspopup="menu"
        onClick={event => {
          const box = event.currentTarget.getBoundingClientRect()
          onMenu({ x: box.right - 6, y: box.bottom + 6 })
        }}>…</button>
    )}
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

/**
 * 手机：点进一个项目以后的样子——三列等大的方形格子（图片裁切填满，间距 4px），往下划看全部，点格子进详情。
 * 不再一进去先显示一张大图。样式见 styles/phone.css「项目详情页」。
 */
function ProjectGrid({ project, onBack, onOpen, onMenu }: {
  project: Project
  onBack: () => void
  onOpen: (index: number) => void
  onMenu: (point: MenuPoint) => void
}) {
  return (
    <div className="m-proj-open">
      <div className="m-proj-open-head">
        <button type="button" className="m-proj-open-back" onClick={onBack} aria-label="返回全部项目">←</button>
        <h2 className="m-proj-open-name">{project.name}</h2>
        <span className="m-proj-open-n">{project.items.length} 张</span>
        <button type="button" className="m-proj-open-more" aria-label={`${project.name} 的操作`} aria-haspopup="menu"
          onClick={event => {
            const box = event.currentTarget.getBoundingClientRect()
            onMenu({ x: box.right - 6, y: box.bottom + 6 })
          }}>…</button>
      </div>
      {project.items.length ? (
        <div className="m-proj-open-grid">
          {project.items.map((item, i) => (
            <button type="button" key={`${item.id}-${i}`} className="m-proj-open-cell" onClick={() => onOpen(i)}
              aria-label={`打开第 ${i + 1} 张详情`}>
              <img src={thumbnailSrc(item.src)} alt="" loading={i < 12 ? 'eager' : 'lazy'} draggable={false} />
            </button>
          ))}
        </div>
      ) : <p className="m-project-browse-empty">这个项目还没有图片</p>}
    </div>
  )
}

/** 整理：让位 / 落位动画时长（= --motion-duration-base），缓动 = --motion-ease-emphasized */
const ARRANGE_MS = 240
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
  /** 项目改名：成功后由这里在本页立刻换名字（接口 PATCH 由上层发） */
  onRenameProject?: (project: Project, name: string) => Promise<boolean>
  /** 项目删除：成功后由这里立刻从列表里拿掉（接口 DELETE 由上层发） */
  onDeleteProject?: (project: Project) => Promise<boolean>
  /** 手机：点开的是哪个项目（三列方格）；null = 全部项目列表。状态放在上层，看完详情回来还停在这个项目里 */
  openId?: string | null
  onOpenId?: (id: string | null) => void
  /** 手机：顶栏「整理」开着没有（开着时卡片可以拖着换位置） */
  arranging?: boolean
  /** 手机：自己排过的项目顺序（项目 id）；没排过的项目按创建时间接在后面 */
  order?: string[]
  /** 点「完成」时把新顺序交上去保存 */
  onReorder?: (ids: string[]) => void
  /** 手机：页面顶上那一行右边的工具（「新建项目 +」，MainApp 传进来；左边是「N 个项目」） */
  headTools?: React.ReactNode
}>(function Projects({ onOpen, saved, onLayoutChange, active, initialLayout, onRenameProject, onDeleteProject, openId = null, onOpenId,
  arranging = false, order, onReorder, headTools }, ref) {
  const phone = usePhoneLayout()
  // 删除 / 改名的即时结果：接口回来之前先按这里显示，reload 回来以后列表本身就没有了
  const [gone, setGone] = useState<Set<string>>(new Set())
  const [renamed, setRenamed] = useState<Map<string, string>>(new Map())
  const projects = useMemo(() => [...saved]
    .filter(project => !gone.has(project.id))
    .map(project => {
      const name = renamed.get(project.id)
      return name === undefined ? project : { ...project, name }
    })
    .sort((a, b) => a.created - b.created)
    // 手机上按自己排过的顺序；电脑不变
    .sort((a, b) => {
      if (!phone || !order?.length) return 0
      const rank = (id: string) => { const at = order.indexOf(id); return at < 0 ? Number.MAX_SAFE_INTEGER : at }
      return rank(a.id) - rank(b.id)
    }), [saved, gone, renamed, phone, order])

  // ===== 手机「整理」：按住把手立刻拖，卡片轻微放大浮起，其他卡片让位，松手落位；点「完成」才保存 =====
  /** 整理中的顺序（项目 id）；不在整理时为 null */
  const [draft, setDraft] = useState<string[] | null>(null)
  const [dragId, setDragId] = useState<string | null>(null)
  const draftRef = useRef<string[] | null>(null)
  draftRef.current = draft
  const gridRef = useRef<HTMLDivElement>(null)
  /** 拖动中的数据：开始时每个位置（格子）在屏幕上的框、手指起点、从第几格拿起、现在该落在第几格、DOM 里实际在第几格 */
  const drag = useRef<{ id: string; pointer: number; x: number; y: number; dx: number; dy: number; slots: Rect[]; from: number; at: number; shownAt: number } | null>(null)
  /** 让位动画：换顺序前记下每张卡的位置，换完从旧位置滑到新位置 */
  const flipFrom = useRef<Map<string, Rect> | null>(null)
  const reorderRef = useRef(onReorder)
  reorderRef.current = onReorder
  const cellOf = (id: string) => gridRef.current?.querySelector<HTMLElement>(`[data-cell="${CSS.escape(id)}"]`) ?? null
  const commit = (ids: string[] | null) => {
    if (!ids) return
    const now = projects.map(p => p.id)
    if (ids.join(',') !== now.join(',')) reorderRef.current?.(ids)
  }
  // 进整理：从现在的顺序开始；退整理（点「完成」）：保存新顺序
  useEffect(() => {
    if (arranging) { setDraft(projects.map(p => p.id)); return }
    commit(draftRef.current)
    setDraft(null)
    setDragId(null)
    drag.current = null
    // 只看开关
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [arranging])
  // 整理到一半离开项目页（底部导航切走）：也保存
  useEffect(() => () => { if (draftRef.current) commit(draftRef.current) }, [])
  /** 被拖的那张：跟着手指，略放大（DOM 里它已经在第 shownAt 格，所以要减掉这一格的位置） */
  const placeDragged = () => {
    const d = drag.current
    const el = d && cellOf(d.id)
    if (!d || !el) return
    const origin = d.slots[d.from]
    const slot = d.slots[d.shownAt]
    el.style.transform = `translate3d(${origin.x + d.dx - slot.x}px, ${origin.y + d.dy - slot.y}px, 0) scale(1.04)`
  }
  useLayoutEffect(() => {
    const d = drag.current
    if (d && draft) {
      d.shownAt = draft.indexOf(d.id)
      placeDragged()
    }
    const from = flipFrom.current
    flipFrom.current = null
    if (!from || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    from.forEach((before, id) => {
      if (id === d?.id) return
      const el = cellOf(id)
      if (!el) return
      const now = rectOf(el)
      const x = before.x - now.x
      const y = before.y - now.y
      if (!x && !y) return
      el.animate([{ transform: `translate(${x}px, ${y}px)` }, { transform: 'none' }],
        { duration: ARRANGE_MS, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' })
    })
    // 只在顺序变了以后跑
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft])
  // 移动 / 松手挂在 window 上：拖动中卡片在 DOM 里会被挪位置，挂在把手上的指针捕获可能丢，window 一直收得到
  const handle = (id: string) => (event: React.PointerEvent<HTMLElement>) => {
    const ids = draftRef.current
    const grid = gridRef.current
    if (!ids || !grid || drag.current || !event.isPrimary) return
    event.preventDefault()
    const slots = ids.map(each => { const el = cellOf(each); return el ? rectOf(el) : { x: 0, y: 0, w: 0, h: 0 } })
    const from = ids.indexOf(id)
    drag.current = { id, pointer: event.pointerId, x: event.clientX, y: event.clientY, dx: 0, dy: 0, slots, from, at: from, shownAt: from }
    setDragId(id)
    placeDragged()
    const stop = () => {
      window.removeEventListener('pointermove', dragMove)
      window.removeEventListener('pointerup', end)
      window.removeEventListener('pointercancel', end)
    }
    const end = (up: PointerEvent) => {
      if (!drag.current) { stop(); return }
      if (up.pointerId === drag.current.pointer) { stop(); dragEnd() }
    }
    window.addEventListener('pointermove', dragMove)
    window.addEventListener('pointerup', end)
    window.addEventListener('pointercancel', end)
  }
  const dragMove = (event: PointerEvent) => {
      const d = drag.current
      if (!d || event.pointerId !== d.pointer) return
      d.dx = event.clientX - d.x
      d.dy = event.clientY - d.y
      // 被拖的卡中心离哪一格的中心最近，就排到哪一格
      const origin = d.slots[d.from]
      const cx = origin.x + origin.w / 2 + d.dx
      const cy = origin.y + origin.h / 2 + d.dy
      let best = d.at
      let bestDistance = Infinity
      d.slots.forEach((slot, i) => {
        const distance = Math.hypot(slot.x + slot.w / 2 - cx, slot.y + slot.h / 2 - cy)
        if (distance < bestDistance) { bestDistance = distance; best = i }
      })
      if (best !== d.at) {
        d.at = best
        const ids = draftRef.current ?? []
        flipFrom.current = new Map(ids.flatMap(each => { const el = cellOf(each); return el ? [[each, rectOf(el)] as const] : [] }))
        setDraft(previous => {
          if (!previous) return previous
          const next = previous.filter(each => each !== d.id)
          next.splice(best, 0, d.id)
          return next
        })
      }
      placeDragged()
  }
  /** 松手：从手指的位置吸回自己的格子 */
  const dragEnd = () => {
    const d = drag.current
    if (!d) return
    drag.current = null
    const el = cellOf(d.id)
    if (!el) { setDragId(null); return }
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    el.style.transition = reduce ? '' : `transform ${ARRANGE_MS}ms cubic-bezier(0.22, 1, 0.36, 1)`
    el.style.transform = 'translate3d(0, 0, 0) scale(1)'
    window.setTimeout(() => {
      el.style.transition = ''
      el.style.transform = ''
      setDragId(current => current === d.id ? null : current)
    }, reduce ? 0 : ARRANGE_MS + 20)
  }
  /** 整理中按草稿顺序摆（整理途中新出现的项目接在最后） */
  const arranged = draft
    ? [...draft.flatMap(id => projects.filter(p => p.id === id)), ...projects.filter(p => !draft.includes(p.id))]
    : projects
  const [wheelOpen, setWheelOpen] = useState(false)
  const [focus, setFocus] = useState(0)
  const [layout, setLayout] = useState(initialLayout)
  // 长按 / 右键打开的项目菜单，以及菜单里的「改名」「删除」两块面板
  const [menu, setMenu] = useState<{ project: Project; point: MenuPoint } | null>(null)
  const [renaming, setRenaming] = useState<Project | null>(null)
  const [deleting, setDeleting] = useState<Project | null>(null)
  const press = useCardPress((point, element) => {
    const project = projects.find(entry => entry.id === element.dataset.project)
    if (project) setMenu({ project, point })
  })
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
  // 手机：点项目先进三列方格，不直接进第一张的大图（电脑照旧）
  const enterProject = (p: Project) => { if (phone && onOpenId) onOpenId(p.id); else enter(p) }
  const opened = phone && openId !== null ? projects.find(p => p.id === openId) : undefined
  // 点开的项目被删掉了：回到全部项目（列表还没读回来时不动）
  const openMissing = phone && openId !== null && saved.length > 0 && !opened
  useEffect(() => { if (openMissing) onOpenId?.(null) }, [openMissing, onOpenId])
  /** 手机：页面顶上只有一行——左边「N 个项目」，右边「新建项目 +」（原来「新建项目 +」和「(N) 全部项目」各占一行） */
  const phoneHead = (
    <div className="m-proj-head m-proj-head--phone">
      <div className="m-proj-row2">
        <span className="m-proj-count">{projects.length} 个项目</span>
        <span className="m-spacer" />
        {headTools}
      </div>
    </div>
  )

  return (
    <section className="m-page m-projects" data-layout={layout} data-wheel={wheelOpen ? 'open' : undefined}>
      {opened ? (
        <ProjectGrid project={opened} onBack={() => onOpenId?.(null)} onOpen={index => enter(opened, index)}
          onMenu={point => setMenu({ project: opened, point })} />
      ) : phone && projects.length === 0 ? (
        // 手机：一个项目都没有时不留一片空白，用设计系统的空状态（.ds-empty），在页面中间说一句
        <>
          {phoneHead}
          <div className="ds-empty m-proj-empty" role="status">
            <p className="ds-empty-title">还没有项目</p>
            <p className="ds-empty-text">在图片详情里点「加入项目」就能建一个</p>
          </div>
        </>
      ) : <>
      <ProjectWheel projects={projects} focus={focus} onFocus={setFocus} onOpenChange={setWheelOpen}
        onEnter={i => { const p = projects[i]; if (p) enterProject(p) }} />
      {layout === 'grid' && phone && phoneHead}
      {layout === 'grid' && !phone && <div className="m-proj-head">
        <div className="m-proj-row2" data-reveal>
          <i className="m-proj-total">({projects.length})</i>
          <span className="m-spacer" />
          <span className="m-proj-all">全部项目</span>
        </div>
      </div>}

      {layout === 'grid' ? (
        <div ref={gridRef} className={`m-proj-grid${phone && draft ? ' is-arranging' : ''}`}>
          {arranged.map((p, i) => (
            <ProjectTable key={p.id} project={p} no={i + 1} index={i} onEnter={() => enterProject(p)} press={press}
              onMenu={point => setMenu({ project: p, point })} tableRef={tableRef(p.id)}
              focus={wheelOpen ? (i === focus ? 'on' : 'dim') : null}
              arranging={phone && !!draft} dragging={dragId === p.id} onHandle={phone && draft ? handle(p.id) : undefined} />
          ))}
        </div>
      ) : (
        currentProject && <ProjectBrowse key={currentProject.id} project={currentProject} active={active}
          onEnter={index => enter(currentProject, index)} />
      )}
      </>}

      {menu && (
        <ActionMenu point={menu.point} title={menu.project.name} onClose={() => setMenu(null)}
          actions={[
            { key: 'rename', label: '改名', act: () => setRenaming(menu.project) },
            { key: 'delete', label: '删除', danger: true, act: () => setDeleting(menu.project) },
          ]} />
      )}
      {renaming && (
        <RenameDialog initial={renaming.name} onClose={() => setRenaming(null)}
          onSubmit={async name => {
            const done = await onRenameProject?.(renaming, name) ?? false
            if (done) setRenamed(previous => new Map(previous).set(renaming.id, name))
            return done
          }} />
      )}
      {deleting && (
        <ConfirmDialog title="删除项目" text="删除后可以在后台恢复，确定删除这个项目？" onClose={() => setDeleting(null)}
          onConfirm={async () => {
            const done = await onDeleteProject?.(deleting) ?? false
            if (done) setGone(previous => new Set(previous).add(deleting.id))
            return done
          }} />
      )}
    </section>
  )
})

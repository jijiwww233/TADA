import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Ellipsis } from 'lucide'
import { Icon } from '../lab/Icon'
import type { LabImage, Swatch } from '../lab/data'
import { thumbnailSrc } from '../imageThumbnail'
import { sourceLabel } from './meta'
import { RevealWords } from './RevealWords'
import { scrollToInstant } from './smooth'
import { ThumbnailStrip } from './ThumbnailStrip'
import { useMobileLayout, usePhoneLayout, useTouchLayout } from './useMobileLayout'
import type { WiringApi } from '../api/wiring'
import type { Project } from './Projects'
import { mergeDetail } from './wiringData'
import { REFINE } from './refine'
import type { AnalysisStatus } from '../api'
import { pendingAnalysisNote } from './useAnalysisStatus'
import { ActionMenu, ConfirmDialog, RenameDialog, type MenuPoint } from './CardActions'
import './project-picker.css'

/** 收藏日期：今年写“10月12日”，往年带上年份。 */
function savedLabel(iso: string | null | undefined): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const md = `${d.getMonth() + 1}月${d.getDate()}日`
  return d.getFullYear() === new Date().getFullYear() ? md : `${d.getFullYear()}年${md}`
}
/**
 * 手机左右滑（真机反馈：原来太快、太灵敏）：
 *   切换 / 回弹的动画 350ms、缓出（CSS 读 --ld-swipe-ms，由这里写进去，只有这一个数；改前 240ms）
 */
const SWIPE_MS = 350
/** 手指动了多少像素才判断是横滑还是竖滑（改前 8） */
const SWIPE_SLOP = 12
/** 横向位移要超过竖向的这么多倍才算横滑，斜着拖不会误触（改前 1 倍） */
const SWIPE_RATIO = 1.5
/** 跟手阻尼：页面只走手指的这么多（改前 1 = 完全跟手） */
const SWIPE_FOLLOW = 0.6
/** 到头时只跟手这么多（轻微回弹的感觉；改前 0.25） */
const SWIPE_EDGE = 0.15
/** 松手时手指横移超过屏宽的这个比例才换一张，没过就弹回原图（改前 1/4，另外甩得快也算，现在去掉了「甩」） */
const SWIPE_COMMIT = 1 / 3
/** 从这些地方开始的手势不算换图：同一组小图、标签行（它们自己横滑）、底部操作条、「⋯」、弹出的面板、输入框 */
const NO_SWIPE = '.m-ld-group, .m-ld-strip, .m-ld-tags, .m-ld-bar, .m-ld-topmore, .m-proj-pick, input, textarea'
const fileName = (src: string) => src.split('/').pop() || 'image'
const pad2 = (n: number) => String(n).padStart(2, '0')
const pad3 = (n: number) => String(n).padStart(3, '0')
const ROMAN = ['i.', 'ii.', 'iii.']
/** 有真正的字（不是空串、空格或「· · ·」这类只有符号的占位） */
const meaningful = (s?: string | null): s is string => !!s && /[\p{L}\p{N}]/u.test(s)
/**
 * 刚进详情页那一下用原有的逐词出现；往下滚换图以后不再逐字播，直接换成新内容，只做 0.15 秒淡入。
 */
function Words({ text, moved }: { text: string; moved: boolean }) {
  return moved ? <span className="m-ld-fade">{text}</span> : <RevealWords text={text} />
}

/**
 * 图片详情（照 Lusano 产品详情）：放在页面里、跟着整页滚（滚动是原有的 Lenis），中栏约 36% 屏宽一列图，
 * 左右两栏钉在视口里。只有进入时第一屏能看到的图用原有的 data-reveal 渐显，往下滚出来的图直接显示；
 * 文字只在进入时逐词出现一次，之后换图时右上编号、标题、参数表直接换内容（0.15 秒淡入）。
 */
export function Detail({ list, index, postOf, onClose, onToast, onColor, api, projects, onAdd, onSimilar, onPip,
  onProjectEdit, projectEditPending, analysis = null, single = false, project, onDeleteImage, onRenameProject, onDeleteProject,
  browse, enterFrom }: {
  /**
   * 手机左右滑（只看一张时）：进详情前那个列表里有没有上一张 / 下一张、换过去。
   * 项目进来的不传，Detail 直接在 list（整个项目）里换。
   */
  browse?: { prev: boolean; next: boolean; go: (delta: 1 | -1) => void }
  /** 是滑过来的：1 = 从右边进来（下一张），-1 = 从左边进来（上一张） */
  enterFrom?: 1 | -1
  /**
   * 一次只看一张（从素材库、日历、手机画板点进来时）：list 是被点那张所在的同一帖，
   * 页面只放被点的这一张，标题、描述、标签、主色、按钮全按这一张；同帖其它图在下面一排小图里点着换。
   * 项目里点进来时为 false，照旧整组往下翻。
   */
  single?: boolean
  /** 全局分析状态：这张图还没分析结果时，说明它在排队、正在分析，还是不会被分析 */
  analysis?: AnalysisStatus | null
  list: LabImage[]
  index: number
  postOf: (item: LabImage) => LabImage[]
  onClose: () => void
  onToast: (s: string) => void
  onColor: (swatch: Swatch) => void
  api: WiringApi
  projects: Project[]
  onAdd: (project: number, image: number) => void
  onSimilar: (image: LabImage) => void
  onPip?: (image: LabImage) => void
  onProjectEdit?: (image: LabImage, action: 'previous' | 'next' | 'remove') => void
  projectEditPending?: boolean
  /** 从项目页进来看的是哪个项目（项目详情页顶部要放项目操作） */
  project?: Project
  /** 删除这张图：上层发 DELETE，成功后关闭详情并刷新列表 */
  onDeleteImage?: (item: LabImage) => Promise<boolean>
  onRenameProject?: (project: Project, name: string) => Promise<boolean>
  onDeleteProject?: (project: Project) => Promise<boolean>
}) {
  const mobile = useMobileLayout()
  const phone = usePhoneLayout()
  const back = useRef<HTMLButtonElement>(null)
  const plates = useRef<(HTMLDivElement | null)[]>([])
  const [at, setAt] = useState(Math.max(0, index))
  const [tags, setTags] = useState<Map<number, string[]>>(() => new Map())
  const [choosingProject, setChoosingProject] = useState(false)
  const [detail, setDetail] = useState<Awaited<ReturnType<WiringApi['getDetail']>>>(null)
  const image = list[at]
  const imageId = image?.id
  useEffect(() => {
    if (imageId === undefined) return
    let live = true
    api.getDetail(imageId).then(value => { if (live) setDetail(value) }, () => { if (live) setDetail(null) })
    return () => { live = false }
  }, [api, imageId])
  const [adding, setAdding] = useState(false)
  /** 引导版：指到哪个主色，「按颜色找」就用哪个（换图时回到第一主色） */
  const [hoverColor, setHoverColor] = useState<number | null>(null)
  /** 手机：右上角「⋯」打开的「更多」菜单开着没有 */
  const [moreOpen, setMoreOpen] = useState(false)
  useEffect(() => { setHoverColor(null); setMoreOpen(false) }, [imageId])
  const [draft, setDraft] = useState('')
  // 详情页的信息区操作：删除这张图 / 项目详情页顶部的项目操作
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [projectMenu, setProjectMenu] = useState<MenuPoint | null>(null)
  const [renamingProject, setRenamingProject] = useState(false)
  const [deletingProject, setDeletingProject] = useState(false)
  // 进入时第一屏看得到的图（null = 还没量，先都带 data-reveal）；量完以后其余的图去掉 data-reveal，滚出来直接显示
  const [firstScreen, setFirstScreen] = useState<Set<number> | null>(null)
  // 进入后有没有滚到过别的图：滚过以后文字不再逐词出现（滑过来的新详情页也直接显示，不逐词播）
  const [moved, setMoved] = useState(!!enterFrom)
  const sectionRef = useRef<HTMLElement>(null)

  const touch = useTouchLayout()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // 「加入项目」「更多」面板开着时，Esc 先关面板，不会一下退到素材库
      if (choosingProject) { setChoosingProject(false); return }
      if (moreOpen) { setMoreOpen(false); return }
      if (!adding) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, adding, choosingProject, moreOpen])

  // 点开哪张就直接停在哪张（用原有滚动的瞬移）
  useLayoutEffect(() => {
    const el = plates.current[index]
    const y = !mobile && !single && el && index > 0 ? el.getBoundingClientRect().top + window.scrollY - 24 : 0
    scrollToInstant(y)
    const seen = new Set<number>()
    plates.current.forEach((p, i) => { if (!p) return; const r = p.getBoundingClientRect(); if (r.top < window.innerHeight && r.bottom > 0) seen.add(i) })
    setFirstScreen(seen)
    back.current?.focus({ preventScroll: true })
    // 只在打开时定位一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const selectImage = (next: number) => {
    setAt(next)
    setMoved(true)
    setAdding(false)
    setChoosingProject(false)
    scrollToInstant(0)
  }

  // ===== 手机：左右滑换上一张 / 下一张 =====
  // 跟手：整页内容（不含钉在屏幕上的底部操作条和「⋯」）跟着手指横移；松手过了 1/4 屏宽或甩得够快就吸到下一张，
  // 否则弹回原位；到头时只跟手 1/4，松手弹回，不切换。
  // 上下滚动交给浏览器：.m-ld 是 touch-action: pan-y（phone.css），手指先横着动时浏览器不滚页面，先竖着动时浏览器滚、我们收到 pointercancel。
  // 「同一组还有 N 张」那排小图、标签行自己会横滑（它们是自己的滚动区，不受 pan-y 限制），从那里开始的手势这里不接。
  const canPrev = single ? !!browse?.prev : at > 0
  const canNext = single ? !!browse?.next : at < list.length - 1
  const swipe = useRef({ canPrev, canNext, go: (_delta: 1 | -1) => {}, busy: false })
  swipe.current.canPrev = canPrev
  swipe.current.canNext = canNext
  swipe.current.busy = moreOpen || choosingProject || adding
  swipe.current.go = delta => {
    if (single) { browse?.go(delta); return }
    selectImage(at + delta)
    slideIn(delta)
  }
  /** 新的一张从 delta 那一侧滑进来、吸到原位 */
  const slideIn = (delta: 1 | -1) => {
    const el = sectionRef.current
    if (!el) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { el.classList.remove('is-swiping', 'is-settling'); return }
    el.classList.remove('is-settling')
    el.classList.add('is-swiping')
    el.style.setProperty('--ld-swipe-ms', `${SWIPE_MS}ms`)
    el.style.setProperty('--ld-swipe-x', `${delta * el.clientWidth}px`)
    requestAnimationFrame(() => requestAnimationFrame(() => {
      el.classList.add('is-settling')
      el.style.setProperty('--ld-swipe-x', '0px')
      window.setTimeout(() => el.classList.remove('is-swiping', 'is-settling'), SWIPE_MS + 40)
    }))
  }
  // 滑过来的新详情页：从那一侧进场
  useLayoutEffect(() => {
    if (phone && enterFrom) slideIn(enterFrom)
    // 只在打开时进场一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    const el = sectionRef.current
    if (!phone || !el) return
    let start: { x: number; y: number; t: number; id: number } | null = null
    let horizontal: boolean | null = null
    /** 手指横移了多少（判断换不换）；页面实际走的是它乘阻尼 */
    let dx = 0
    let timer = 0
    const reduce = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches
    el.style.setProperty('--ld-swipe-ms', `${SWIPE_MS}ms`)
    const put = (x: number, settle: boolean) => {
      el.classList.add('is-swiping')
      el.classList.toggle('is-settling', settle && !reduce())
      el.style.setProperty('--ld-swipe-x', `${x}px`)
    }
    const rest = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => el.classList.remove('is-swiping', 'is-settling'), reduce() ? 0 : SWIPE_MS + 40)
    }
    const down = (event: PointerEvent) => {
      if (event.pointerType === 'mouse' || !event.isPrimary || swipe.current.busy) return
      if ((event.target as Element | null)?.closest(NO_SWIPE)) return
      window.clearTimeout(timer)
      start = { x: event.clientX, y: event.clientY, t: event.timeStamp, id: event.pointerId }
      horizontal = null
      dx = 0
    }
    const move = (event: PointerEvent) => {
      if (!start || event.pointerId !== start.id) return
      const mx = event.clientX - start.x
      const my = event.clientY - start.y
      if (horizontal === null) {
        if (Math.hypot(mx, my) < SWIPE_SLOP) return
        horizontal = Math.abs(mx) > Math.abs(my) * SWIPE_RATIO
        if (!horizontal) { start = null; return }
      }
      const edge = (mx > 0 && !swipe.current.canPrev) || (mx < 0 && !swipe.current.canNext)
      dx = mx
      put(mx * (edge ? SWIPE_EDGE : SWIPE_FOLLOW), false)
    }
    const up = (event: PointerEvent) => {
      if (!start || event.pointerId !== start.id) return
      start = null
      if (!horizontal) return
      const width = el.clientWidth || window.innerWidth
      const delta: 1 | -1 = dx < 0 ? 1 : -1
      const can = delta === 1 ? swipe.current.canNext : swipe.current.canPrev
      if (can && Math.abs(dx) > width * SWIPE_COMMIT) {
        put(-delta * width, true)
        window.setTimeout(() => swipe.current.go(delta), reduce() ? 0 : SWIPE_MS)
      } else {
        put(0, true)
        rest()
      }
    }
    const cancel = (event: PointerEvent) => {
      if (!start || event.pointerId !== start.id) return
      start = null
      if (horizontal) { put(0, true); rest() }
    }
    el.addEventListener('pointerdown', down)
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerup', up)
    el.addEventListener('pointercancel', cancel)
    return () => {
      window.clearTimeout(timer)
      el.removeEventListener('pointerdown', down)
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerup', up)
      el.removeEventListener('pointercancel', cancel)
    }
  }, [phone])

  // 当前张 = 跨过视口中线的那张；页面滚动由 Lenis 驱动，这里只读位置
  useEffect(() => {
    // 只看一张时页面上只有这一张，不按滚动位置换「当前张」
    if (mobile || single) return
    let raf = 0
    let firstAt: number | null = null
    const pick = () => {
      raf = 0
      const mid = window.innerHeight / 2
      let best = 0
      plates.current.forEach((el, i) => { if (el && el.getBoundingClientRect().top <= mid) best = i })
      if (firstAt === null) firstAt = best
      else if (best !== firstAt) setMoved(true)
      setAt(best)
    }
    const kick = () => { if (!raf) raf = requestAnimationFrame(pick) }
    pick()
    window.addEventListener('scroll', kick, { passive: true })
    return () => { window.removeEventListener('scroll', kick); if (raf) cancelAnimationFrame(raf) }
  }, [mobile, single])

  // 图先出，文字紧跟着（晚 160ms）逐词出现。原来要等图完全出完再等一下，点开一张图要一秒多才看到字
  const textAt = 160

  if (!image) return null
  const actual = detail?.id === image.id ? detail : null
  const current = actual ? mergeDetail(image, actual) : image
  const originalPost = actual?.posts.find(value => value.id === current.post_id) ?? actual?.posts[0]
  const originalUrl = originalPost ? api.sourceUrl(originalPost) : null

  const post = postOf(current)
  const subject = (current.subject ?? []).filter(Boolean)
  const saved = savedLabel(current.saved_at)
  const imported = savedLabel(current.createdAt)
  const source = sourceLabel(current)
  const local = source === '本地'
  // 什么分析都没有时的标题：不出现「本地」
  const plainTitle = local
    ? (saved ? `${saved}收藏的图` : imported ? `${imported}导入的图` : '未命名的图')
    : (saved ? `${source} · ${saved}` : source)
  // 标题先用这张图自己的 title；没有再用帖子标题，都没有才用来源 / 日期，不拿标签拼
  const ownTitle = meaningful(current.title) ? current.title.trim() : ''
  const ownPostTitle = meaningful(current.post_title) ? current.post_title.trim() : ''
  const title = ownTitle || ownPostTitle || plainTitle
  const imageTheme = [subject.join('、'), ...(current.style_tags ?? []).slice(0, 2)].filter(Boolean).join(' · ')
  // 主题行只是把标题重复一遍时就不显示
  const theme = imageTheme === title ? '' : imageTheme
  const intro = meaningful(current.description) ? current.description.trim() : ownPostTitle !== title ? ownPostTitle : ''
  const notes = [intro && { key: 'intro', text: intro }, theme && { key: 'theme', text: theme }].filter(Boolean) as { key: string; text: string }[]
  /** 手机描述：介绍和主题合成一段（换行隔开），不带 i. ii. 编号 */
  const descText = notes.map(note => note.text).join('\n')
  // 详情页长相退回最初版；唯一的文字改动：「收藏」一栏原来显示「未知」，改成「未记录收藏夹」+ 一行小字说明原因
  const savedNote = imported ? `导入时没带收藏夹信息 · ${imported}导入` : '导入时没带收藏夹信息'
  // 分析中提示：详情已经取回、但还没有分析结果时才显示
  const analysisNote = actual && !actual.analysis ? pendingAnalysisNote(analysis, current.id) : null
  const sourceText = touch && local ? '自己导入' : source
  const colors = current.palette.slice(0, 3)
  const currentTags = tags.get(current.id) ?? current.tags ?? []
  // 「(最后一张)」是整组往下翻时用的；只看一张时不说
  const last = !single && at === list.length - 1
  /** 「更多 → 保存 ↓」：和电脑上那条下载链接是同一个地址和文件名 */
  const download = () => {
    const link = document.createElement('a')
    link.href = current.src
    link.download = actual?.download.filename || current.source || fileName(current.src)
    document.body.appendChild(link)
    link.click()
    link.remove()
  }
  const addTag = async () => {
    const t = draft.trim()
    setDraft('')
    setAdding(false)
    if (!t || currentTags.includes(t)) return
    try {
      const saved = await api.setAnnotation(current.id, { tags: [...currentTags, t] })
      if (saved?.tags) setTags(previous => new Map(previous).set(current.id, saved.tags!))
    } catch { onToast('标签保存失败') }
  }

  // iPad（触屏、比手机宽、走逐张往下滚的布局）：左边大图，右边一栏信息；两侧栏收进同一个吸顶信息栏。
  // 电脑和手机不走这条，DOM 和原来一模一样
  const pane = touch && !phone && !mobile
  const leftAside = (
      <aside className="m-ld-side m-ld-left">
        <div className="m-ld-top">
          <button type="button" ref={back} className="m-ld-back" onClick={onClose} aria-label="返回">
            {/* 拆成两段只为手机：手机上收成 44px 圆形按钮、只留箭头；电脑上照旧是「← 返回」 */}
            <span className="m-ld-back-arrow" aria-hidden="true">←</span> <span className="m-ld-back-word">返回</span>
          </button>
          {notes[0]?.key === 'intro' && (
            <p className="m-ld-note m-ld-intro" key={`intro-${current.id}`}><i>{ROMAN[0]}</i><Words text={notes[0].text} moved={moved} /></p>
          )}
        </div>
        {notes.find(n => n.key === 'theme') && (
          <p className="m-ld-note m-ld-theme" key={`theme-${current.id}`}>
            <i>{ROMAN[notes.findIndex(n => n.key === 'theme')]}</i><Words text={theme} moved={moved} />
          </p>
        )}
        <div className="m-ld-bottom">
          {/* 只看一张时，同帖的图在大图下面那排能点的小图里，这里不再重复 */}
          {!single && post.length > 1 && <p className="m-ld-cap">(同帖 {post.length} 张)</p>}
          <div className="m-ld-foot">
            {!single && <div className="m-ld-thumbs">
              {post.slice(0, 4).map((p, i) => (
                <img key={`${p.id}-${i}`} src={thumbnailSrc(p.src)} alt="" className={p.id === current.id ? 'is-on' : ''} />
              ))}
            </div>}
            <p className="m-ld-links">
              <a href={current.src} download={actual?.download.filename || current.source || fileName(current.src)}>保存 ↓</a>
            </p>
          </div>
        </div>
      </aside>
  )
  return (
    <section ref={sectionRef} className={pane ? 'm-ld m-ld--pane' : 'm-ld'} aria-label="图片详情" style={{ '--ld-text-at': `${textAt}ms` } as React.CSSProperties}>
      {!pane && leftAside}
      {/* 窄窗口的电脑仍把这排小图放在顶上；手机挪到详情信息下面（见本节点末尾的 .m-ld-group） */}
      {mobile && !phone && list.length > 1 && <ThumbnailStrip list={list} index={at} onSelect={selectImage} active />}
      <div className="m-ld-center" style={onProjectEdit && !mobile && list.length > 1 ? { paddingBottom: '50vh' } : undefined}>
        {list.map((p, i) => (!(mobile || single) || i === at) && (
          <div className="m-ld-plate" key={`${p.id}-${i}`}
            data-reveal={((mobile || single) ? !moved : firstScreen === null || firstScreen.has(i)) ? '' : undefined} ref={el => { plates.current[i] = el }}>
            <img src={p.src} alt="" width={p.w} height={p.h} loading={mobile || single || Math.abs(i - index) < 2 ? 'eager' : 'lazy'} />
          </div>
        ))}
        {/* 电脑 / iPad：同帖其它图放在大图下面一排，点哪张就换成哪张（手机用顶上那排） */}
        {single && !mobile && list.length > 1 && (
          <div className="m-ld-post">
            <p className="m-ld-post-cap">同帖还有 {list.length - 1} 张</p>
            <ThumbnailStrip list={list} index={at} onSelect={selectImage} active />
          </div>
        )}
      </div>

      <aside className="m-ld-side m-ld-right" data-lenis-prevent={pane ? '' : undefined}>
        {/* iPad：左栏（返回、描述、保存 / 原帖）收进右栏，整栏一起吸顶、可单独滚动 */}
        {pane && leftAside}
        <div className="m-ld-top">
          {/* 项目详情页顶部：项目名 + 「…」（改名 / 删除），手机和电脑都看得见 */}
          {project && (
            <p className="m-ld-projectbar">
              <span className="m-ld-projectname">{project.name}</span>
              <button type="button" className="m-ld-projmore" aria-label="项目操作" aria-haspopup="menu"
                onClick={event => {
                  const box = event.currentTarget.getBoundingClientRect()
                  setProjectMenu({ x: box.right - 6, y: box.bottom + 6 })
                }}>…</button>
            </p>
          )}
          <div className="m-ld-index">
            <span className="m-ld-no" key={`no-${at}`}><Words text={pad2(at + 1)} moved={moved} /></span>
            <span className="m-ld-range">{pad3(1)} – {pad3(list.length)}</span>
          </div>
          {/* 不写“往下滚”这类解释界面的话；到头了才说一声 */}
          <p className="m-ld-hint">{last ? '(最后一张)' : ' '}</p>
          <p className="m-ld-tags">
            {currentTags.map((t, i) => <span key={t}>{i > 0 && ' · '}{t}</span>)}
            {currentTags.length > 0 && ' · '}
            {adding ? (
              <input className="m-ld-taginput" autoFocus value={draft} aria-label="新标签"
                onChange={e => setDraft(e.target.value)}
                onBlur={addTag}
                onKeyDown={e => {
                  if (e.key === 'Enter') addTag()
                  if (e.key === 'Escape') { setDraft(''); setAdding(false) }
                }} />
            ) : (
              <button type="button" className="m-ld-addtag" onClick={() => setAdding(true)}>+ 标签</button>
            )}
          </p>
          {onProjectEdit && <p className="m-ld-more" aria-label="项目图片操作">
            <button type="button" disabled={projectEditPending || at === 0} onClick={() => onProjectEdit(current, 'previous')}>前移</button>
            {' · '}
            <button type="button" disabled={projectEditPending || at === list.length - 1} onClick={() => onProjectEdit(current, 'next')}>后移</button>
            {' · '}
            <button type="button" disabled={projectEditPending} onClick={() => onProjectEdit(current, 'remove')}>从项目移除</button>
          </p>}
        </div>
        <div className="m-ld-bottom" key={`spec-${current.id}`}>
          {analysisNote && (
            <p className="ds-analysis-note" role="status">
              <span className="ds-analyzing">{analysisNote.label}</span>
              <span>{analysisNote.note}</span>
            </p>
          )}
          <h1 className="m-ld-title"><Words text={title} moved={moved} /></h1>
          <dl className="m-ld-spec">
            <div><dt>来源</dt><dd><Words text={sourceText} moved={moved} /></dd></div>
            {/* 链接导入的图存了原帖地址；旧图没有地址就不显示这一行 */}
            {originalUrl && <div><dt>原帖</dt><dd>
              <a className="m-ld-source" href={originalUrl} target="_blank" rel="noopener noreferrer">打开 →</a>
            </dd></div>}
            <div className={saved ? undefined : 'm-ld-spec-saved'}><dt>收藏</dt><dd>
              {saved
                ? <Words text={saved} moved={moved} />
                : <span className="m-ld-unrecorded">
                    <span>未记录收藏夹</span>
                    <small title={savedNote}>{savedNote}</small>
                  </span>}
            </dd></div>
            <div><dt>主色</dt><dd className="m-ld-colors">
              {colors.map((c, i) => (
                <button type="button" key={c.hex} style={{ background: c.hex }} title={c.hex}
                  className={REFINE && i === (hoverColor ?? 0) ? 'is-on' : undefined}
                  onPointerEnter={() => setHoverColor(i)} onFocus={() => setHoverColor(i)}
                  aria-label={`按 ${c.hex} 找图`} onClick={() => onColor(c)} />
              ))}
            </dd></div>
          </dl>
          {REFINE && colors.length > 0 ? (
            // 引导版：「按颜色找」和「找相似」并排，按钮里的色点跟着指到的色块变；默认用第一主色
            <div className="m-ld-finds">
              <button type="button" className="m-ld-similar" onClick={() => onSimilar(current)}>找相似</button>
              <button type="button" className="m-ld-similar m-ld-samecolor" onClick={() => onColor(colors[hoverColor ?? 0] ?? colors[0])}>
                <span className="m-ld-samecolor-dot" style={{ background: (colors[hoverColor ?? 0] ?? colors[0]).hex }} aria-hidden="true" />按颜色找
              </button>
            </div>
          ) : (
            <button type="button" className="m-ld-similar" onClick={() => onSimilar(current)}>找相似</button>
          )}
          <p className="m-ld-more">
            <button type="button" onClick={() => projects.length ? setChoosingProject(true) : onToast('没有可加入的项目')}>加入项目</button>
            {' · '}
            <button type="button" title={REFINE ? '弹出一块置顶的画板，边做设计边看参考' : undefined}
              onClick={() => onPip ? onPip(current) : onToast('还没接上')}>{REFINE ? '打开参考画板' : '打开小窗'}</button>
          </p>
          {/* 信息区最下方：看得见的文字按钮，不是实心按钮 */}
          <p className="m-ld-more m-ld-more-danger">
            <button type="button" className="m-ld-danger" onClick={() => setConfirmDelete(true)}>删除这张图</button>
          </p>
        </div>
      </aside>

      {/* ===== 手机专用的几块（电脑不渲染）。手机上页面从上到下：大图 → 标题 → 标签 → 描述 → 主色 → 同一组 → 来源；
          上面两栏里的参数表、编号、描述原样、保存 / 原帖、项目内排序、删除都在 phone.css 里藏起来，
          保存 / 原帖 / 前移 · 后移 · 从项目移除 / 删除收进顶栏右上角「⋯」打开的「更多」 ===== */}
      {/* 描述：全文直接显示，有多长显示多长；去掉 i. ii. 编号 */}
      {phone && descText && (
        <div className="m-ld-desc" key={`desc-${current.id}`}>
          <p className="m-ld-desc-text">{descText}</p>
        </div>
      )}
      {/* 主色一行：色块 + 「按这个颜色找 →」 */}
      {phone && colors.length > 0 && (
        <div className="m-ld-colorrow">
          <span className="m-ld-colorrow-label">主色</span>
          <span className="m-ld-colors">
            {colors.map((c, i) => (
              <button type="button" key={c.hex} style={{ background: c.hex }} title={c.hex}
                className={REFINE && i === (hoverColor ?? 0) ? 'is-on' : undefined}
                aria-label={`按 ${c.hex} 找图`} onClick={() => { setHoverColor(i); onColor(c) }} />
            ))}
          </span>
          <button type="button" className="m-ld-colorfind" onClick={() => onColor(colors[hoverColor ?? 0] ?? colors[0])}>按这个颜色找 →</button>
        </div>
      )}
      {/* 同一组的其它图：一排大一点的小图，左右滑；只有一张时整块不显示 */}
      {phone && list.length > 1 && (
        <div className="m-ld-group">
          <p className="m-ld-group-cap">{single ? '同一组' : '这个项目'}还有 {list.length - 1} 张</p>
          <ThumbnailStrip list={list} index={at} onSelect={selectImage} active />
        </div>
      )}
      {/* 来源：保留，改成一行小字 */}
      {phone && <p className="m-ld-srcline">
        来源 · {sourceText}
        {originalUrl && <> · <a className="m-ld-source" href={originalUrl} target="_blank" rel="noopener noreferrer">原帖：打开 →</a></>}
      </p>}

      {/* 手机：底部操作条只有两颗——「加入项目」主按钮、「找相似」次按钮 */}
      {phone && (
        <div className="m-ld-bar" role="toolbar" aria-label="这张图的操作">
          {/* 点开底部升起的项目面板（占屏幕高度 70%），不再用系统下拉 */}
          <button type="button" className="ds-btn ds-btn--primary m-ld-bar-pick"
            onClick={() => projects.length ? setChoosingProject(true) : onToast('还没有项目：先去「项目」页新建一个')}>加入项目</button>
          <button type="button" className="ds-btn ds-btn--secondary" onClick={() => onSimilar(current)}>找相似</button>
        </div>
      )}
      {/* 手机：「⋯」钉在顶栏「TADA · 素材库」那一行的最右边，只有图标、没有底色，点击区 44×44 */}
      {phone && (
        <button type="button" className="m-ld-topmore" aria-label="更多" aria-haspopup="dialog"
          onClick={() => setMoreOpen(true)}><Icon icon={Ellipsis} size={22} /></button>
      )}
      {phone && moreOpen && (
        <div className="m-proj-pick m-more-sheet">
          <div className="m-proj-pick-bg" onClick={() => setMoreOpen(false)} />
          <div className="m-proj-pick-panel" role="dialog" aria-modal="true" aria-labelledby="m-more-title">
            <div className="m-proj-pick-head">
              <h2 id="m-more-title">更多</h2>
              <button type="button" className="m-close m-proj-pick-close" aria-label="关闭" onClick={() => setMoreOpen(false)}>×</button>
            </div>
            <ul className="m-proj-pick-list">
              <li><button type="button" autoFocus onClick={() => { setMoreOpen(false); download() }}>保存 ↓</button></li>
              {originalUrl && <li><button type="button" onClick={() => {
                setMoreOpen(false)
                window.open(originalUrl, '_blank', 'noopener,noreferrer')
              }}>打开原帖 ↗</button></li>}
              {onProjectEdit && <>
                <li><button type="button" disabled={projectEditPending || at === 0}
                  onClick={() => { setMoreOpen(false); onProjectEdit(current, 'previous') }}>前移</button></li>
                <li><button type="button" disabled={projectEditPending || at === list.length - 1}
                  onClick={() => { setMoreOpen(false); onProjectEdit(current, 'next') }}>后移</button></li>
                <li><button type="button" disabled={projectEditPending}
                  onClick={() => { setMoreOpen(false); onProjectEdit(current, 'remove') }}>从项目移除</button></li>
              </>}
              <li><button type="button" className="m-more-danger"
                onClick={() => { setMoreOpen(false); setConfirmDelete(true) }}>删除这张图</button></li>
            </ul>
          </div>
        </div>
      )}

      {/* 「加入项目」选择面板：电脑 / iPad 居中，至少 480px 宽；手机从底部升起、占屏幕高度 70%
          （样式见 project-picker.css，手机在 styles/phone.css） */}
      {projectMenu && project && (
        <ActionMenu point={projectMenu} title={project.name} onClose={() => setProjectMenu(null)}
          actions={[
            { key: 'rename', label: '改名', act: () => setRenamingProject(true) },
            { key: 'delete', label: '删除', danger: true, act: () => setDeletingProject(true) },
          ]} />
      )}
      {renamingProject && project && (
        <RenameDialog initial={project.name} onClose={() => setRenamingProject(false)}
          onSubmit={async name => await onRenameProject?.(project, name) ?? false} />
      )}
      {deletingProject && project && (
        <ConfirmDialog title="删除项目" text="删除后可以在后台恢复，确定删除这个项目？" onClose={() => setDeletingProject(false)}
          onConfirm={async () => {
            const done = await onDeleteProject?.(project) ?? false
            if (done) onClose()
            return done
          }} />
      )}
      {confirmDelete && (
        <ConfirmDialog title={phone ? '删除这张图？' : '删除这张图'} text={phone ? '删除后可以找回。' : '删除后可以找回，确定删除吗？'}
          textConfirm={phone} onClose={() => setConfirmDelete(false)}
          onConfirm={async () => {
            const done = await onDeleteImage?.(current) ?? false
            if (done) onClose()
            return done
          }} />
      )}

      {choosingProject && projects.length > 0 && (
        <div className="m-proj-pick">
          <div className="m-proj-pick-bg" onClick={() => setChoosingProject(false)} />
          <div className="m-proj-pick-panel" role="dialog" aria-modal="true" aria-labelledby="m-proj-pick-title">
            <div className="m-proj-pick-head">
              <h2 id="m-proj-pick-title">加入项目</h2>
              <button type="button" className="m-close m-proj-pick-close" aria-label="关闭" onClick={() => setChoosingProject(false)}>×</button>
            </div>
            <p className="m-proj-pick-help">把这张图加进哪个项目？</p>
            <ul className="m-proj-pick-list">
              {projects.map((project, index) => (
                <li key={project.id}>
                  <button type="button" autoFocus={index === 0}
                    onClick={() => { onAdd(Number(project.id), current.id); setChoosingProject(false) }}>
                    <span className="m-proj-pick-name">{project.name}</span>
                    <span className="m-proj-pick-count">{project.items.length} 张</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  )
}

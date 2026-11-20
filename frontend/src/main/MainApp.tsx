import { RollingLabel } from './RollingLabel'
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { X } from 'lucide'
import { Icon } from '../lab/Icon'
import { thumbnailSrc } from '../imageThumbnail'
import { useManifest, type LabImage, type Swatch } from '../lab/data'
import { ActivityPage } from './ActivityPage'
import { AppHeader, HeaderColor, type AppPage } from './AppHeader'
import { Detail } from './Detail'
import { GlassMenu } from './GlassMenu'
import { GradualBlur } from './GradualBlur'
import { Library } from './Library'
import { ImportDialog } from './ImportDialog'
import { pageView, startTelemetry, track } from './telemetry'
import { ImportResult } from './ImportResult'
import { analyzingIds, useAnalysisStatus } from './useAnalysisStatus'

import { Projects, type Project, type ProjectsHandle } from './Projects'
import { SettingsDrawer } from './SettingsDrawer'
import { SettingsPage } from './SettingsPage'
import { useScrollReveal } from './reveal'
import { scrollToInstant, scrollToY, startSmoothScroll } from './smooth'
import { PAGE, installMotionVars } from './motion'
import { SORTS, type SortKey } from './sorts'
import { PLATFORMS, sourceLabel } from './meta'
import { useDemoMode } from '../api/mode'
import type { Relevance } from '../search/contract'
import { flushSync } from 'react-dom'
import type { ImageItem } from '../api'
import PipButton, { type PipHandle } from '../PipButton'
import { PhoneBoard } from './PhoneBoard'
import { PhonePending } from './PhonePending'
import { PhoneTabBar } from './PhoneTabBar'
import { useNarrowPhoneLayout, usePhoneLayout } from './useMobileLayout'
import { useWiringApi, useWiringImport, useWiringResource } from '../api/useWiring'
import { errorText } from '../api/errors'
import type { WiringApi } from '../api/wiring'
import type { RecommendationResponse } from '../recommendation/contract'
import { collectDroppedFiles } from '../localImport/collect'
import { demoFixture, demoReferenceResult, emptyManifest, fromLibrary, hitImages, projectView } from './wiringData'
import { deleteImageRequest, deleteProjectRequest, renameProjectRequest } from './itemActionsApi'
import './main.css'
import './app-header.css'
import './fable.css'
import './mobile.css'
import '../pip/fable-pip.css'
import './refine.css'
// 设计系统组件层：放在所有页面样式之后，统一按钮、下拉、标签、提示（只用 styles/tokens.css 的令牌）
import '../styles/design-system.css'
// 评审后补的入口和提示：待确认提示条、当前筛选、搜索框中文小字、卡片标题两行
import '../styles/entries.css'
// 手机专用（触屏 + 窄屏），排在最后；电脑不会命中
import '../styles/phone.css'
import { REFINE } from './refine'

// 动效变量（--mo-*）装到 <html> 上，CSS 和注入的 GlassFolder 样式都读这一份
installMotionVars()

type View = 'library' | 'projects' | 'activity' | 'settings'
/** 提示条后面跟的一个动作（整条提示可点） */
type ToastAction = { label: string; act: () => void }
/** 打开的详情：哪张图、从哪个列表进来、在列表里第几张 */
/** 手机项目页「整理」后的顺序存在这里（项目 id 数组） */
const PROJECT_ORDER_KEY = 'm-project-order'
type Opened = {
  list: LabImage[]
  index: number
  project?: Project
  /**
   * 手机左右滑换图用：进详情前看到的那个列表和被点的是第几张（素材库 = 当前排序 + 筛选 / 搜索结果，日历 = 每日灵感那几张）。
   * 项目进来的不用它——项目详情本身就是整个项目，Detail 自己按 list 换。
   */
  browse?: { list: LabImage[]; index: number }
  /** 滑过来的方向：1 = 从右边滑进来（下一张），-1 = 从左边（上一张）；新详情页按它做进场 */
  enter?: 1 | -1
}
/** 找图结果里每张图的相关度：strong 是像的，weak 是「不太像但可能有用」；老后端没有这个字段，缺省按 strong。 */
export type LibraryResult = { text: string; items: LabImage[]; relevance?: Record<number, Relevance>; groups?: LabImage[][]; reasons?: Record<number, string>; referenceNotes?: Record<number, string>[]; referenceSource?: string; projectId?: number; similar?: boolean; docked?: boolean }

function toPipImage(item: LabImage): ImageItem {
  return {
    id: item.id,
    post_id: item.post_id ?? item.id,
    position: item.position ?? 0,
    url: item.src,
    width: item.w || null,
    height: item.h || null,
    original_name: item.source || null,
    created_at: item.createdAt ?? '',
    platform: (item.platform as ImageItem['platform'] | undefined) ?? 'local',
  }
}

/**
 * 关详情后回到打开前的滚动位置。详情打开时 .m-view 是 hidden，瀑布流要等重新显示后再量一次才有高度，
 * 刚 commit 的几帧页面还很矮，scrollToInstant 会被夹到矮页面的底部；所以整段重新排版结束前一直压着这个位置
 * （约 1 秒），而不是「到位一次就收手」——搜索行从大字收到正文大小那一下会让页面再变高一次，
 * 收早了就会被带偏几十像素。用户一滚（wheel / 触摸 / 键）就立刻停手，不和用户抢滚动。
 */
function restoreScroll(y: number) {
  const until = performance.now() + 1000
  let cancelled = false
  let raf = 0
  const stop = () => {
    if (raf) cancelAnimationFrame(raf)
    raf = 0
    window.removeEventListener('wheel', cancel)
    window.removeEventListener('touchstart', cancel)
    window.removeEventListener('keydown', cancel)
  }
  const cancel = () => { cancelled = true; stop() }
  window.addEventListener('wheel', cancel, { passive: true })
  window.addEventListener('touchstart', cancel, { passive: true })
  window.addEventListener('keydown', cancel)
  const step = () => {
    raf = 0
    if (cancelled) return
    scrollToInstant(y)
    if (performance.now() < until) raf = requestAnimationFrame(step)
    else stop()
  }
  raf = requestAnimationFrame(step)
  return cancel
}

const loadLibrary = async (api: WiringApi) => {
  const [images, counts] = await Promise.all([api.listImages(), api.getLibraryCounts()])
  const items = images.map(fromLibrary)
  return { ...emptyManifest, cards: items, items, total: counts.collections, visual_ref: counts.references }
}
const loadProjects = async (api: WiringApi) => {
  const projects = await api.listProjects()
  // 单个项目 404（取列表之后刚被删、或设备上还是旧列表）不能拖垮整页：
  // 之前用 Promise.all，一个失败就整批 reject，项目页会变成一片空白。
  const details = await Promise.allSettled(projects.items.map(project => api.getProject(project.id)))
  return details.flatMap(result => result.status === 'fulfilled' && result.value ? [result.value] : [])
}
const loadPending = (api: WiringApi) => api.listUncertain()


/** /main：注册流程之后的主界面 demo。一个主页 + 一个项目页，其余全部是浮层。 */
export default function MainApp() {
  const demoManifest = useManifest(REFINE)
  const [demo, setDemo] = useDemoMode()
  const fixture = useMemo(() => demoManifest && demoManifest !== 'missing' ? demoFixture(demoManifest) : undefined, [demoManifest])
  const api = useWiringApi(demo, demo ? fixture : undefined)
  const library = useWiringResource(api, loadLibrary)
  const manifest = demo ? demoManifest === 'missing' ? emptyManifest : demoManifest : library.value.status === 'ready' ? library.value.data : emptyManifest
  // 首屏先让图片走：项目、待确认、分析状态这几个接口在公网上各占 10～17 秒连接，
  // 等素材库第一张图显示出来（或确定没有图 / 读失败 / 离开素材库）之后再请求
  const [firstShown, setFirstShown] = useState(false)
  const markFirstShown = useCallback(() => setFirstShown(true), [])
  const extrasOn = demo || firstShown
  const projectData = useWiringResource(api, loadProjects, extrasOn)
  const pendingData = useWiringResource(api, loadPending, extrasOn)
  const demoReviewed = useMemo(() => demo ? new Set<number>() : null, [demo, api])
  const pendingChanged = useRef(false)
  const importer = useWiringImport(api, { onImported: library.reload })
  /** 分析状态（只在真实模式下请求）：卡片和详情页的「分析中」提示用 */
  const analysis = useAnalysisStatus(!demo && firstShown)
  const libraryStatus = library.value.status
  const libraryEmpty = library.value.status === 'ready' && library.value.data.cards.length === 0
  useEffect(() => {
    if (libraryStatus === 'error' || libraryEmpty) setFirstShown(true)
  }, [libraryStatus, libraryEmpty])
  // 保底：素材库读回来 20 秒后图还没显示出来，也不再压着这几个接口
  useEffect(() => {
    if (libraryStatus !== 'ready' || firstShown) return
    const timer = window.setTimeout(() => setFirstShown(true), 20000)
    return () => window.clearTimeout(timer)
  }, [libraryStatus, firstShown])
  const analyzing = useMemo(() => analyzingIds(analysis), [analysis])
  const [results, setResults] = useState<LibraryResult | null>(null)
  const [pendingSearch, setPendingSearch] = useState<number | null>(null)
  /** 正在进行的是找图还是要参考：加载中那句话不一样 */
  const [searchKind, setSearchKind] = useState<'find' | 'ref'>('find')
  /** 引导页示例搜索框跳过来时带的 ?q=；只消费一次，然后从地址栏去掉。 */
  const [initialQuery] = useState(() => new URLSearchParams(location.search).get('q')?.trim() ?? '')
  // 只把 ?q= 从地址栏去掉；真正的搜索在 Library 挂载后触发（避免在 MainApp 提前 return 之前加 Hook）。
  useEffect(() => {
    if (!initialQuery) return
    const url = new URL(window.location.href)
    url.searchParams.delete('q')
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash)
  }, [initialQuery])
  const requestVersion = useRef(0)
  const actionMode = useRef(api)
  actionMode.current = api
  const [view, setView] = useState<View>('library')
  // 匿名使用记录：设备编号 + 批量上报；失败静默，不影响页面
  useEffect(() => startTelemetry('library'), [])
  /** 手机：项目页里点开的那个项目（显示三列方格）；null = 全部项目列表 */
  const [phoneProjectId, setPhoneProjectId] = useState<string | null>(null)
  /** 手机项目页「整理」：开着时项目卡可以拖着换位置，点「完成」退出并保存 */
  const [arranging, setArranging] = useState(false)
  /**
   * 手机上自己排的项目顺序。后端还没有项目排序接口，先存在这台设备的浏览器里（localStorage）；
   * 接口补好以后只改 saveProjectOrder 这一处：在这里调用接口，读的时候改成用接口返回的顺序。
   */
  const [projectOrder, setProjectOrder] = useState<string[]>(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(PROJECT_ORDER_KEY) ?? '[]')
      return Array.isArray(saved) ? saved.filter((id): id is string => typeof id === 'string') : []
    } catch { return [] }
  })
  const saveProjectOrder = (ids: string[]) => {
    setProjectOrder(ids)
    try { window.localStorage.setItem(PROJECT_ORDER_KEY, JSON.stringify(ids)) } catch { /* 隐私模式存不了就只在这次打开里有效 */ }
    say('已保存项目顺序')
  }
  const [detail, setDetail] = useState<Opened | null>(null)
  const detailSnapshot = useRef(detail)
  detailSnapshot.current = detail
  const [projectEditPending, setProjectEditPending] = useState(false)
  const [hue, setHue] = useState<number | null>(null)
  const [wallRequest, setWallRequest] = useState(0)
  /** 主页工具条（筛选 / 排序）挪到顶栏右侧：菜单在顶栏里开 */
  const [filterOpen, setFilterOpen] = useState(false)
  const [colorOpen, setColorOpen] = useState(false)
  const [sort, setSort] = useState<SortKey>('time')
  /** 项目页的「新建项目 +」「单列 | 网格」挪到顶栏右侧 */
  const [projectForm, setProjectForm] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [projectLayout, setProjectLayout] = useState<'grid' | 'feed'>('grid')
  const projectsRef = useRef<ProjectsHandle | null>(null)
  /** 日历页的「每天 / 每周 / 累计」挪到顶栏右侧 */
  const [calendarGrain, setCalendarGrain] = useState<'day' | 'week' | 'total'>('day')
  const [pendingOpen, setPendingOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [reduce, setReduce] = useState(false)
  const reducedMotion = useReducedMotion()
  /** 提示条；带 action 时整条可以点（手机上「已加入「项目名」· 去看看 →」） */
  const [toast, setToast] = useState<{ text: string; action?: ToastAction } | null>(null)
  const [dragging, setDragging] = useState(false)
  const [keep, setKeep] = useState<Set<number>>(new Set())
  /** 待确认页标题下固定显示「已保留 N · 不是参考 N」，处理一批就实时累加 */
  const [pendingTally, setPendingTally] = useState({ keep: 0, drop: 0 })
  /** 已经处理过的图：API 还没刷回来之前先从网格里拿掉，计数立刻更新 */
  const [decidedIds, setDecidedIds] = useState<Set<number>>(new Set())
  const [platforms, setPlatforms] = useState<Set<string>>(new Set())

  const toastTimer = useRef<number | null>(null)
  const returnY = useRef(0)
  const cancelScrollRestore = useRef<(() => void) | null>(null)
  // 详情页用浏览器历史收口：打开时压一条 URL 不变的条目；后退 / 「← 返回」都退回到这条之前，
  // 由 popstate 统一关详情并把页面滚回打开前的位置。0 表示当前历史条目不是详情条目。
  const historySeq = useRef(0)
  const detailSeq = useRef(0)
  // popstate 是原生事件，读不到最新的 React state，用 ref 同步“详情开着没有”
  const detailOpenRef = useRef(false)
  // 自己调 history.back() 的次数：对应的 popstate 跳过，免得关掉之后又新打开的详情
  const selfBack = useRef(0)

  const say = useCallback((msg: string, action?: ToastAction) => {
    setToast({ text: msg, action })
    if (toastTimer.current) window.clearTimeout(toastTimer.current)
    // 能点的提示多留一会儿，来得及点
    toastTimer.current = window.setTimeout(() => setToast(null), action ? 4500 : 2400)
  }, [])

  // 手机上的参考画板：不开画布小窗，在本页显示两列图片（PhoneBoard）；切到别的页面时清掉
  const phone = usePhoneLayout()
  const narrowPhone = useNarrowPhoneLayout()
  const [phoneBoard, setPhoneBoard] = useState<{ projectId: number | null; name: string | null; items: LabImage[] } | null>(null)
  const boardOn = phone && phoneBoard !== null
  // 网页标题跟着页面走：只有手机参考画板这一页改标题，离开就换回来
  useEffect(() => {
    if (!boardOn) return
    const previous = document.title
    document.title = '参考画板 · TADA! 图然想起'
    return () => { document.title = previous }
  }, [boardOn])

  // 切页先回到顶，新页面再从下面长上来
  useEffect(() => {
    scrollToInstant(0)
  }, [view, boardOn, phoneProjectId])
  useScrollReveal(boardOn ? 'board' : view)

  // 整站滚动交给 Lenis：下拉有惯性，停下是缓的
  useEffect(() => startSmoothScroll(), [])

  // 浏览器后退：退回详情条目之前的同页条目时，关掉详情并回到打开前的滚动位置
  //（「← 返回」走 closeDetail：同样先退条目再恢复位置，对应那次 popstate 由 selfBack 跳过）
  useEffect(() => {
    const onPop = () => {
      if (selfBack.current > 0) { selfBack.current -= 1; return }
      if (!detailOpenRef.current) return
      detailOpenRef.current = false
      detailSeq.current = 0
      setDetail(null)
      cancelScrollRestore.current?.()
      cancelScrollRestore.current = restoreScroll(returnY.current)
    }
    window.addEventListener('popstate', onPop)
    return () => {
      window.removeEventListener('popstate', onPop)
      cancelScrollRestore.current?.()
    }
  }, [])

  // “打开小窗”：先用 flushSync 把目标项目/图片塞进 PipButton 的 props，再在同一个用户手势里开窗。
  type PipTarget = { projectId: number | null; name: string | null; items: LabImage[] }
  const pipRef = useRef<PipHandle | null>(null)
  const [pipTarget, setPipTarget] = useState<PipTarget>({ projectId: null, name: null, items: [] })
  const pipImages = useMemo(() => pipTarget.items.map(toPipImage), [pipTarget])
  const pipLibrary = useMemo(() => manifest?.items.map(toPipImage), [manifest])
  const openPip = (target: PipTarget) => {
    // 手机：画布用不了，也不另开标签页（另开的页面会残留图片、缩放条和工具栏），改在本页显示简单版
    if (phone) {
      setFilterOpen(false)
      setColorOpen(false)
      dismissDetail()
      setPhoneBoard(target.projectId === null ? { ...target, items: target.items.slice(0, 6) } : target)
      return
    }
    flushSync(() => setPipTarget(target))
    const pipName = REFINE ? '参考画板' : '参考小窗'
    if (!pipRef.current?.supported) { say(`这个浏览器打不开${pipName}：请用电脑上的 Chrome 或 Edge`); return }
    void pipRef.current.open().catch(error => say(error instanceof Error && error.message === '参考小窗被浏览器拦截'
      ? `${pipName}被浏览器拦截了：在地址栏右侧允许弹出窗口后再点一次` : `${pipName}没打开：请再点一次，或刷新页面后重试`))
  }

  // 导入结果不再用 2.4 秒就消失的提示条，改成页面底部的结果卡片（ImportResult），看完自己关

  if (manifest === null) return <div className="m-app" />
  const allPendingItems = pendingData.value.status === 'ready'
    ? pendingData.value.data.map(row => fromLibrary({ ...row, colors: [] })) : []
  const pendingItems = (demoReviewed ? allPendingItems.filter(item => !demoReviewed.has(item.id)) : allPendingItems)
    .filter(item => !decidedIds.has(item.id))
  const projects = projectData.value.status === 'ready' ? projectData.value.data.map(project => projectView(project, manifest.items)) : []
  // 详情页“打开小窗”：当前图在哪个项目里就开哪个项目；不在任何项目里就开“全部收藏”。
  const openPipForImage = (image: LabImage) => {
    const owner = projects.find(project => project.items.some(item => item.id === image.id))
    if (owner) openPip({ projectId: Number(owner.id), name: owner.name, items: owner.items })
    else openPip({ projectId: null, name: null, items: [image, ...manifest.items.filter(item => item.id !== image.id)] })
  }
  // 打开详情时在历史里压一条 URL 不变的条目，让浏览器后退能收口到「关详情 + 回原位」
  const pushDetailEntry = () => {
    const seq = ++historySeq.current
    window.history.pushState({ ...(window.history.state ?? {}), mDetail: seq }, '')
    detailSeq.current = seq
  }
  // 退掉自家那条详情历史；当前历史条目不是详情条目时什么都不做
  const popDetailEntry = () => {
    if (!detailSeq.current) return
    detailSeq.current = 0
    selfBack.current += 1
    window.history.back()
  }
  /** 页面内跳转等场景关详情：不负责回滚位置，只清状态并退掉多压的那条历史 */
  const dismissDetail = () => {
    if (!detailOpenRef.current) return
    detailOpenRef.current = false
    setDetail(null)
    popDetailEntry()
  }
  const navigate = (next: View) => {
    if (next === 'library' && view === 'settings' && !demo) library.reload()
    setFilterOpen(false)
    setColorOpen(false)
    dismissDetail()
    setPhoneBoard(null)
    setPhoneProjectId(null)
    // 整理到一半切走：Projects 卸载时自己保存，这里只把开关关掉
    setArranging(false)
    // 去别的页面（项目页要用项目数据）就不再等首屏图片
    if (next !== 'library') setFirstShown(true)
    setView(next)
    pageView(next)
  }
  /** 手机：去项目页并直接点开某个项目（三列方格） */
  const openPhoneProject = (id: string) => {
    navigate('projects')
    setPhoneProjectId(id)
  }
  // 同帖的图：有 post_id 就按帖内顺序列出来，没有就只有这一张
  const postOf = (item: LabImage) => item.post_id == null ? [item]
    : manifest.items.filter(it => it.post_id === item.post_id).sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
  // 详情放在页面里跟着整页滚：打开时记下原来的滚动位置，关掉再回到那里
  const openDetail = (item: LabImage, list: LabImage[] = [], index = list.indexOf(item), project?: Project) => {
    cancelScrollRestore.current?.()
    returnY.current = window.scrollY
    pushDetailEntry()
    detailOpenRef.current = true
    setFirstShown(true)
    track('detail_open', { from: project ? 'project' : 'library' })
    // 从搜索结果点开：让统计知道这个词点开过结果（不记图片 id）
    if (!project && results && !results.similar && results.text !== '按颜色找'
        && results.items.some(entry => entry.id === item.id)) {
      track('search_open', { q: results.text.slice(0, 80) })
    }
    if (!project) {
      // 不是从项目进来的：只看被点的这一张，同帖其它图作为下面那排小图（点了哪张就显示哪张）
      const browse = index >= 0 && list[index]?.id === item.id ? { list, index } : undefined
      setDetail({ ...singleOpened(item), browse })
      return
    }
    setDetail(index >= 0 && list[index] ? { list, index, project } : { list: [item], index: 0, project })
  }
  /** 只看一张时的详情：被点那张所在的同一帖 */
  const singleOpened = (item: LabImage): Opened => {
    const post = postOf(item)
    const at = post.findIndex(entry => entry.id === item.id)
    return at >= 0 ? { list: post, index: at } : { list: [item], index: 0 }
  }
  /** 手机左右滑：换成进详情前那个列表里的上一张 / 下一张（不再压历史，「返回」照样回到原来的位置） */
  const stepDetail = (delta: 1 | -1) => {
    track('detail_swipe', { dir: delta })
    setDetail(opened => {
      const browse = opened?.browse
      const item = browse?.list[browse.index + delta]
      if (!opened || !browse || !item) return opened
      return { ...singleOpened(item), browse: { list: browse.list, index: browse.index + delta }, enter: delta }
    })
  }
  // 「← 返回」/ Esc：退掉详情历史条目（交给 popstate 收口）并回到打开前的滚动位置
  const closeDetail = () => {
    const wasOpen = detailOpenRef.current
    dismissDetail()
    if (wasOpen) cancelScrollRestore.current = restoreScroll(returnY.current)
  }
  const editProjectImage = async (image: LabImage, action: 'previous' | 'next' | 'remove') => {
    const opened = detail
    const project = opened?.project
    if (!project || projectEditPending) return
    setProjectEditPending(true)
    try {
      const { image_ids } = await api.editProjectImage(Number(project.id), image.id, action)
      projectData.reload()
      if (detailSnapshot.current !== opened) return
      const known = new Map([...manifest.items, ...project.items].map(item => [item.id, item]))
      const list = image_ids.flatMap(id => {
        const item = known.get(id)
        return item ? [item] : []
      })
      if (list.length) {
        const previousIndex = opened.list.findIndex(item => item.id === image.id)
        const index = action === 'remove' ? Math.min(previousIndex, list.length - 1) : list.findIndex(item => item.id === image.id)
        setDetail({ list, index: Math.max(0, index), project: { ...project, items: list } })
      } else {
        closeDetail()
      }
      say(action === 'remove' ? '已从项目移除' : '已保存项目顺序')
    } catch {
      say('项目图片更新失败，请重试')
    } finally {
      setProjectEditPending(false)
    }
  }
  /** 项目改名：PATCH 成功后 reload，列表和已打开的详情页标题一起换掉 */
  const renameProject = async (project: Project, name: string) => {
    if (demo) { say('演示模式不会改到真实数据'); return false }
    try {
      await renameProjectRequest(Number(project.id), name)
      if (actionMode.current !== api) return false
      projectData.reload()
      if (detailSnapshot.current?.project?.id === project.id) {
        setDetail(opened => opened?.project?.id === project.id ? { ...opened, project: { ...opened.project, name } } : opened)
      }
      say('项目已改名')
      return true
    } catch {
      say('改名没成功：网络或服务出错，请再试一次')
      return false
    }
  }
  /** 项目删除（软删除）：成功后 reload，列表里立刻消失；已打开的项目详情页要关掉 */
  const removeProject = async (project: Project) => {
    if (demo) { say('演示模式不会改到真实数据'); return false }
    try {
      await deleteProjectRequest(Number(project.id))
      if (actionMode.current !== api) return false
      projectData.reload()
      if (detailSnapshot.current?.project?.id === project.id) closeDetail()
      say('已删除项目，可在后台恢复')
      return true
    } catch {
      say('项目删除没成功：网络或服务出错，请再试一次')
      return false
    }
  }
  /** 图片删除（软删除）：成功后 reload 素材库，墙上和项目里都会消失 */
  const removeImage = async (item: LabImage) => {
    if (demo) { say('演示模式不会改到真实数据'); return false }
    try {
      await deleteImageRequest(item.id)
      if (actionMode.current !== api) return false
      library.reload()
      say('已删除这张图，可在后台恢复')
      return true
    } catch {
      say('删除没成功：网络或服务出错，请再试一次')
      return false
    }
  }
  const recommendationResult = (text: string, response: RecommendationResponse, projectId?: number) => {
    const items = hitImages(response.images, manifest.items)
    const byId = new Map(items.map(item => [item.id, item]))
    const groups = (['layout', 'palette', 'style'] as const).map(purpose =>
      (response.columns.find(column => column.purpose === purpose)?.items ?? []).flatMap(hit => {
        const item = byId.get(hit.image_id)
        return item ? [item] : []
      }))
    const referenceNotes = (['layout', 'palette', 'style'] as const).map(purpose =>
      Object.fromEntries((response.columns.find(column => column.purpose === purpose)?.items ?? []).map(
        hit => [hit.image_id, hit.reference_point]
      )))
    const referenceSource = response.columns.some(column => column.recall_source === 'local_library') ? '本地参考' : undefined
    return { text, items, groups, referenceNotes, referenceSource, projectId }
  }
  const search = async (text: string, reference: boolean, docked: boolean) => {
    // 原来的行为：找图搜完按相似度排（顶栏排序菜单跟着显示「像不像」）
    if (!reference && text.trim()) setSort('similar')
    const version = ++requestVersion.current
    setSearchKind(reference ? 'ref' : 'find')
    setPendingSearch(version)
    try {
      if (reference) {
        const example = REFINE && demo ? demoReferenceResult(text, manifest.cards) : null
        if (example) {
          setResults({ ...example, docked })
          track('search', { q: text.slice(0, 80), mode: 'ref', strong: 0, weak: example.items.length, none: example.items.length === 0 })
          return
        }
        const response = await api.recommend(text)
        if (version !== requestVersion.current || actionMode.current !== api) return
        const referenceResult = response ? recommendationResult(text, response) : { text, items: [], groups: [] }
        setResults({ ...referenceResult, docked })
        track('search', { q: text.slice(0, 80), mode: 'ref', strong: 0, weak: referenceResult.items.length, none: referenceResult.items.length === 0 })
      } else {
        const response = await api.search(text)
        if (version !== requestVersion.current || actionMode.current !== api) return
        // 后端给每项 relevance；缺失（老后端 / 本地检索）一律当 strong，界面与改前一致。
        const relevance: Record<number, Relevance> = {}
        for (const hit of response.items) relevance[hit.image_id] = hit.relevance ?? 'strong'
        setResults({ text, items: hitImages(response.items, manifest.items), relevance, docked })
        const strong = response.items.filter(hit => (hit.relevance ?? 'strong') === 'strong').length
        track('search', {
          q: text.slice(0, 80), mode: 'find',
          strong, weak: response.items.length - strong, none: strong === 0,
        })
        if ('degraded' in response && response.degraded) say('已使用本地搜索')
      }
    } catch (error) {
      if (version === requestVersion.current && actionMode.current === api) {
        setResults({ text, items: [], groups: reference ? [] : undefined, docked })
        say(errorText(error, reference ? '要参考没成功：网络或服务出错，稍后再试' : '搜索没成功：网络或服务出错，检查网络后再试'))
      }
    } finally {
      setPendingSearch(current => current === version ? null : current)
    }
  }
  const findSimilar = async (item: LabImage) => {
    track('similar')
    try {
      const response = await api.findSimilar(item.id)
      if (actionMode.current !== api) return
      setSort('similar')
      setResults({ text: 'match_basis' in response && response.match_basis === 'palette' ? '配色相近的图片' : '相似图片', similar: true, items: hitImages(response.items, manifest.items).filter(row => row.id !== item.id) })
      navigate('library')
      setWallRequest(n => n + 1)
    } catch (error) { say(errorText(error, '找相似没成功：网络或服务出错，稍后再试')) }
  }
  const createProject = async (title: string) => {
    try {
      const result = await api.createProject({ name: title, brief: title })
      if (actionMode.current !== api) return
      projectData.reload()
      if (result.recommendations) {
        setResults(recommendationResult(title, result.recommendations, result.project.id))
        navigate('library')
        setWallRequest(n => n + 1)
      } else if (result.recommendationFailed) say('项目已新建；参考推荐没响应，稍后可以在素材库用「要参考」再找')
    } catch { say('新建项目没成功：网络或服务出错，请再试一次') }
  }
  const addToProject = async (projectId: number, imageId: number) => {
    track('add_project')
    try {
      await api.addToProject(projectId, imageId, ['subject'])
      if (actionMode.current !== api) return
      projectData.reload()
      // 手机：提示里带上项目名，点一下直接进这个项目（三列方格）；电脑照旧
      if (phone) {
        const name = projects.find(project => Number(project.id) === projectId)?.name ?? '项目'
        say(`已加入「${name}」`, { label: '去看看 →', act: () => openPhoneProject(String(projectId)) })
      } else say('已加入项目')
    } catch { say('没加入项目：网络或服务出错，请再试一次') }
  }
  const saveProject = async (name: string, items: LabImage[]) => {
    try {
      const projectId = results?.projectId ?? (await api.createProject({ name, brief: '' })).project.id
      const added = await Promise.allSettled(items.map(item => api.addToProject(projectId, item.id, ['subject'])))
      if (actionMode.current !== api) return
      projectData.reload()
      if (added.some(item => item.status === 'rejected')) say('有几张没加入项目：网络或服务出错，打开项目看看缺哪几张再补')
    } catch { say('存为新项目没成功：网络或服务出错，请再试一次') }
  }
  const findByColor = async (c: Swatch) => {
    track('color_find', { hex: c.hex })
    try {
      const items = await api.findByColor(c.hex)
      if (actionMode.current !== api) return
      setResults({ text: '按颜色找', items: items.map(fromLibrary) })
      navigate('library')
      setHue(c.h)
      setWallRequest(n => n + 1)
    } catch (error) { say(errorText(error, '按颜色找没成功：网络或服务出错，稍后再试')) }
  }
  const clearResults = () => {
    requestVersion.current += 1
    setPendingSearch(null)
    setResults(null)
  }
  /** 「查看刚导入的」：素材库只显示这一轮新收进来的图 */
  const viewImported = () => {
    const ids = new Set(importer.progress.addedIds ?? [])
    const items = manifest.items.filter(item => ids.has(item.id))
    requestVersion.current += 1
    setPendingSearch(null)
    setHue(null)
    setPlatforms(new Set())
    setSort('time')
    // 列表还没刷新到新图时，退回「按时间排的全部」——新图本来就在最前面
    setResults(items.length ? { text: `刚导入的 ${items.length} 张`, items } : null)
    if (!items.length) say('列表还在刷新，最新导入的图在最前面')
    importer.reset()
    navigate('library')
    setWallRequest(n => n + 1)
  }
  /** 点左上角 TADA / 「回首页」：回素材库首页（清掉搜索、筛选和颜色，回到全部收藏），并回到页面顶部 */
  const goHome = () => {
    navigate('library')
    clearResults()
    setHue(null)
    setPlatforms(new Set())
    if (detail || reduce || reducedMotion) scrollToInstant(0)
    else scrollToY(0)
  }
  const decide = async (retain: boolean) => {
    const ids = [...keep]
    if (!ids.length) return
    try {
      // 演示模式不改后端数据（和手机版一致），只记在这一页
      if (!demoReviewed) await api.decideReference(ids, retain)
      if (actionMode.current !== api) return
      setKeep(new Set())
      setDecidedIds(previous => {
        const next = new Set(previous)
        ids.forEach(id => next.add(id))
        return next
      })
      setPendingTally(previous => retain
        ? { ...previous, keep: previous.keep + ids.length }
        : { ...previous, drop: previous.drop + ids.length })
      pendingData.reload()
      library.reload()
      // 面板不关：标题下的「已保留 / 不是参考」要看得见，剩下的可以接着处理
    } catch { say('没保存上：网络或服务出错，选中的图还在，请再点一次') }
  }
  /** 顶栏右侧的工具：每一页自己那一行带下划线的衬线小字（样式见 app-header.css） */
  const filterBase = results?.items ?? manifest.items
  const filteredCount = filterBase.filter(item => platforms.size === 0 || platforms.has(sourceLabel(item))).length
  const clearFilters = () => {
    track('filter_change', { kind: 'filter', value: '全部' })
    setPlatforms(new Set())
    if (hue !== null) clearResults()
    setHue(null)
  }
  const filterRows = [
    { key: '全部', label: '全部', count: manifest.items.length, on: platforms.size === 0 && hue === null, act: clearFilters },
    ...PLATFORMS.map(platform => ({
      key: platform,
      label: platform,
      count: manifest.items.filter(item => sourceLabel(item) === platform).length,
      on: platforms.has(platform),
      act: () => {
        const next = new Set(platforms)
        if (next.has(platform)) next.delete(platform); else next.add(platform)
        setPlatforms(next)
        track('filter_change', { kind: 'platform', value: platform })
      },
    })),
    { key: 'color', label: '按颜色', on: hue !== null, act: () => { setFilterOpen(false); setColorOpen(true) } },
    // 「待确认」不再藏在筛选里：搜索框下方的提示条和顶栏切换菜单各有一个入口（openPending）
  ]
  const openPending = () => {
    if (narrowPhone) setFirstShown(true)
    setFilterOpen(false)
    setColorOpen(false)
    setKeep(new Set())
    setPendingOpen(true)
  }
  const closePending = () => {
    setPendingOpen(false)
    if (!pendingChanged.current) return
    pendingChanged.current = false
    if (!demo) {
      pendingData.reload()
      library.reload()
    }
  }
  // 手机：排序写成人话
  const PHONE_SORT_LABEL: Record<SortKey, string> = { time: '时间正序', color: '颜色相似', similar: '画面相近' }
  const sortRows = SORTS
    .filter(option => !(phone && option.k === 'similar' && !results))
    .map(option => ({
      key: `sort-${option.k}`, label: phone ? PHONE_SORT_LABEL[option.k] : option.label, on: sort === option.k,
      act: () => { setSort(option.k); setFilterOpen(false); track('filter_change', { kind: 'sort', value: option.k }) },
    }))
  const importTool = REFINE || !demo
    ? <ImportDialog demo={demo} busy={importer.status === 'importing'} onFiles={files => void importer.start(files)} />
    : null
  const projectTools = projectForm ? (
    <form className="m-appbar-form" onSubmit={event => {
      event.preventDefault()
      const title = projectName.trim()
      if (!title) return
      createProject(title)
      setProjectName('')
      setProjectForm(false)
    }}>
      <input className="m-appbar-input" autoFocus value={projectName} placeholder="写一句要做什么" aria-label="新项目名字"
        onChange={event => setProjectName(event.target.value)}
        onKeyDown={event => { if (event.key === 'Escape') { setProjectForm(false); setProjectName('') } }} />
      <button type="submit" className="m-hb" disabled={!projectName.trim()}>新建</button>
    </form>
  ) : (
    <>
      <button type="button" className="m-hb" onClick={() => setProjectForm(true)}>新建项目 +</button>
      {/* 手机：项目只用网格（点进项目是三列方格），不放「翻看 | 网格」 */}
      {!phone && <>
        <span className="m-hb-dot" aria-hidden="true">·</span>
        <button type="button" className={`m-hb m-state-switch${projectLayout === 'feed' ? ' is-on' : ''}`} aria-pressed={projectLayout === 'feed'}
          onClick={() => projectsRef.current?.setLayout('feed')}><RollingLabel text="翻看" /></button>
        <span className="m-hb-dot" aria-hidden="true">|</span>
        <button type="button" className={`m-hb m-state-switch${projectLayout === 'grid' ? ' is-on' : ''}`} aria-pressed={projectLayout === 'grid'}
          onClick={() => projectsRef.current?.setLayout('grid')}><RollingLabel text="网格" /></button>
      </>}
    </>
  )
  const calendarTools = (
    <>
      {([['day', '每天'], ['week', '每周'], ['total', '累计']] as const).map(([key, label], index) => (
        <Fragment key={key}>
          {index > 0 && <span className="m-hb-dot" aria-hidden="true">·</span>}
          <button type="button" className={`m-hb m-state-switch${calendarGrain === key ? ' is-on' : ''}`} aria-pressed={calendarGrain === key}
            onClick={() => setCalendarGrain(key)}><RollingLabel text={label} /></button>
        </Fragment>
      ))}
    </>
  )
  // 手机：顶栏右边只放「导入 +」（素材库再加「筛选」）；日历页的「每天 · 每周 · 累计」挪到页面顶上一行。
  // 项目页的「新建项目 +」交给 Projects，和「N 个项目」合成一行（headTools），这里不再单独占一行
  const phonePageTools = phone && !detail && !boardOn && view === 'activity' ? calendarTools : null
  const headerTools = detail || boardOn ? undefined : view === 'library' ? (
    <>
      {importTool && (
        <>
          {importTool}
          <span className="m-hb-dot" aria-hidden="true">·</span>
        </>
      )}
      <HeaderColor open={colorOpen} onOpenChange={setColorOpen} hue={hue} onHue={value => { clearResults(); setHue(value); track('filter_change', { kind: 'hue', value: String(value) }) }} />
      <GlassMenu variant="text" label="筛选" title="筛选" width={180} open={filterOpen} onOpenChange={open => { setFilterOpen(open); if (open) { setColorOpen(false); track('filter_open') } }}
        onClose={() => setFilterOpen(false)}
        head={<><RollingLabel text="筛选" />{(platforms.size > 0 || hue !== null) && <span className="m-hb-filter-active" aria-hidden="true" />}</>}
        rows={[...filterRows, ...sortRows]} splitAt={filterRows.length}
        footer={<>
          {platforms.size > 0 || hue !== null
            ? <button type="button" className="m-gm-clear" onClick={clearFilters}>清除筛选</button>
            : <span />}
          <span className="m-gm-shown">显示 {filteredCount} 张</span>
        </>} />

    </>
  ) : phone && view === 'projects' && phoneProjectId === null && projects.length > 1 ? (
    // 手机项目页：顶栏右上角（素材库放「筛选」的位置）一颗「整理」；整理中变成陶土橙的「完成」
    <>
      {importTool && !arranging && (
        <>
          {importTool}
          <span className="m-hb-dot" aria-hidden="true">·</span>
        </>
      )}
      <button type="button" className={`m-hb m-hb-arrange${arranging ? ' is-done' : ''}`} aria-pressed={arranging}
        onClick={() => setArranging(on => !on)}>{arranging ? '完成' : '整理'}</button>
    </>
  ) : phone ? importTool ?? undefined
    : view === 'projects' ? projectTools
    : view === 'activity' ? calendarTools
    : undefined

  /** 正中胶囊里写当前页的名字：详情页写「素材库」或所在项目名 */
  // 手机参考画板里点开的详情：胶囊仍写「参考画板」，和网页标题一致
  const headerTitle = detail ? (boardOn ? '参考画板' : detail.project ? detail.project.name : '素材库')
    : boardOn ? '参考画板'
    : view === 'projects' ? '项目' : view === 'activity' ? '日历' : view === 'settings' ? '设置' : '素材库'
  const headerPage: AppPage = detail ? (detail.project ? 'projects' : 'library') : view
  // 手机底部导航：详情页不显示（详情页底部有自己的操作条，两条叠在一起太挤）
  const tabBarOn = phone && !detail

  return (
    <div className={`m-app ${reduce ? 'm-reduce' : ''}${detail ? ' m-with-detail' : ''}${REFINE ? ' m-refine' : ''}${tabBarOn ? ' m-has-tabbar' : ''}`} style={{ position: 'relative' }}
      data-lenis-prevent-wheel={view === 'projects' && projectLayout === 'feed' && !detail ? '' : undefined}
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={e => { if (e.currentTarget === e.target) setDragging(false) }}
      onDrop={e => {
        e.preventDefault()
        setDragging(false)
        void collectDroppedFiles(e.dataTransfer).then(files => importer.start(files.map(item => item.file)), () => say('读不到拖进来的文件：试试用「导入 +」从文件夹选'))
      }}>
      <div className="m-grain" />
      <PipButton ref={pipRef} showTrigger={false} projectId={pipTarget.projectId} projectName={pipTarget.name}
        images={pipImages} libraryImages={pipLibrary}
        projects={projects.map(project => ({ id: Number(project.id), name: project.name }))}
        loadProject={async id => {
          const project = projects.find(entry => Number(entry.id) === id)
          if (!project) throw new Error('项目不存在')
          return { id, name: project.name, images: project.items.map(toPipImage) }
        }} />

      <AppHeader title={headerTitle} current={headerPage} onHome={goHome}
        onGo={page => navigate(page)}
        onPip={() => {
          const project = detail?.project ?? (view === 'projects' && !detail ? projectsRef.current?.getCurrentProject() : undefined)
          if (project) openPip({ projectId: Number(project.id), name: project.name, items: project.items })
          else openPip({ projectId: null, name: null, items: manifest.items })
        }}

        pendingCount={pendingItems.length} onPending={() => { dismissDetail(); openPending() }}
        tools={headerTools} />

      <motion.div key={boardOn ? 'board' : view} className="m-view" hidden={!!detail}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: PAGE.duration, ease: PAGE.ease }}>
          {phonePageTools && <div className="m-phone-pagetools">{phonePageTools}</div>}
          {boardOn && phoneBoard ? (
            <PhoneBoard projectId={phoneBoard.projectId} name={phoneBoard.name} items={phoneBoard.items}
              library={manifest.items} onOpen={openDetail} />
          ) : view === 'library' ? (
            <>
              {/* 读素材库中 / 读失败：都在瀑布流的位置说（Library 的 loadState），不再先摆出空库和「去导入」 */}
              <Library manifest={manifest}
                loadState={demo ? 'ready' : libraryStatus} onRetry={library.reload}
                searchKind={searchKind} onFirstImage={markFirstShown} initialQuery={initialQuery} onOpen={openDetail} onPending={openPending}
                platforms={platforms} onPlatforms={setPlatforms} hue={hue} onHue={setHue} wallRequest={wallRequest}
                onSaveProject={saveProject} results={results} searching={pendingSearch !== null} onSearch={search} onClear={clearResults}
                pendingCount={pendingItems.length} reduce={reduce || !!reducedMotion}
                searchActive={!detail && !pendingOpen} sort={sort} api={api} analyzing={analyzing}
                onImportFiles={files => void importer.start(files)} onDeleteImage={removeImage} />
            </>
          ) : view === 'settings' ? (
            <SettingsPage items={manifest.cards} onToast={say} reduce={reduce} onReduce={setReduce} api={api}
              demo={demo} onSynced={library.reload} onImport={files => { void importer.start(files) }} onDemo={value => { clearResults(); setHue(null); setPlatforms(new Set()); setDemo(value) }} />
          ) : view === 'activity' ? (
            <ActivityPage api={api} demo={demo} manifest={manifest} onOpen={openDetail}
              grain={calendarGrain} />
          ) : (
            <Projects ref={projectsRef} active={!detail} initialLayout={projectLayout} onOpen={(project, index) => openDetail(project.items[index], project.items, index, project)}
              saved={projects} onLayoutChange={setProjectLayout}
              openId={phoneProjectId} onOpenId={setPhoneProjectId}
              arranging={phone && arranging} order={projectOrder} onReorder={saveProjectOrder}
              headTools={phone && !arranging ? projectTools : undefined}
              onRenameProject={renameProject} onDeleteProject={removeProject} />
          )}
      </motion.div>

      {/* 手机底部是导航栏，不再叠一层渐隐 */}
      {view === 'library' && !detail && !boardOn && !phone && <GradualBlur position="bottom" height={96} strength={0.5} layers={10} zIndex={40} />}


      {detail && (
        <Detail key={detail.project ? detail.list.map(item => item.id).join(',') + ':' + detail.index : `${detail.list[detail.index]?.id}-${detail.index}`} postOf={postOf}
          list={detail.list} index={detail.index} single={!detail.project} project={detail.project}
          browse={detail.browse ? {
            prev: detail.browse.index > 0,
            next: detail.browse.index < detail.browse.list.length - 1,
            go: stepDetail,
          } : undefined}
          enterFrom={detail.enter}
          onClose={closeDetail} onToast={say} onColor={findByColor} api={api} analysis={analysis}
          projects={projects} onAdd={addToProject} onSimilar={findSimilar}
          onDeleteImage={removeImage} onRenameProject={renameProject} onDeleteProject={removeProject}
          onProjectEdit={detail.project && !demo && import.meta.env.VITE_PUBLIC_DEMO === '1' ? editProjectImage : undefined}
          projectEditPending={projectEditPending}
          onPip={image => {
            const project = detail.project
            if (project) openPip({ projectId: Number(project.id), name: project.name, items: project.items })
            else openPipForImage(image)
          }} />
      )}
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} onToast={say} reduce={reduce} onReduce={setReduce} />

      {narrowPhone && pendingOpen && (
        <PhonePending
          items={allPendingItems} status={pendingData.value.status} api={api} demoReviewed={demoReviewed}
          onClose={closePending} onReturn={() => { closePending(); goHome() }}
          onRetry={pendingData.reload} onChanged={() => { if (actionMode.current === api) pendingChanged.current = true }} />
      )}
      {!narrowPhone && <div className={`m-modal ${pendingOpen ? 'is-on' : ''}`}>
        <div className="m-modal-bg" onClick={closePending} />
        <div className="m-modal-panel">
          <div className="m-modal-head">
            <h2>待确认</h2>
            <span className="m-spacer" />
            <button type="button" className="m-close" style={{ position: 'static' }} aria-label="关闭" onClick={closePending}><Icon icon={X} size={16} /></button>
          </div>
          <p className="m-pending-tally">已保留 {pendingTally.keep} · 不是参考 {pendingTally.drop}</p>
          {/* 引导版：「待确认」三个字第一次看不知道在确认什么，补一句 */}
          {REFINE && <p className="m-empty-line" style={{ padding: '0 0 14px' }}>
            {pendingItems.length ? '这些图可能不是设计参考。点图选中，再选「保留」或「不是参考」；你的选择会让以后的分类更准。' : pendingTally.keep + pendingTally.drop > 0 ? '都分好了，可以关闭了。' : '现在没有需要确认的图。'}
          </p>}
          <div className="m-pending-grid">
            {pendingItems.map(it => {
              const on = keep.has(it.id)
              return (
                <button type="button" key={it.id}
                  className={`m-pending-item ${on ? 'is-keep' : ''}`}
                  onClick={() => {
                    const next = new Set(keep)
                    if (next.has(it.id)) next.delete(it.id); else next.add(it.id)
                    setKeep(next)
                  }}>
                  <img src={thumbnailSrc(it.src)} alt="" loading="lazy" />
                  {on && <b>✓</b>}
                </button>
              )
            })}
          </div>
          <div className="m-modal-foot">
            <span className="m-spacer" />
            <button type="button" className="ds-btn ds-btn--secondary" disabled={!keep.size} onClick={() => void decide(false)}>不是参考</button>
            <button type="button" className="ds-btn ds-btn--primary" disabled={!keep.size} onClick={() => void decide(true)}>
              保留
            </button>
          </div>
        </div>
      </div>}

      <div className={`m-drop ${dragging ? 'is-on' : ''}`}><p>松手导入</p></div>
      <ImportResult status={importer.status} progress={importer.progress} cancelled={importer.cancelled} error={importer.error}
        onView={viewImported} onClose={importer.reset} onCancel={importer.cancel} />
      {tabBarOn && <PhoneTabBar current={boardOn ? null : view}
        onGo={page => { if (page === 'library') goHome(); else navigate(page) }} />}
      <div className={`m-toast ${toast ? 'is-on' : ''}${toast?.action ? ' has-action' : ''}`} role="status" aria-live="polite">
        {toast?.action ? (
          <button type="button" className="m-toast-action" onClick={() => {
            const act = toast.action?.act
            setToast(null)
            act?.()
          }}>{toast.text}<span className="m-toast-go"> · {toast.action.label}</span></button>
        ) : toast?.text}
      </div>
    </div>
  )
}

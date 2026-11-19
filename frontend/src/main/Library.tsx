import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { MasonryPhotoAlbum, type Photo } from 'react-photo-album'
import 'react-photo-album/masonry.css'
import type { LabImage, Manifest } from '../lab/data'
import { thumbnailSrc } from '../imageThumbnail'
import type { LibraryResult } from './MainApp'
import { SearchLine, type Mode } from './SearchLine'
import { scrollToInstant, scrollToY } from './smooth'
import { sourceLabel } from './meta'
import { SaveToProject, type SaveJob } from './SaveToProject'
import { enterDelayMs } from './motion'
import { cardTitle } from './caption'
import type { SortKey } from './sorts'
import type { WiringApi } from '../api/wiring'
import { useMobileLayout } from './useMobileLayout'
import './library-wall.css'

type WallPhoto = Photo & { item: LabImage; index: number }
const REF_GROUPS = ['排版', '配色', '风格']
const hueGap = (a: number, b: number) => { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d }
/** 瀑布流一列大约多宽：column 宽度按内容宽 / 295 取整算列数（1440 下 5 列） */
const COL_UNIT = 288
const colCount = (width: number) => Math.min(5, Math.max(2, Math.round(width / COL_UNIT)))
/** 首屏优先加载的图片张数：这几张 eager + 高优先级，其余懒加载 */
const FIRST_IMAGES = 4
/** 加载中的灰色占位块：高矮不一，看起来像瀑布流 */
const PLACEHOLDER_RATIOS = [0.78, 1.25, 1, 0.8, 1.3, 0.72, 1.1, 0.9]

/** 加载中：一句话 + 几块灰色占位，不放任何按钮（免得数据回来时误点到换上来的图） */
function WallLoading({ text }: { text: string }) {
  return (
    <div className="m-wall-loading" role="status" aria-live="polite">
      <p className="m-wall-loading-text">{text}</p>
      <div className="m-wall-loading-grid" aria-hidden="true">
        {PLACEHOLDER_RATIOS.map((ratio, i) => <span key={i} style={{ aspectRatio: ratio }} />)}
      </div>
    </div>
  )
}

/**
 * 素材库：第一行就是搜索（大标题本身是输入框），细线下面直接是瀑布流；找图替换墙，要参考换成三列。
 * 瀑布流用 现有 masonry 相册（图 + 图注一块儿放进一列，不会互相压）；筛选/排序挪到顶栏，这里只吃 sort 入参。
 */
export function Library({ manifest, initialQuery = '', onOpen, onPending, platforms, onPlatforms, hue, onHue: setHue, wallRequest, onSaveProject, results, searching, onSearch, onClear, pendingCount = 0, searchActive, reduce = false, sort = 'time', analyzing, onImportFiles, loadState = 'ready', onRetry, searchKind = 'find', onFirstImage }: {
  /** 素材库读到哪一步：loading 时不摆空库和「去导入」（公网慢时会被误点），error 时给一个重试 */
  loadState?: 'loading' | 'ready' | 'error'
  onRetry?: () => void
  /** 正在进行的搜索是找图还是要参考：加载中那句话不一样 */
  searchKind?: 'find' | 'ref'
  /** 首屏第一张图显示出来（或加载失败）时通知一次：不急的接口等它之后再请求 */
  onFirstImage?: () => void
  /** 从引导页示例搜索框带过来的初始关键词 */
  initialQuery?: string
  /** 此刻正在分析的图片 id：卡片上标「分析中」 */
  analyzing?: Set<number>
  /** 搜不到 / 库是空的时候：直接打开相册导入 */
  onImportFiles?: (files: File[]) => void
  manifest: Manifest
  /** list：当前筛选结果，详情页按它显示“第几张 / 共几张” */
  onOpen: (item: LabImage, list: LabImage[], index: number) => void
  /** 打开「待确认」：搜索框下方的提示条用 */
  onPending?: () => void
  platforms: Set<string>
  onPlatforms?: (next: Set<string>) => void
  hue: number | null
  onHue: (h: number | null) => void
  /** 每加一次就滚到墙顶（详情里点主色回来时用） */
  wallRequest: number
  /** “存为项目”动画飞到 PROJECTS 时调用：新项目这时才落进项目列表 */
  onSaveProject: (name: string, items: LabImage[]) => void
  results: LibraryResult | null
  searching: boolean
  onSearch: (text: string, reference: boolean, docked: boolean) => void
  onClear: () => void
  /** 待确认的图有几张：大于 0 时搜索框下方出现「有 N 张图待确认 →」 */
  pendingCount?: number
  searchActive: boolean
  /** 「减少动效」开关（主页目前没有时序动画，保留这个入参不动 MainApp 的传参） */
  reduce?: boolean
  /** 顶栏排序菜单传给瀑布流的排序档，默认按时间 */
  sort?: SortKey
  /** 已不再使用：卡片标题改用列表接口带的 title，不再逐张取详情。保留入参不动 MainApp 的传参 */
  api?: WiringApi
}) {
  const [mode, setMode] = useState<Mode>('find')
  const [text, setText] = useState(initialQuery)
  const initialQuerySent = useRef(false)
  useEffect(() => {
    if (initialQuerySent.current || !initialQuery) return
    initialQuerySent.current = true
    onSearch(initialQuery, false, false)
  }, [initialQuery, onSearch])
  const field = useRef<HTMLInputElement | null>(null)

  const query = results?.text ?? ''
  const submitted = results !== null
  const [picked, setPicked] = useState<LabImage[]>([])
  const [saving, setSaving] = useState<SaveJob | null>(null)
  const wallTop = useRef<HTMLDivElement | null>(null)
  const wallEl = useRef<HTMLDivElement | null>(null)

  /** 只用真实图库数据，一张只出现一次。 */
  const cards = manifest.cards

  const sorted = useMemo(() => {
    const list = (results?.items ?? cards).slice()
    if (results && (results.groups || hue !== null)) return list
    if (hue !== null) {
      const near = (it: LabImage) => it.hues.length ? Math.min(...it.hues.map(h => hueGap(h, hue))) : 999
      list.sort((a, b) => near(a) - near(b))
    } else if (sort === 'color') list.sort((a, b) => (a.hues[0] ?? 0) - (b.hues[0] ?? 0))
    else if (sort === 'time') list.sort((a, b) => {
      if (!a.createdAt && !b.createdAt) return b.id - a.id
      return (Date.parse(b.createdAt ?? '') || 0) - (Date.parse(a.createdAt ?? '') || 0)
    })
    else if (sort === 'similar') list.sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
    return list
  }, [cards, sort, hue, results])

  const filtered = useMemo(() => sorted.filter(it => platforms.size === 0 || platforms.has(sourceLabel(it))), [sorted, platforms])

  const photos = useMemo<WallPhoto[]>(() => filtered.map((item, index) => ({
    key: String(item.id), src: thumbnailSrc(item.src), width: item.w || 1, height: item.h || 1, item, index,
  })), [filtered])

  // 换筛选 / 排序时保持当前滚动位置：结果变少、页面变短时，给墙垫一个最小高度，再回到原来的位置
  const lastY = useRef(0)
  useEffect(() => {
    const onScroll = () => { lastY.current = window.scrollY }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  const firstLayout = useRef(true)
  const lastResults = useRef(results)
  useLayoutEffect(() => {
    if (firstLayout.current) { firstLayout.current = false; lastResults.current = results; return }
    const wrap = wallTop.current
    if (!wrap) return
    // 新的搜索结果：原地换墙，不垫高度也不滚（页面不跳）；页面变短时浏览器自己把滚动位置收回可视范围
    if (lastResults.current !== results) {
      lastResults.current = results
      wrap.style.minHeight = ''
      return
    }
    const y = lastY.current
    // 瀑布流是下一帧才重新排的，所以先无条件垫好“能停在原位置”的最小高度，再回到原位置
    const wrapTop = wrap.getBoundingClientRect().top + window.scrollY
    wrap.style.minHeight = `${Math.max(0, Math.ceil(y + window.innerHeight - wrapTop))}px`
    scrollToInstant(y)
  }, [filtered, sort])

  const mobile = useMobileLayout()
  const gap = mobile ? 10 : 24
  // 首屏图片第一次显示出来（或失败）只通知一次
  const firstImageSent = useRef(false)
  const firstImageDone = () => {
    if (firstImageSent.current) return
    firstImageSent.current = true
    onFirstImage?.()
  }

  // 卡片标题直接用列表接口带的 title（没有就用列表里已有的描述 / 标签），不再逐张请求 /api/images/{id}

  // 从详情点主色回来：等切页回顶之后，再滚回搜索行所在的页面顶部
  useEffect(() => {
    if (!wallRequest) return
    const t = window.setTimeout(() => {
      if (!wallTop.current) return
      scrollToY(0)
    }, 80)
    return () => window.clearTimeout(t)
  }, [wallRequest])

  const submit = (next: string, docked: boolean) => {
    setPicked([])
    onSearch(next, mode === 'ref', docked)
  }
  const clear = () => { setText(''); setHue(null); onClear(); setPicked([]) }
  const togglePick = (it: LabImage) =>
    setPicked(prev => prev.includes(it) ? prev.filter(x => x !== it) : [...prev, it])
  // 存为项目：记下每张图现在在屏幕上的位置，交给飞行动画
  const saveAsProject = () => {
    if (!picked.length || saving) return
    const from = picked.map(it => {
      const r = document.querySelector(`[data-pick-id="${it.id}"] img`)?.getBoundingClientRect()
      return r ? { x: r.left, y: r.top, w: r.width, h: r.height } : { x: window.innerWidth / 2, y: window.innerHeight, w: 56, h: 75 }
    })
    setSaving({ name: query || '参考', items: picked, from })
  }
  const navTarget = () => {
    const r = document.querySelector('[data-nav="projects"]')?.getBoundingClientRect()
    return r ? { x: r.left, y: r.top, w: r.width, h: r.height } : null
  }

  const cardNode = (it: LabImage, i = 0) => (
    <button type="button" key={it.id} className="m-card" onClick={() => onOpen(it, filtered, filtered.indexOf(it))} aria-label={it.source}
      data-reveal style={{ '--rv-d': enterDelayMs(i) } as React.CSSProperties}>
      <img src={thumbnailSrc(it.src)} alt="" loading="lazy" width={it.w} height={it.h} />
    </button>
  )

  const refGroups = [0, 1, 2].map(index =>
    (results?.groups?.[index] ?? []).filter(item => !platforms.size || platforms.has(sourceLabel(item))))

  return (
    <div className="m-sl-wrap">
      <SearchLine mode={mode} onMode={setMode} text={text} onText={setText} onSearch={submit}
        onEscape={clear}
        field={field} active={searchActive} reduce={reduce} searching={searching} />

      {/* 「待确认」入口：有待确认的图才出现，一行不打扰的小字，点了直接打开待确认 */}
      {pendingCount > 0 && onPending && !submitted && (
        <div className="m-pending-cue">
          <button type="button" className="m-pending-cue-btn" onClick={onPending}>有 {pendingCount} 张图待确认 →</button>
        </div>
      )}

      <section className="m-wall-wrap" ref={wallTop}>
        {/* 筛选只管下面的列表：列表上方写出当前生效的条件，点 × 单独去掉一条 */}
        {(platforms.size > 0 || hue !== null) && (
          <div className="m-filter-line" role="group" aria-label="当前筛选">
            <span className="m-filter-line-lead">当前筛选：</span>
            {[...platforms].map(platform => (
              <button type="button" key={platform} className="m-filter-chip" aria-label={`去掉筛选：${platform}`}
                onClick={() => { const next = new Set(platforms); next.delete(platform); onPlatforms?.(next) }}>
                {platform}<span className="m-filter-chip-x" aria-hidden="true">×</span>
              </button>
            ))}
            {hue !== null && (
              <button type="button" className="m-filter-chip" aria-label="去掉筛选：按颜色" onClick={() => setHue(null)}>
                <span className="m-filter-chip-dot" style={{ background: `oklch(0.72 0.15 ${hue})` }} aria-hidden="true" />按颜色
                <span className="m-filter-chip-x" aria-hidden="true">×</span>
              </button>
            )}
          </div>
        )}
        {/* 搜索进行中：上一次的结果标题行一起收起来，免得看起来像搜错了 */}
        {submitted && !searching && (
          <div className="m-result-line" data-reveal>
            <span>{query || (mode === 'find' ? '全部' : '参考')}</span>
            {results?.reasons && <span className="m-pick-hint">示例解读</span>}
            {results?.referenceSource && <span className="m-pick-hint">{results.referenceSource}</span>}
            {/* 要参考：先说清楚两种点法，勾选圈才看得懂 */}
            {results?.groups && filtered.length > 0 && (
              <span className="m-pick-hint m-pick-howto">点图看详情，点右上角圆圈挑选</span>
            )}
            <button type="button" className="ds-btn ds-btn--ghost ds-btn--sm" onClick={clear}>返回全部收藏</button>
          </div>
        )}

        {searching ? (
          // 搜索进行中：旧结果先换成加载状态，结果回来再换上
          <WallLoading text={searchKind === 'ref' ? '正在从素材库找排版、配色和风格参考…' : '正在找你描述的图…'} />
        ) : !submitted && cards.length === 0 && loadState === 'loading' ? (
          <WallLoading text="正在打开素材库…" />
        ) : !submitted && cards.length === 0 && loadState === 'error' ? (
          <div className="ds-empty m-wall-failed" role="alert">
            <button type="button" className="ds-btn ds-btn--primary" onClick={onRetry}>没连上，点这里重试</button>
          </div>
        ) : filtered.length === 0 ? (
          submitted ? (
            // 搜不到时不要再给一排“下一步”，只留一个明确的出口。
            <div className="ds-empty" role="status">
              <p className="ds-empty-title">没找到{query ? <>「{query}」</> : null}</p>
              <button type="button" className="ds-btn ds-btn--primary" onClick={clear}>返回全部收藏</button>
            </div>
          ) : (
            <div className="ds-empty" role="status">
              <p className="ds-empty-title">{cards.length === 0 ? '还没有收藏' : '这个筛选下没有图'}</p>
              <p className="ds-empty-text">
                {cards.length === 0 ? '先放几张参考图进来：从相册选，或者直接把图片拖进窗口。'
                  : '点上面「当前筛选」里的 × 去掉条件，就能看到所有图。'}
              </p>
              <div className="ds-empty-actions">
                {onImportFiles && cards.length === 0 && (
                  <label className="ds-btn ds-btn--primary ds-file-btn">
                    去导入
                    <input type="file" accept="image/*" multiple onChange={event => {
                      const files = Array.from(event.currentTarget.files ?? [])
                      event.currentTarget.value = ''
                      if (files.length) onImportFiles(files)
                    }} />
                  </label>
                )}
              </div>
            </div>
          )
        ) : results?.groups ? (
          <div className="m-ref-cols">
            {REF_GROUPS.map((key, gi) => (
              <div className="m-ref-col" key={key}>
                <h3>{key}</h3>
                <div className={`m-ref-grid${results.reasons ? ' m-ref-examples' : ''}`}>
                  {refGroups[gi].map((it, i) => {
                    const on = picked.includes(it)
                    const note = results.referenceNotes?.[gi]?.[it.id] ?? results.reasons?.[it.id]
                    return (
                      <div key={it.id} className={`m-ref-cell${on ? ' is-picked' : ''}`} data-pick-id={it.id}>
                        {cardNode(it, i)}
                        <button type="button" className="m-pick" aria-pressed={on} aria-label={on ? '取消选中' : '选中'}
                          onClick={() => togglePick(it)}>
                          <svg viewBox="0 0 16 16" width="10" height="10" fill="none" stroke="currentColor" strokeWidth={2}
                            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" /></svg>
                        </button>
                        {note && <p className="m-ref-reason">{note}</p>}
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="m-tiles" ref={wallEl} key={`${sort}-${hue ?? 'all'}`}>
            <MasonryPhotoAlbum<WallPhoto> photos={photos} spacing={gap} columns={mobile ? 2 : colCount} padding={0}
              render={{ photo: (_, { photo: { item: it, index: i, src } }) => {
              const title = cardTitle(it.title, it.description, it.tags)
              const source = sourceLabel(it)
              const busy = analyzing?.has(it.id) ?? false
              // 首屏前 4 张先下载（公网慢时不让它们排在别的请求后面），其余照旧懒加载
              const firstRow = i < FIRST_IMAGES
              // 没有云端分析的图不写「还没有标题 / 本地」；只在有标题或正在分析时显示图注。
              const showCaption = !!title || busy
              return (
                <button type="button" key={it.id} className="m-tile" onClick={() => onOpen(it, filtered, i)}
                  data-id={it.id}
                  aria-label={`${title ? `${title} · ` : ''}${source}${busy ? ' · 分析中' : ''}`} data-reveal
                  style={{ '--rv-d': enterDelayMs(i) } as React.CSSProperties}>
                  <img src={src} alt="" loading={firstRow ? 'eager' : 'lazy'}
                    fetchPriority={firstRow ? 'high' : undefined} width={it.w} height={it.h}
                    onLoad={firstRow ? firstImageDone : undefined} onError={firstRow ? firstImageDone : undefined} />
                  {showCaption && <span className="m-tile-cap">
                    {title && <span className="m-tile-title">{title}</span>}
                    <span className="m-tile-src">
                      {/* data-local：手机上不显示「本地」两个字 */}
                      {title && <span data-local={source === '本地' || undefined}>{source}</span>}
                      {busy && <span className="ds-analyzing" title="正在分析这张图，标题和标签稍后出现">分析中</span>}
                    </span>
                  </span>}
                </button>
              )
            } }} />
          </div>
        )}

      </section>

      {/* 要参考里勾了图：底部一条「已选 N 张 · 存为新项目」，往下翻着勾也随时点得到 */}
      {results?.groups && picked.length > 0 && !searching && (
        <div className="m-pick-bar">
          <button type="button" className="ds-btn ds-btn--primary m-pick-save" onClick={saveAsProject}>
            已选 {picked.length} 张 · {results.projectId ? '加入项目' : '存为新项目'}
          </button>
        </div>
      )}

      {saving && (
        <SaveToProject job={saving} target={navTarget()}
          onArrive={() => { onSaveProject(saving.name, saving.items); setPicked([]) }}
          onDone={() => setSaving(null)} />
      )}
    </div>
  )
}

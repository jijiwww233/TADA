import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
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
import { shortTitle } from './caption'
import type { SortKey } from './sorts'
import type { WiringApi } from '../api/wiring'
import { useMobileLayout } from './useMobileLayout'
import { REFINE } from './refine'
import './library-wall.css'

type WallPhoto = Photo & { item: LabImage; index: number }
const REF_GROUPS = ['排版', '配色', '风格']
const hueGap = (a: number, b: number) => { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d }
/** 瀑布流一列大约多宽：column 宽度按内容宽 / 295 取整算列数（1440 下 5 列） */
const COL_UNIT = 288
const colCount = (width: number) => Math.min(5, Math.max(2, Math.round(width / COL_UNIT)))

/**
 * 素材库：第一行就是搜索（大标题本身是输入框），细线下面直接是瀑布流；找图替换墙，要参考换成三列。
 * 瀑布流用 现有 masonry 相册（图 + 图注一块儿放进一列，不会互相压）；筛选/排序挪到顶栏，这里只吃 sort 入参。
 */
export function Library({ manifest, initialQuery = '', onOpen, platforms, hue, onHue: setHue, wallRequest, onSaveProject, results, searching, onSearch, onClear, searchActive, reduce = false, sort = 'time', api, analyzing, onImportFiles }: {
  /** 从引导页示例搜索框带过来的初始关键词 */
  initialQuery?: string
  /** 此刻正在分析的图片 id：卡片上标「分析中」 */
  analyzing?: Set<number>
  /** 搜不到 / 库是空的时候：直接打开相册导入 */
  onImportFiles?: (files: File[]) => void
  manifest: Manifest
  /** list：当前筛选结果，详情页按它显示“第几张 / 共几张” */
  onOpen: (item: LabImage, list: LabImage[], index: number) => void
  /** 顶栏的「待确认」已经接走，这个入参保留不动 MainApp 的传参 */
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
  /** 顶栏的「待确认」已经接走，这个入参保留不动 MainApp 的传参 */
  pendingCount?: number
  searchActive: boolean
  /** 「减少动效」开关（主页目前没有时序动画，保留这个入参不动 MainApp 的传参） */
  reduce?: boolean
  /** 顶栏排序菜单传给瀑布流的排序档，默认按时间 */
  sort?: SortKey
  /** 图注要的短标题在详情接口里（列表接口不带 description/tags），滚到眼前时按需取 */
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
  /** 图注短标题的缓存：列表数据没有描述时，滚到眼前的那几张按 id 取一次详情，取到就补上标题 */
  const [captions, setCaptions] = useState<Record<number, string>>({})
  const captionAsked = useRef(new Set<number>())
  const captionQueue = useRef<number[]>([])
  const captionFlying = useRef(0)
  const captionLive = useRef(true)
  useEffect(() => {
    captionLive.current = true
    return () => { captionLive.current = false }
  }, [])

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

  // 按需补图注标题：一次最多 4 个请求，只取滚到眼前（含上下 400px）的那些
  const collectCaption = useCallback((id: number) => {
    if (!api || captionAsked.current.has(id)) return
    captionAsked.current.add(id)
    captionQueue.current.push(id)
    const pump = () => {
      while (captionLive.current && captionFlying.current < 4 && captionQueue.current.length) {
        const next = captionQueue.current.shift() as number
        captionFlying.current += 1
        api.getDetail(next).then(detail => {
          if (!captionLive.current || !detail) return
          const title = shortTitle(detail.description, detail.tags)
          if (title) setCaptions(previous => ({ ...previous, [next]: title }))
        }).catch(() => {}).finally(() => { captionFlying.current -= 1; pump() })
      }
    }
    pump()
  }, [api])
  useEffect(() => {
    const node = wallEl.current
    if (!node || !api || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        const id = Number((entry.target as HTMLElement).dataset.id)
        if (id) collectCaption(id)
      }
    }, { rootMargin: '400px 0px' })
    const observeTiles = () => node.querySelectorAll<HTMLElement>('.m-tile[data-enrich]').forEach(tile => observer.observe(tile))
    observeTiles()
    // The album may first mount its columns after measuring the container.
    const columns = new MutationObserver(observeTiles)
    columns.observe(node, { childList: true, subtree: true })
    return () => { columns.disconnect(); observer.disconnect() }
  }, [api, collectCaption, filtered, sort, hue])

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

      <section className="m-wall-wrap" ref={wallTop}>
        {submitted && (
          <div className="m-result-line" data-reveal>
            <span>{query || (mode === 'find' ? '全部' : '参考')}</span>
            {results?.reasons && <span className="m-pick-hint">示例解读</span>}
            {results?.referenceSource && <span className="m-pick-hint">{results.referenceSource}</span>}
            {results?.groups && picked.length > 0 && (
              <button type="button" className="ds-btn ds-btn--primary ds-btn--sm" onClick={saveAsProject}>{results?.projectId ? '加入项目' : '存为新项目'} · {picked.length} 张</button>
            )}
            {/* 引导版：还没勾的时候先说一句能勾，不然「存为新项目」永远等不到人发现 */}
            {REFINE && results?.groups && picked.length === 0 && (
              <span className="m-pick-hint">点图片右上角的圈勾选，可以{results?.projectId ? '加入项目' : '存为新项目'}</span>
            )}
            <button type="button" className="ds-btn ds-btn--ghost ds-btn--sm" onClick={clear}>返回全部收藏</button>
          </div>
        )}

        {filtered.length === 0 ? (
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
                  : '打开右上角「筛选」，点「全部」就能看到所有图。'}
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
              const title = shortTitle(it.description, it.tags) || captions[it.id] || ''
              const source = sourceLabel(it)
              const busy = analyzing?.has(it.id) ?? false
              const firstRow = i < (mobile ? 2 : colCount(wallEl.current?.clientWidth ?? window.innerWidth))
              // 没有云端分析的图不写「还没有标题 / 本地」；只在有标题或正在分析时显示图注。
              const showCaption = !!title || busy
              return (
                <button type="button" key={it.id} className="m-tile" onClick={() => onOpen(it, filtered, i)}
                  data-id={it.id} data-enrich={!title && api ? '' : undefined}
                  aria-label={`${title ? `${title} · ` : ''}${source}${busy ? ' · 分析中' : ''}`} data-reveal
                  style={{ '--rv-d': enterDelayMs(i) } as React.CSSProperties}>
                  <img src={src} alt="" loading={firstRow ? 'eager' : 'lazy'}
                    fetchPriority={firstRow ? 'high' : undefined} width={it.w} height={it.h} />
                  {showCaption && <span className="m-tile-cap">
                    {title && <span className="m-tile-title">{title}</span>}
                    <span className="m-tile-src">
                      {title && <span>{source}</span>}
                      {busy && <span className="ds-analyzing" title="正在分析这张图，标题和标签稍后出现">分析中</span>}
                    </span>
                  </span>}
                </button>
              )
            } }} />
          </div>
        )}

      </section>

      {saving && (
        <SaveToProject job={saving} target={navTarget()}
          onArrive={() => { onSaveProject(saving.name, saving.items); setPicked([]) }}
          onDone={() => setSaving(null)} />
      )}
    </div>
  )
}

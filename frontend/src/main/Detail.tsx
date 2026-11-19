import { useEffect, useLayoutEffect, useRef, useState } from 'react'
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

/** 收藏日期：今年写“10月12日”，往年带上年份。 */
function savedLabel(iso: string | null | undefined): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const md = `${d.getMonth() + 1}月${d.getDate()}日`
  return d.getFullYear() === new Date().getFullYear() ? md : `${d.getFullYear()}年${md}`
}
const fileName = (src: string) => src.split('/').pop() || 'image'
const pad2 = (n: number) => String(n).padStart(2, '0')
const pad3 = (n: number) => String(n).padStart(3, '0')
const ROMAN = ['i.', 'ii.', 'iii.']
type DetailData = NonNullable<Awaited<ReturnType<WiringApi['getDetail']>>>
/** 有真正的字（不是空串、空格或「· · ·」这类只有符号的占位） */
const meaningful = (s?: string | null): s is string => !!s && /[\p{L}\p{N}]/u.test(s)
/** 按帖内顺序，取第一张有这项信息的图的值 */
function firstOf<T>(items: LabImage[], pick: (item: LabImage) => T | null | undefined): T | undefined {
  for (const item of items) { const value = pick(item); if (value != null) return value }
  return undefined
}
const postKeyOf = (item: LabImage | undefined) => !item ? '' : item.post_id != null ? `p${item.post_id}` : `i${item.id}`
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
  onProjectEdit, projectEditPending, analysis = null }: {
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
  useEffect(() => { setHoverColor(null) }, [imageId])
  const [draft, setDraft] = useState('')
  // 进入时第一屏看得到的图（null = 还没量，先都带 data-reveal）；量完以后其余的图去掉 data-reveal，滚出来直接显示
  const [firstScreen, setFirstScreen] = useState<Set<number> | null>(null)
  // 进入后有没有滚到过别的图：滚过以后文字不再逐词出现
  const [moved, setMoved] = useState(false)

  // 触屏（手机 / iPad）：右侧信息按「帖子」固定显示——同一帖里往下滑换图，标题、标签、描述都不变；
  // 某张图自己没分析时沿用同帖其它图的。为此把同帖每张图的详情都取回来（最多 12 张）。电脑上不走这套。
  const touch = useTouchLayout()
  const [known, setKnown] = useState<Map<number, DetailData>>(() => new Map())
  const asked = useRef(new Set<number>())
  useEffect(() => {
    if (detail) setKnown(previous => previous.get(detail.id) === detail ? previous : new Map(previous).set(detail.id, detail))
  }, [detail])
  const siblingIds = touch && image ? postOf(image).slice(0, 12).map(item => item.id).join(',') : ''
  useEffect(() => {
    if (!siblingIds) return
    for (const id of siblingIds.split(',').map(Number)) {
      if (asked.current.has(id)) continue
      asked.current.add(id)
      api.getDetail(id).then(value => { if (value) setKnown(previous => new Map(previous).set(id, value)) }, () => {})
    }
  }, [api, siblingIds])
  const postKey = postKeyOf(image)
  const [startPost] = useState(() => postKeyOf(list[Math.max(0, index)]))
  const [leftStartPost, setLeftStartPost] = useState(false)
  useEffect(() => { if (postKey !== startPost) setLeftStartPost(true) }, [postKey, startPost])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !adding) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, adding])

  // 点开哪张就直接停在哪张（用原有滚动的瞬移）
  useLayoutEffect(() => {
    const el = plates.current[index]
    const y = !mobile && el && index > 0 ? el.getBoundingClientRect().top + window.scrollY - 24 : 0
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

  // 当前张 = 跨过视口中线的那张；页面滚动由 Lenis 驱动，这里只读位置
  useEffect(() => {
    if (mobile) return
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
  }, [mobile])

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
  // 触屏：整帖的信息（帖内顺序，当前这张用已合并详情的版本）
  const postImages = post.map(item => item.id === current.id ? current : known.has(item.id) ? mergeDetail(item, known.get(item.id)!) : item)
  const postTitle = firstOf(postImages, item => meaningful(item.post_title) ? item.post_title.trim() : null)
  const postSubject = firstOf(postImages, item => { const s = (item.subject ?? []).filter(meaningful); return s.length ? s : null }) ?? []
  const postStyle = firstOf(postImages, item => { const s = (item.style_tags ?? []).filter(meaningful); return s.length ? s : null }) ?? []
  const postIntro = firstOf(postImages, item => meaningful(item.description) ? item.description.trim() : null) ?? ''
  const local = source === '本地'
  // 什么分析都没有时的标题：不出现「本地」
  const plainTitle = local
    ? (saved ? `${saved}收藏的图` : imported ? `${imported}导入的图` : '未命名的图')
    : (saved ? `${source} · ${saved}` : source)
  const title = touch ? postTitle || postSubject.join('、') || plainTitle
    : subject.join('、') || current.post_title || (saved ? `${source} · ${saved}` : source)
  const postTheme = [postSubject.join('、'), ...postStyle.slice(0, 2)].filter(Boolean).join(' · ')
  // 触屏：主题行只是把标题重复一遍时就不显示
  const theme = touch ? (postTheme === title ? '' : postTheme)
    : [subject.join('、'), ...(current.style_tags ?? []).slice(0, 2)].filter(Boolean).join(' · ')
  const intro = touch ? postIntro : current.description || current.post_title || ''
  const notes = [intro && { key: 'intro', text: intro }, theme && { key: 'theme', text: theme }].filter(Boolean) as { key: string; text: string }[]
  // 详情页长相退回最初版；唯一的文字改动：「收藏」一栏原来显示「未知」，改成「未记录收藏夹」+ 一行小字说明原因
  const savedNote = imported ? `导入时没带收藏夹信息 · ${imported}导入` : '导入时没带收藏夹信息'
  // 分析中提示：详情已经取回、但还没有分析结果时才显示
  const analysisNote = actual && !actual.analysis ? pendingAnalysisNote(analysis, current.id) : null
  const sourceText = touch && local ? '自己导入' : source
  const colors = current.palette.slice(0, 3)
  const currentTags = tags.get(current.id) ?? current.tags ?? []
  // 触屏：显示整帖所有图的标签（去重、去掉空占位）；「+ 标签」仍加在当前这张上
  const shownTags = touch
    // 按帖内顺序合并（第一张的标签在前），换图时顺序不变；当前这张不在帖内列表里时它的标签排最后
    ? [...new Set([...postImages.flatMap(item => tags.get(item.id) ?? item.tags ?? []), ...currentTags].filter(meaningful))]
    : currentTags
  // 触屏：同一帖里换图时，帖子级的文字不重播、不闪；换到别的帖子才淡入新内容
  const infoKey = touch ? postKey : String(at)
  const postMoved = touch ? leftStartPost || postKey !== startPost : moved
  const last = at === list.length - 1
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
            <p className="m-ld-note m-ld-intro" key={`intro-${infoKey}`}><i>{ROMAN[0]}</i><Words text={notes[0].text} moved={postMoved} /></p>
          )}
        </div>
        {notes.find(n => n.key === 'theme') && (
          <p className="m-ld-note m-ld-theme" key={`theme-${infoKey}`}>
            <i>{ROMAN[notes.findIndex(n => n.key === 'theme')]}</i><Words text={theme} moved={postMoved} />
          </p>
        )}
        <div className="m-ld-bottom">
          {post.length > 1 && <p className="m-ld-cap">(同帖 {post.length} 张)</p>}
          <div className="m-ld-foot">
            <div className="m-ld-thumbs">
              {post.slice(0, 4).map((p, i) => (
                <img key={`${p.id}-${i}`} src={thumbnailSrc(p.src)} alt="" className={p.id === current.id ? 'is-on' : ''} />
              ))}
            </div>
            <p className="m-ld-links">
              <a href={current.src} download={actual?.download.filename || current.source || fileName(current.src)}>保存 ↓</a>
              {originalUrl && <a href={originalUrl} target="_blank" rel="noopener noreferrer">打开原帖 ↗</a>}
            </p>
          </div>
        </div>
      </aside>
  )
  return (
    <section className={pane ? 'm-ld m-ld--pane' : 'm-ld'} aria-label="图片详情" style={{ '--ld-text-at': `${textAt}ms` } as React.CSSProperties}>
      {!pane && leftAside}
      {mobile && list.length > 1 && <ThumbnailStrip list={list} index={at} onSelect={selectImage} active />}
      <div className="m-ld-center" style={onProjectEdit && !mobile && list.length > 1 ? { paddingBottom: '50vh' } : undefined}>
        {list.map((p, i) => (!mobile || i === at) && (
          <div className="m-ld-plate" key={`${p.id}-${i}`}
            data-reveal={(mobile ? !moved : firstScreen === null || firstScreen.has(i)) ? '' : undefined} ref={el => { plates.current[i] = el }}>
            <img src={p.src} alt="" width={p.w} height={p.h} loading={mobile || Math.abs(i - index) < 2 ? 'eager' : 'lazy'} />
          </div>
        ))}
      </div>

      <aside className="m-ld-side m-ld-right" data-lenis-prevent={pane ? '' : undefined}>
        {/* iPad：左栏（返回、描述、保存 / 原帖）收进右栏，整栏一起吸顶、可单独滚动 */}
        {pane && leftAside}
        <div className="m-ld-top">
          <div className="m-ld-index">
            <span className="m-ld-no" key={`no-${at}`}><Words text={pad2(at + 1)} moved={moved} /></span>
            <span className="m-ld-range">{pad3(1)} – {pad3(list.length)}</span>
          </div>
          {/* 不写“往下滚”这类解释界面的话；到头了才说一声 */}
          <p className="m-ld-hint">{last ? '(最后一张)' : ' '}</p>
          <p className="m-ld-tags">
            {shownTags.map((t, i) => <span key={t}>{i > 0 && ' · '}{t}</span>)}
            {shownTags.length > 0 && ' · '}
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
        <div className="m-ld-bottom" key={`spec-${infoKey}`}>
          {analysisNote && (
            <p className="ds-analysis-note" role="status">
              <span className="ds-analyzing">{analysisNote.label}</span>
              <span>{analysisNote.note}</span>
            </p>
          )}
          <h1 className="m-ld-title"><Words text={title} moved={postMoved} /></h1>
          <dl className="m-ld-spec">
            <div><dt>来源</dt><dd><Words text={sourceText} moved={moved} /></dd></div>
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
            {choosingProject && projects.length ? (
              <select className="m-set-input" aria-label="选择项目" value="" onChange={e => {
                const id = Number(e.currentTarget.value)
                if (id) onAdd(id, current.id)
                setChoosingProject(false)
              }}>
                <option value="">选择项目</option>
                {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
              </select>
            ) : <button type="button" onClick={() => projects.length ? setChoosingProject(true) : onToast('没有可加入的项目')}>加入项目</button>}
            {' · '}
            <button type="button" title={REFINE ? '弹出一块置顶的画板，边做设计边看参考' : undefined}
              onClick={() => onPip ? onPip(current) : onToast('还没接上')}>{REFINE ? '打开参考画板' : '打开小窗'}</button>
          </p>
        </div>
      </aside>

      {/* 手机：三个主要操作钉在屏幕底部（样式见 styles/phone.css）。电脑上不渲染，原来的按钮照旧 */}
      {phone && (
        <div className="m-ld-bar" role="toolbar" aria-label="这张图的操作">
          <button type="button" className="ds-btn ds-btn--primary" onClick={() => onSimilar(current)}>找相似</button>
          {colors.length > 0 && (
            <button type="button" className="ds-btn ds-btn--secondary" onClick={() => onColor(colors[hoverColor ?? 0] ?? colors[0])}>
              <span className="m-ld-samecolor-dot" style={{ background: (colors[hoverColor ?? 0] ?? colors[0]).hex }} aria-hidden="true" />按颜色找
            </button>
          )}
          {projects.length ? (
            // 手机上直接用系统的选择器：整颗按钮下面垫一个透明的 <select>，点一下就弹出项目列表
            <label className="ds-btn ds-btn--secondary m-ld-bar-pick">
              加入项目
              <select aria-label="加入项目" value="" onChange={e => {
                const id = Number(e.currentTarget.value)
                if (id) onAdd(id, current.id)
              }}>
                <option value="" disabled>选择项目</option>
                {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
              </select>
            </label>
          ) : (
            <button type="button" className="ds-btn ds-btn--secondary" onClick={() => onToast('还没有项目：先去「项目」页新建一个')}>加入项目</button>
          )}
        </div>
      )}
    </section>
  )
}

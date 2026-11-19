import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { SlidersHorizontal } from 'lucide'
import { collectionPalette, rng, type LabImage, type Manifest } from '../lab/data'
import { Icon } from '../lab/Icon'
import { ArcGallery } from './ArcGallery'
import { Capsule, type Mode } from './Capsule'
import { CapsuleMesh } from './Mesh'
import { PLATFORMS, sourceLabel } from './meta'

const NOTES = [
  '收藏不是囤积，是给将来的自己留一条线索。',
  '先存下来，再决定它属于哪个项目。',
  '好版式不吵，它只是把顺序排对了。',
  '颜色是最快的记忆索引。',
]
const SORTS: { k: 'time' | 'color' | 'similar'; label: string }[] = [
  { k: 'time', label: '时间' },
  { k: 'color', label: '颜色' },
  { k: 'similar', label: '像不像' },
]
const REF_GROUPS = [
  { key: '排版', desc: '标题、网格、留白', captions: ['学它的标题阶梯', '学它的单栏压印', '学它的留白节奏'] },
  { key: '配色', desc: '色相、明度、面积', captions: ['学它的单色叠印', '学它的旧纸色温', '学它的冷暖对比'] },
  { key: '风格', desc: '年代、材质、气质', captions: ['学它的年代颗粒', '学它的手写标注', '学它的拼贴质感'] },
]
const hueGap = (a: number, b: number) => { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d }

/** 素材库：首屏只有标题和胶囊；往下滑是瀑布流；找图替换墙，要参考换成三列。 */
export function Library({ manifest, onOpen, onPending, platforms, onPlatforms, reduced }: {
  manifest: Manifest
  onOpen: (item: LabImage) => void
  onPending: () => void
  platforms: Set<string>
  onPlatforms: (next: Set<string>) => void
  reduced: boolean
}) {
  const [mode, setMode] = useState<Mode>('find')
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [sort, setSort] = useState<'time' | 'color' | 'similar'>('time')
  const [filterOpen, setFilterOpen] = useState(false)
  const [hue, setHue] = useState<number | null>(null)
  const wallTop = useRef<HTMLDivElement | null>(null)
  const filterRef = useRef<HTMLDivElement | null>(null)

  // 示例图只有 10 张，按不同种子洗四轮再拼，避免同一列上下出现同一张。
  const pale = useMemo(() => collectionPalette(manifest, 2, 0.91, 0.045), [manifest])

  const cards = useMemo(() => {
    const out: LabImage[] = []
    for (let k = 0; k < 4; k++) {
      const deck = manifest.cards.slice()
      const r = rng(k + 11)
      for (let i = deck.length - 1; i > 0; i--) {
        const j = Math.floor(r() * (i + 1))
        const t = deck[i]
        deck[i] = deck[j]
        deck[j] = t
      }
      out.push(...deck)
    }
    return out.slice(0, 42)
  }, [manifest])

  const perPlatform = useMemo(() => {
    const m = new Map<string, number>()
    for (const it of manifest.cards) m.set(sourceLabel(it), (m.get(sourceLabel(it)) ?? 0) + 1)
    return m
  }, [manifest])

  const sorted = useMemo(() => {
    const list = cards.slice()
    if (hue !== null) {
      const near = (it: LabImage) => it.hues.length ? Math.min(...it.hues.map(h => hueGap(h, hue))) : 999
      list.sort((a, b) => near(a) - near(b))
    } else if (sort === 'color') list.sort((a, b) => (a.hues[0] ?? 0) - (b.hues[0] ?? 0))
    else if (sort === 'time') list.sort((a, b) => b.id - a.id)
    else list.sort((a, b) => (a.id * 7919) % 101 - (b.id * 7919) % 101)
    return list
  }, [cards, sort, hue])

  const filtered = useMemo(() => sorted.filter(it => platforms.size === 0 || platforms.has(sourceLabel(it))), [sorted, platforms])

  useEffect(() => {
    if (!submitted || !wallTop.current) return
    const top = wallTop.current.getBoundingClientRect().top + window.scrollY - 60
    window.scrollTo({ top, behavior: 'smooth' })
  }, [submitted])

  useEffect(() => {
    if (!filterOpen) return
    const onDown = (e: PointerEvent) => { if (filterRef.current && !filterRef.current.contains(e.target as Node)) setFilterOpen(false) }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [filterOpen])

  const submit = (text: string) => { setQuery(text); setSubmitted(true) }
  const clear = () => { setSubmitted(false); setQuery('') }
  const togglePlatform = (p: string) => {
    const next = new Set(platforms)
    if (next.has(p)) next.delete(p); else next.add(p)
    onPlatforms(next)
  }

  const cardInner = (it: LabImage) => (
    <>
      <img src={it.src} alt="" loading="lazy" />
      <span className="m-card-platform">{sourceLabel(it)}</span>
      <span className="m-card-meta">
        <span>{sourceLabel(it)} · {String(it.id).padStart(3, '0')}</span>
        <span>{it.w}×{it.h}</span>
      </span>
    </>
  )

  const cardNode = (it: LabImage, i: number, cols: number): ReactNode => (
    <button type="button" key={`${it.id}-${cols}-${i}`} className="m-card" onClick={() => onOpen(it)}
      aria-label={`打开收藏 ${String(it.id).padStart(3, '0')}`}>
      {cardInner(it)}
    </button>
  )

  const refNode = (it: LabImage, i: number, caption: string): ReactNode => (
    <div className="m-ref-item" key={`${it.id}-ref-${i}`}>
      <button type="button" className="m-card" onClick={() => onOpen(it)} aria-label={`打开参考 ${String(it.id).padStart(3, '0')}`}>
        {cardInner(it)}
      </button>
      <p className="m-ref-caption">{caption}</p>
    </div>
  )

  const wallNodes = (list: LabImage[]): ReactNode[] => {
    const nodes: ReactNode[] = []
    list.forEach((it, i) => {
      if (i > 0 && i % 9 === 0) {
        nodes.push(<div key={`note-${i}`} className="m-note-card"><p>{NOTES[(i / 9) % NOTES.length | 0]}</p><small>收藏的旁注</small></div>)
      }
      if (i > 0 && i % 14 === 0) {
        nodes.push(
          <div key={`palette-${i}`} className="m-palette-card">
            <div className="m-pal">{it.palette.slice(0, 5).map(s => <i key={s.hex} style={{ background: s.hex }} />)}</div>
            <small>这张图的取色</small>
          </div>,
        )
      }
      nodes.push(cardNode(it, i, 5))
    })
    return nodes
  }

  const refGroups = [0, 1, 2].map(k => filtered.filter((_, i) => i % 3 === k).slice(0, 8))

  return (
    <>
      <section className="m-hero">
        <ArcGallery manifest={manifest} reduced={reduced} />
        <CapsuleMesh colors={['#ffffff', pale[0] ?? '#f8eee9', '#ffffff', pale[1] ?? '#f2f1ee']} />
        <h1 className="m-hero-title">I know I saved it</h1>
        <div className="m-hero-capsule">
          <Capsule mode={mode} onMode={setMode} onSearch={submit} hue={hue} onHue={setHue} />
        </div>
        <p className="m-hero-subtitle">Nothing is lost that may be found.</p>
      </section>

      <section className="m-wall-wrap" ref={wallTop}>
        <div className="m-controls">
          <span className="m-controls-label">排序</span>
          <div className="m-seg">
            {SORTS.map(s => (
              <button key={s.k} type="button" className={sort === s.k ? 'is-on' : ''} onClick={() => setSort(s.k)}>{s.label}</button>
            ))}
          </div>
          <div className="m-filter-wrap" ref={filterRef}>
            <button type="button" className="m-filter-btn" aria-expanded={filterOpen} onClick={() => setFilterOpen(o => !o)}>
              <Icon icon={SlidersHorizontal} size={14} /> 筛选
              {platforms.size > 0 && <span className="m-filter-count">{platforms.size}</span>}
            </button>
            {filterOpen && (
              <div className="m-filter-pop">
                <p className="m-filter-title">平台</p>
                {PLATFORMS.map(p => (
                  <button key={p} type="button" className={`m-filter-item ${platforms.has(p) ? 'is-on' : ''}`} onClick={() => togglePlatform(p)}>
                    <i />
                    <span>{p}</span>
                    <span style={{ marginLeft: 'auto', fontFamily: 'var(--m-mono)', fontSize: 9, color: 'var(--m-ink-3)' }}>{perPlatform.get(p) ?? 0}</span>
                  </button>
                ))}
                <div className="m-filter-foot">
                  <button type="button" onClick={() => onPlatforms(new Set())}>全部平台</button>
                </div>
              </div>
            )}
          </div>
          {hue !== null && (
            <button type="button" className="m-chip m-hue is-on" onClick={() => setHue(null)}>
              <i style={{ background: `oklch(0.72 0.15 ${hue})` }} />色相 {Math.round(hue)}° ×
            </button>
          )}
          <span className="m-spacer" />
          <button type="button" className="m-badge" onClick={onPending}><b>12</b> 张待确认</button>
        </div>

        {submitted && (
          <div className="m-result-head">
            <h2>{mode === 'find' ? '找图' : '要参考'} · {query || '全部'}</h2>
            <span>{mode === 'find' ? `${filtered.length} 张` : '三组参考'}</span>
            <button type="button" onClick={clear}>清除</button>
          </div>
        )}

        {filtered.length === 0 ? (
          <p style={{ color: 'var(--m-ink-3)', fontSize: 13, padding: '40px 0' }}>没有符合的图，换个平台或颜色试试。</p>
        ) : submitted && mode === 'ref' ? (
          <div className="m-ref-cols">
            {REF_GROUPS.map((g, gi) => (
              <div className="m-ref-col" key={g.key}>
                <h3>{g.key}</h3>
                <p>{g.desc}</p>
                <div className="m-ref-grid">
                  {refGroups[gi].map((it, i) => refNode(it, i, g.captions[i % g.captions.length]))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className={`m-wall ${submitted ? 'm-wall-four' : ''}`}>
            {submitted ? filtered.slice(0, 24).map((it, i) => cardNode(it, i, 4)) : wallNodes(filtered)}
          </div>
        )}
      </section>
    </>
  )
}

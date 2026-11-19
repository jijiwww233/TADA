import { useMemo, useState } from 'react'
import { ArrowLeft, FolderClosed, PictureInPicture2, Plus, Sparkles } from 'lucide'
import { Icon } from '../lab/Icon'
import { take, type LabImage, type Manifest } from '../lab/data'

type Project = { id: string; name: string; note: string; items: LabImage[] }

/** 项目页：另一种状态——在做事，不是在找东西。 */
export function Projects({ manifest, onOpen, onToast }: {
  manifest: Manifest
  onOpen: (item: LabImage) => void
  onToast: (s: string) => void
}) {
  const seed = useMemo<Project[]>(() => [
    { id: 'p1', name: '品牌手册 · 2026 春', note: '把古着海报的排版搬进手册', items: take(manifest.cards, 14) },
    { id: 'p2', name: '咖啡包装参考', note: '牛皮纸、油墨、手写标签', items: take(manifest.cards.slice(3), 11) },
    { id: 'p3', name: '日式版式研究', note: '竖排、留白、细线', items: take(manifest.cards.slice(6), 9) },
  ], [manifest])
  const [projects, setProjects] = useState<Project[]>(seed)
  const [active, setActive] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [suggest, setSuggest] = useState<LabImage[]>([])

  const current = projects.find(p => p.id === active) ?? null

  const create = () => {
    const title = name.trim()
    if (!title) { onToast('先写一句要做什么（演示）'); return }
    const items = take(manifest.cards, 12)
    const p: Project = { id: `p${Date.now()}`, name: title, note: title, items }
    setProjects([p, ...projects])
    setSuggest(items.slice(0, 6))
    setName('')
    onToast('项目已创建 · 从收藏里推荐了 12 张')
  }

  if (current) {
    const wall = take(current.items, 18)
    return (
      <section className="m-page">
        <button type="button" className="m-back" onClick={() => setActive(null)}><Icon icon={ArrowLeft} size={14} /> 所有项目</button>
        <div className="m-page-head" style={{ marginTop: 18 }}>
          <h1>{current.name}</h1>
          <span className="m-spacer" />
          <button type="button" className="m-btn" onClick={() => onToast('参考小窗会单独开一个窗口（演示）')}><Icon icon={PictureInPicture2} size={15} /> 打开参考小窗</button>
          <button type="button" className="m-btn m-ghost" onClick={() => onToast('把图拖进项目就能钉进来（演示）')}><Icon icon={Plus} size={15} /> 钉图</button>
        </div>
        <p className="m-page-sub">{current.note} · {current.items.length} 张钉选</p>
        <div className="m-wall m-wall-three" style={{ marginTop: 26 }}>
          {wall.map((it, i) => (
            <button type="button" key={`${it.id}-${i}`} className="m-card" onClick={() => onOpen(it)} aria-label={`打开钉选 ${it.id}`}>
              <img src={it.src} alt="" loading="lazy" />
              <span className="m-card-meta"><span>{it.source} · {String(it.id).padStart(3, '0')}</span><span>钉选</span></span>
            </button>
          ))}
        </div>
      </section>
    )
  }

  return (
    <section className="m-page">
      <div className="m-page-head">
        <h1>项目</h1>
        <span className="m-spacer" />
        <span className="m-status is-ok">3 个进行中</span>
      </div>
      <p className="m-page-sub">做项目的时候才来这里。一个项目就是一组钉住的图，从这里打开参考小窗。</p>

      <div className="m-newproject">
        <div className="m-newproject-row">
          <input value={name} onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') create() }}
            placeholder="写一句要做什么：给一个咖啡品牌做手册，想要安静、纸张感" aria-label="新项目描述" />
          <button type="button" className="m-btn m-primary" onClick={create}><Icon icon={Sparkles} size={15} /> 创建项目</button>
        </div>
        {suggest.length > 0 ? (
          <div className="m-suggest">
            <span>刚推荐</span>
            <div className="m-suggest-thumbs">
              {suggest.map(it => <img key={it.id} src={it.src} alt="" />)}
            </div>
          </div>
        ) : (
          <div className="m-suggest">
            <span>会推荐</span>
            <p style={{ fontSize: 12, color: 'var(--m-ink-3)' }}>写一句话，马上从收藏里挑相关的图给你。</p>
            <div className="m-suggest-thumbs">
              {take(manifest.cards, 6).map((it, i) => <img key={`${it.id}-${i}`} src={it.src} alt="" />)}
            </div>
          </div>
        )}
      </div>

      <div className="m-folders">
        {projects.map(p => (
          <button type="button" key={p.id} className="m-folder" onClick={() => setActive(p.id)}>
            <div className="m-folder-previews">
              {p.items.slice(0, 3).map((it, i) => <img key={`${it.id}-${i}`} src={it.src} alt="" loading="lazy" />)}
            </div>
            <div className="m-folder-name">{p.name}</div>
            <div className="m-folder-meta"><Icon icon={FolderClosed} size={10} /> {p.items.length} 张钉选 · 昨天更新</div>
          </button>
        ))}
      </div>
    </section>
  )
}

import { useEffect, useState } from 'react'
import { ArrowUpRight, ExternalLink, Plus, Sparkles, X } from 'lucide'
import { Icon } from '../lab/Icon'
import { sourceLabel } from './meta'
import type { LabImage } from '../lab/data'

/** 点开一张图：全屏大图 + 浮出的毛玻璃信息面板，标签在这里改。 */
export function Detail({ item, onClose, onToast }: { item: LabImage | null; onClose: () => void; onToast: (s: string) => void }) {
  const [tags, setTags] = useState<string[]>(['复古', '版式'])
  const [draft, setDraft] = useState('')
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  if (!item) return null
  return (
    <div className={`m-overlay ${item ? 'is-on' : ''}`} onClick={onClose}>
      <div className="m-overlay-bg" />
      <button type="button" className="m-close" aria-label="关闭" onClick={onClose}><Icon icon={X} size={17} /></button>
      <div className="m-detail" onClick={e => e.stopPropagation()}>
        <div className="m-detail-imgwrap">
          <img src={item.src} alt={`收藏 ${item.id}`} />
        </div>
        <aside className="m-detail-panel">
          <p className="m-detail-kicker">来自 {sourceLabel(item)} · 收藏 {String(item.id).padStart(3, '0')}</p>
          <h2>一张存下来的<br />版式参考</h2>
          <div className="m-detail-swatches">
            {item.palette.slice(0, 5).map(s => <i key={s.hex} style={{ background: s.hex }} title={s.hex} />)}
          </div>
          <div className="m-detail-hr" />
          <p className="m-detail-kicker">标签</p>
          <div className="m-detail-tags">
            {tags.map(t => (
              <span key={t} className="m-tag">{t}<button type="button" aria-label={`移除标签 ${t}`} onClick={() => setTags(tags.filter(x => x !== t))}>×</button></span>
            ))}
            <input value={draft} placeholder="加标签" aria-label="新标签"
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && draft.trim()) { setTags([...tags, draft.trim()]); setDraft('') }
              }} />
          </div>
          <div className="m-detail-hr" />
          <p className="m-detail-kicker">原始信息</p>
          <p style={{ fontSize: 12, color: 'var(--m-ink-2)', lineHeight: 1.7 }}>
            {item.w} × {item.h} · {sourceLabel(item)} · 已收录<br />
            颜色取自画面：{[...item.palette.slice(0, 3).map(s => s.hex)].join(' / ')}
          </p>
          <div className="m-detail-actions">
            <button type="button" className="m-btn m-primary" onClick={() => onToast('已加入项目（演示）')}><Icon icon={Plus} size={15} /> 加入项目</button>
            <button type="button" className="m-btn" onClick={() => onToast('正在找相似（演示）')}><Icon icon={Sparkles} size={15} /> 找相似</button>
            <button type="button" className="m-btn m-ghost" onClick={() => onToast('参考小窗会单独开一个窗口（演示）')}><Icon icon={ArrowUpRight} size={15} /> 打开小窗</button>
            <button type="button" className="m-btn m-ghost" onClick={() => onToast('已复制原图链接（演示）')}><Icon icon={ExternalLink} size={15} /> 原图</button>
          </div>
        </aside>
      </div>
    </div>
  )
}

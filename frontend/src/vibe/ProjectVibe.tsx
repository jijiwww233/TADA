import { useState } from 'react'
import type { Project } from '../projects/contract'
import { thumbnailSrc } from '../imageThumbnail'
import { PurposeSelect } from './PurposeSelect'
import { useProjectVibe } from './useProjectVibe'

export function ProjectVibe({ project, pinned, onPinned, onOpen }: {
  project: Project; pinned: ReadonlySet<number>; onPinned: (projectId: number) => void; onOpen: (imageId: number) => void
}) {
  const { items, busy, error, act } = useProjectVibe(project.id, project.brief, onPinned)
  const [purpose, setPurpose] = useState('')
  const visible = items.filter(item => !pinned.has(item.image_id))
  if (!project.brief.trim() || !visible.length) return null
  return <section aria-label="从你的收藏里捞回来的">
    <p>从你的收藏里捞回来的</p>
    <PurposeSelect value={purpose} onChange={setPurpose} disabled={busy !== null} />
    {error && <p role="alert">{error}</p>}
    <ul style={{ display: 'flex', flexWrap: 'wrap', gap: 12, listStyle: 'none', padding: 0 }}>
      {visible.map(image => <li key={image.image_id}>
        <button type="button" onClick={() => onOpen(image.image_id)}>
          <img src={thumbnailSrc(image.url)} alt={image.original_name || '收藏图片 #' + image.image_id}
            width={112} height={80} style={{ objectFit: 'contain' }} loading="lazy" />
        </button>
        <div>
          <button type="button" disabled={!purpose || busy !== null} onClick={() => void act(image.image_id, purpose)}>钉进项目</button>
          <button type="button" disabled={busy !== null} onClick={() => void act(image.image_id)}>不要这种</button>
        </div>
      </li>)}
    </ul>
  </section>
}

import { useState } from 'react'
import type { ImageEntity } from '../search/contract'
import { ImagePreview } from '../search/Search'
import type { Pin, Project } from './contract'
import type { LoadState } from './store'
import { purposeLabel } from './labels'

export function ProjectFeedback({ state, retry }: { state: LoadState; retry: () => void }) {
  if (state.status === 'loading') return <p role="status">读取项目中…</p>
  if (state.status === 'error') return <div role="alert">{state.error}<button type="button" onClick={retry}>重试</button></div>
  return null
}

// Adapted from d36cd1c Projects.tsx. No stylesheet, page layout or mount changes.
export function ProjectSelector({ projects, activeId, state, busy, select, create, retry }: {
  projects: Project[]; activeId: number | null; state: LoadState; busy: boolean
  select: (id: number | null) => void; create: (name: string, brief: string) => void; retry: () => void
}) {
  const [name, setName] = useState(''); const [brief, setBrief] = useState('')
  return <div>
    <label>当前项目<select value={activeId ?? ''} onChange={e => select(e.target.value ? Number(e.target.value) : null)}>
      <option value="">未选项目（沿用图库小窗）</option>
      {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select></label>
    <ProjectFeedback state={state} retry={retry} />
    {state.status === 'ok' && projects.length === 0 && <p>暂无项目，可以先创建一个。</p>}
    <form onSubmit={e => { e.preventDefault(); if (!busy && state.status !== 'loading' && name.trim()) create(name, brief) }}>
      <label>新项目名称<input value={name} onChange={e => setName(e.target.value)} /></label>
      <label>新项目 brief<textarea value={brief} onChange={e => setBrief(e.target.value)} /></label>
      <button type="submit" disabled={busy || state.status === 'loading' || !name.trim()}>创建项目</button>
    </form>
  </div>
}

function BriefForm({ project, busy, save }: { project: Project; busy: boolean; save: (brief: string) => void }) {
  const [brief, setBrief] = useState(project.brief)
  return <form onSubmit={e => { e.preventDefault(); if (!busy) save(brief) }}>
    <label>项目 brief<textarea value={brief} onChange={e => setBrief(e.target.value)} /></label>
    <button type="submit" disabled={busy}>保存项目 brief</button>
  </form>
}
export function ProjectBriefEditor(props: { project: Project; busy: boolean; save: (brief: string) => void }) {
  return <BriefForm key={props.project.id} {...props} />
}

export function PinControl({ imageId, allowed, suggested = [], initialNote = '', enabled, busy, pin }: {
  imageId: number; allowed: string[]; suggested?: string[]; initialNote?: string; enabled: boolean; busy: boolean
  pin: (purposes: string[], note?: string) => void
}) {
  const [selected, setSelected] = useState(() => [...new Set(suggested)].filter(p => allowed.includes(p)))
  const [note, setNote] = useState(initialNote); const [noteEdited, setNoteEdited] = useState(false)
  return <form onSubmit={e => { e.preventDefault(); if (enabled && !busy && selected.length) pin(selected, noteEdited ? note : undefined) }}>
    <fieldset disabled={!enabled || busy}>
      <legend>钉选图片 {imageId}：确认用途</legend>
      {allowed.map(p => <label key={p}><input type="checkbox" checked={selected.includes(p)}
        onChange={e => setSelected(old => e.target.checked ? [...old, p] : old.filter(x => x !== p))} />{purposeLabel(p)}</label>)}
      <label>钉选备注<input value={note} onChange={e => { setNote(e.target.value); setNoteEdited(true) }} /></label>
      <button type="submit" disabled={!selected.length || busy || !enabled}>{busy ? '保存中…' : '确认钉选'}</button>
    </fieldset>
    {!enabled && <small>选择项目并读取详情后可钉选。</small>}
  </form>
}

export function ProjectPins({ pins, imagesById, busyKeys, onOpen, remove, onPip }: {
  pins: Pin[]; imagesById: Readonly<Record<number, ImageEntity>>; busyKeys: string[]; onOpen: (id: number) => void
  remove: (imageId: number, purpose: string) => void; onPip?: () => void
}) {
  return <div><h3>已钉选</h3>{pins.length === 0 && <p>项目还没有钉选图片。</p>}
    {onPip && <button type="button" onClick={onPip}>使用项目钉选作为小窗来源</button>}
    <ul aria-label="项目钉选">{pins.map(p => <li key={p.image_id}>
      <ImagePreview id={p.image_id} image={imagesById[p.image_id]} onOpen={onOpen} />
      {p.needs_purpose && <p>用途待补充，请重新选择用途后钉选。</p>}
      <ul>{p.purposes.map(purpose => <li key={purpose}>{purposeLabel(purpose)}{' '}
        <button type="button" disabled={busyKeys.includes(`${p.project_id}:${p.image_id}`)}
          aria-label={`移除 ${purpose} #${p.image_id}`} onClick={() => remove(p.image_id, purpose)}>移除此用途</button>
      </li>)}</ul>{p.note && <p>{p.note}</p>}
    </li>)}</ul>
    <small>移除最后用途只移出项目，不删除图库图片。</small>
  </div>
}

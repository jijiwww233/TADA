import { useRef, useState } from 'react'
import { startImageDrag } from '../pip/dragData'
import { thumbnailSrc } from '../imageThumbnail'
import type { ImageEntity } from './contract'
import type { RequestState } from './store'

export function RequestFeedback({ state, retry }: { state: RequestState; retry: () => void }) {
  if (state.status === 'loading') return <p role="status">读取中…</p>
  if (state.status === 'error') return <div role="alert">{state.error} <button type="button" onClick={retry}>重试</button></div>
  if (state.status === 'index_empty') return <p role="status">还没有可搜索的分析结果，分析完成后会自动建立索引。</p>
  if (state.status === 'ok' && state.degraded) return <p className="muted" role="status">AI 搜索没连上，这次结果来自本地匹配</p>
  return null
}
export function SearchBar({ loading, active, submit, clear, image }: {
  loading: boolean; active: boolean; submit: (query: string) => void; clear: () => void; image?: (file: File) => void
}) {
  const [query, setQuery] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)
  const [fileError, setFileError] = useState('')
  const choose = (files: FileList | File[]) => {
    if (files.length !== 1 || !files[0].type.startsWith('image/')) { setFileError('请一次选择一张图片找相似'); return }
    if (files[0].size > 50 * 1024 * 1024) { setFileError('图片超过50MB'); return }
    setFileError(''); image?.(files[0])
  }
  return <form onDragOver={e => { if (image) { e.preventDefault(); e.stopPropagation() } }}
    onDrop={e => { if (image) { e.preventDefault(); e.stopPropagation(); choose(e.dataTransfer.files) } }} onSubmit={e => { e.preventDefault(); if (!loading && query.trim()) submit(query) }}>
    <label>搜索收藏<input value={query} onChange={e => { setQuery(e.target.value); if (!e.target.value) clear() }} /></label>
    <button type="submit" disabled={loading || !query.trim()}>搜索</button>
    {image && <><button type="button" disabled={loading} onClick={() => fileInput.current?.click()}>选择图片找相似</button>
      <input ref={fileInput} aria-label="选择图片找相似" type="file" accept="image/*" hidden onChange={e => {
        if (e.target.files?.length) choose(e.target.files); e.target.value = ''
      }} /><span className="muted">也可将一张图片拖进搜索框（不导入图库）</span></>}
    {fileError && <p role="alert">{fileError}</p>}
    {active && <button type="button" onClick={() => { setQuery(''); clear() }}>返回全部收藏</button>}
  </form>
}
export function ImagePreview({ image, id, onOpen }: { image?: ImageEntity; id: number; onOpen: (id: number) => void }) {
  return <button type="button" className="workspace-preview" draggable
    onDragStart={event => startImageDrag(event, id)} onClick={() => onOpen(id)} aria-label={`查看图片 ${id}`}>
    {image ? <img src={thumbnailSrc(image.url)} alt={image.original_name ?? `图片 ${id}`} loading="lazy" /> : <span>图片 #{id}（详情待加载）</span>}
  </button>
}
export function SearchResults({ ids, imagesById, state, onOpen }: {
  ids: number[]; imagesById: Record<number, ImageEntity>; state: RequestState; onOpen: (id: number) => void
}) {
  if (state.status !== 'ok') return null
  return <div>
    {ids.length === 0 && <p>没有搜索结果，试试其他描述。</p>}
    <ol className="workspace-cards" aria-label="搜索结果">
      {ids.map(id => <li key={id} data-image-id={id}><ImagePreview image={imagesById[id]} id={id} onOpen={onOpen} /></li>)}
    </ol>
  </div>
}

import { useState } from 'react'
import type { Project } from '../projects/contract'
import type { SimilarDimension } from '../search/similar'
import { pinVibeImages } from './api'
import { PurposeSelect } from './PurposeSelect'

const labels: Record<SimilarDimension, string> = {
  combined: '综合', palette: '配色', layout: '排版', subject: '主体', style: '风格（试验）',
}

export function VibeSimilarityControls({ imageId, mode, available, resultIds, loading,
  projects, activeProjectId, onSelect, onPinned }: {
  imageId?: number; mode: SimilarDimension; available?: SimilarDimension[]; resultIds: number[]; loading: boolean
  projects: Project[]; activeProjectId: number | null; onSelect: (dimension: SimilarDimension) => void
  onPinned: (projectId: number) => void
}) {
  const [destination, setDestination] = useState<number | null>(null)
  const [purpose, setPurpose] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const projectId = destination ?? activeProjectId
  const pin = async () => {
    if (!projectId || busy || !purpose || !resultIds.length) return
    setBusy(true)
    setMessage('')
    try {
      const result = await pinVibeImages(projectId, resultIds, purpose)
      onPinned(projectId)
      setMessage(result.pinned ? '已钉入 ' + result.pinned + ' 张' : '这批图片已在项目中')
    } catch {
      setMessage('批量钉选失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  return <div>
    {imageId !== undefined && <div>
      <label>找相似维度{' '}
        <select value={mode} disabled={loading || busy}
          onChange={event => { setMessage(''); onSelect(event.currentTarget.value as SimilarDimension) }}>
          {(Object.keys(labels) as SimilarDimension[]).map(value =>
            <option key={value} value={value} disabled={value !== 'combined' && !available?.includes(value)}>
              {labels[value]}
            </option>)}
        </select>
      </label>
      {available?.length === 1 && <small> 这张还没分析</small>}
      {available && available.length > 1 && available.length < 5 && <small> 部分维度缺少向量</small>}
    </div>}
    {!!resultIds.length && <div>
      <label>目标项目{' '}
        <select value={projectId ?? ''} disabled={busy}
          onChange={event => { setDestination(Number(event.currentTarget.value) || null); setMessage('') }}>
          <option value="" disabled>{projects.length ? '请选择' : '请先创建项目'}</option>
          {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select>
      </label>{' '}
      <PurposeSelect value={purpose} onChange={setPurpose} disabled={busy} />{' '}
      <button type="button" disabled={!projectId || !purpose || busy} onClick={() => void pin()}>
        {busy ? '正在钉选…' : '全部钉进项目'}
      </button>
      {message && <span role="status"> {message}</span>}
    </div>}
  </div>
}

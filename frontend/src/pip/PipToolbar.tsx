import { Hand, ImagePlus, LayoutGrid, Maximize, MousePointer2, StickyNote } from 'lucide'
import { Icon } from '../lab/Icon'
import { useState } from 'react'
import type { ImageItem } from '../api'

export type PipProjectOption = { id: number; name: string }
export type PipPanel = 'project' | 'add'
export type PipTool = 'select' | 'hand'

interface PipToolbarProps {
  saveState: 'idle' | 'saving' | 'saved' | 'error' | 'conflict'
  projectId: number | null
  projectName?: string | null
  projects: readonly PipProjectOption[]
  projectImages: readonly ImageItem[]
  libraryImages: readonly ImageItem[]
  dirty: boolean
  resetArmed: boolean
  boardOpacity: number
  scale: number
  tool: PipTool
  panel: PipPanel | null
  onPanel: (panel: PipPanel | null) => void
  onTool: (tool: PipTool) => void
  onProject: (projectId: number) => void
  onAddImage: (image: ImageItem) => void
  onZoom: (direction: -1 | 1) => void
  onSave: () => void
  onReload: () => void
  onReset: () => void
  onAddNote: () => void
  onArrange?: () => void
  onFit?: () => void
  onAllOpacity: (opacity: number) => void
  onBoardOpacity: (opacity: number) => void
}

function ImagePicker({ projectId, projectImages, libraryImages, onAddImage }: Pick<PipToolbarProps,
  'projectId' | 'projectImages' | 'libraryImages' | 'onAddImage'>) {
  const [source, setSource] = useState(projectId === null ? 'library' : 'project')
  const images = source === 'project' ? projectImages : libraryImages
  return (
    <div className="pip-popover pip-picker m-frost" aria-label="加图">
      <div className="pip-picker-sources" role="group" aria-label="图片来源">
        <button type="button" className="pip-text-btn" aria-pressed={source === 'project'}
          disabled={projectId === null} onClick={() => setSource('project')}>当前项目</button>
        <button type="button" className="pip-text-btn" aria-pressed={source === 'library'}
          onClick={() => setSource('library')}>素材库</button>
      </div>
      <div className="pip-picker-images">
        {images.map(image => {
          const label = image.original_name || `图片 ${image.id}`
          return (
            <button type="button" key={image.id} className="pip-picker-image" title={label}
              onClick={() => onAddImage(image)}>
              <img src={image.url} alt={label} loading="lazy" />
            </button>
          )
        })}
        {images.length === 0 && <p>没有图片</p>}
      </div>
    </div>
  )
}

export default function PipToolbar({
  saveState, projectId, projectName, projects, projectImages, libraryImages, dirty, resetArmed,
  boardOpacity, scale, tool, panel, onPanel, onTool, onProject, onAddImage, onZoom,
  onSave, onReload, onReset, onAddNote, onArrange, onFit, onAllOpacity, onBoardOpacity,
}: PipToolbarProps) {
  const unsaved = saveState === 'error' || saveState === 'conflict'
  const name = projectId === null ? '全部收藏' : (projectName?.trim() || '项目')
  const toggle = (next: PipPanel) => onPanel(panel === next ? null : next)

  return (
    <div className="pip-chrome">
      <div className="pip-project">
        <button type="button" className="pip-text-btn pip-project-trigger pip-popup-trigger"
          aria-expanded={panel === 'project'} onClick={() => toggle('project')}>
          <span className="pip-project-name" data-testid="pip-project-name">{name}</span>
          <span aria-hidden="true">▾</span>
        </button>
        <span className="pip-status" data-testid="pip-status" data-state={saveState}>{unsaved ? '未保存' : ''}</span>
        {panel === 'project' && (
          <div className="pip-popover pip-project-panel m-frost" aria-label="项目列表和布局操作">
            {projects.length > 0 && <span className="pip-group-label">切换项目</span>}
            {projects.map(project => (
              <button type="button" key={project.id} className="pip-text-btn" aria-current={projectId === project.id}
                onClick={() => projectId === project.id ? onPanel(null) : onProject(project.id)}>{project.name}</button>
            ))}
            {projects.length > 0 && <hr />}
            <span className="pip-group-label">布局</span>
            <button type="button" className="pip-text-btn" onClick={onSave}
              disabled={!dirty || saveState === 'saving' || projectId === null}>保存布局</button>
            <button type="button" className="pip-text-btn" onClick={onReload} disabled={projectId === null}>重新加载</button>
            <button type="button" className={'pip-text-btn' + (resetArmed ? ' pip-armed' : '')} onClick={onReset}>
              {resetArmed ? '确认重置' : '重置布局'}
            </button>
            <hr />
            <span className="pip-group-label">透明度</span>
            <label className="pip-field">全部不透明度
              <input type="range" min={10} max={100} step={5} defaultValue={100}
                onChange={event => onAllOpacity(Number(event.target.value) / 100)} />
            </label>
            <label className="pip-field" title="画板透明度">画板透明度
              <input type="range" min={30} max={100} step={5} value={Math.round(boardOpacity * 100)}
                onChange={event => onBoardOpacity(Number(event.target.value) / 100)} />
            </label>
          </div>
        )}
      </div>

      {/* 8h：每个图标下面配两个字的说明，悬停还有带快捷键的提示（title + data-tip）；按钮至少 44px 见方 */}
      <div className="pip-tools m-frost" role="toolbar" aria-label="画板工具">
        <button type="button" className="pip-text-btn pip-tool" aria-pressed={tool === 'select'}
          aria-label="选择" title="选择：点选、拖动图片（V）" data-tip="选择 · V" onClick={() => onTool('select')}><Icon icon={MousePointer2} /><span className="pip-tool-label">选择</span></button>
        <button type="button" className="pip-text-btn pip-tool" aria-pressed={tool === 'hand'}
          aria-label="抓手" title="抓手：拖动整块画板（H，或按住空格）" data-tip="抓手 · H / 空格" onClick={() => onTool('hand')}><Icon icon={Hand} /><span className="pip-tool-label">抓手</span></button>
        <button type="button" className="pip-text-btn pip-tool pip-popup-trigger" aria-expanded={panel === 'add'}
          aria-label="加图" title="加图：从项目或素材库挑图放上来" data-tip="加图" onClick={() => toggle('add')}><Icon icon={ImagePlus} /><span className="pip-tool-label">加图</span></button>
        <button type="button" className="pip-text-btn pip-tool" aria-label="便签" title="便签：在画板上写一段字" data-tip="便签" onClick={onAddNote}><Icon icon={StickyNote} /><span className="pip-tool-label">便签</span></button>
        {(onArrange || onFit) && <i className="pip-tools-sep" aria-hidden="true" />}
        {onArrange && <button type="button" className="pip-text-btn pip-tool" aria-label="排整齐" title="排整齐：把图片按网格排好" data-tip="排整齐" onClick={onArrange}><Icon icon={LayoutGrid} /><span className="pip-tool-label">排整齐</span></button>}
        {onFit && <button type="button" className="pip-text-btn pip-tool" aria-label="看全部" title="看全部：缩放到所有图片都在窗口里（Shift+1）" data-tip="看全部 · Shift+1" onClick={onFit}><Icon icon={Maximize} /><span className="pip-tool-label">看全部</span></button>}
        {panel === 'add' && <ImagePicker projectId={projectId} projectImages={projectImages}
          libraryImages={libraryImages} onAddImage={onAddImage} />}
      </div>

      <div className="pip-zoom m-frost">
        <div className="pip-zoom-controls" role="group" aria-label="缩放比例">
          <button type="button" className="pip-text-btn" aria-label="缩小" title="缩小（滚轮向下）" onClick={() => onZoom(-1)}>−</button>
          <span className="pip-zoom-value">{Math.round(scale * 100)}%</span>
          <button type="button" className="pip-text-btn" aria-label="放大" title="放大（滚轮向上）" onClick={() => onZoom(1)}>+</button>
        </div>
      </div>
    </div>
  )
}

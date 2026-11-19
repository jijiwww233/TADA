import { Contrast, Crop, Ellipsis, FlipHorizontal2 } from 'lucide'
import { Icon } from '../lab/Icon'
import { useEffect, useRef } from 'react'
import type { PipSelection } from '../PipCanvas'
import type { PipItem, PipNote } from './scene'

export interface PipSelectionToolsProps {
  selection: PipSelection
  item?: PipItem
  note?: PipNote
  cropMode: boolean
  noteEditToken: number
  onToggleFlag: (flag: 'flip_x' | 'grayscale') => void
  onToggleCrop: () => void
  onMenu: (button: HTMLButtonElement) => void
  onDelete: () => void
  onNoteText: (text: string) => void
  onNoteColor: (color: string) => void
  onNoteSize: (size: number) => void
}

/** Only shown while something is selected; floats next to the selection box. */
export default function PipSelectionTools({
  selection, item, note, cropMode, noteEditToken, onToggleFlag, onToggleCrop, onMenu, onDelete, onNoteText, onNoteColor, onNoteSize,
}: PipSelectionToolsProps) {
  const noteTextareaRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (!noteEditToken) return
    noteTextareaRef.current?.focus()
  }, [noteEditToken])

  if (selection.kind === 'note' && note) {
    return (
      <div className="pip-sel-tools pip-note-editor m-frost" data-testid="pip-note-tools" role="toolbar" aria-label="便签工具">
        <label className="pip-field pip-note-text">内容
          <textarea ref={noteTextareaRef} value={note.text} placeholder="写点什么"
            onChange={event => onNoteText(event.target.value)} />
        </label>
        <div className="pip-note-row">
          <label className="pip-field pip-note-color">颜色
            <input type="color" value={note.color} onChange={event => onNoteColor(event.target.value)} />
          </label>
          <label className="pip-field pip-note-size">字号
            <input type="range" min={8} max={64} step={2} value={note.font_size}
              onChange={event => onNoteSize(Number(event.target.value))} />
          </label>
          <button type="button" className="pip-text-btn pip-danger" title="删除（Delete）" onClick={onDelete}>删除便签</button>
        </div>
      </div>
    )
  }

  if (selection.kind === 'note') return null
  const multi = selection.kind === 'images'
  return (
    <div className="pip-sel-tools m-frost" data-testid={multi ? 'pip-multi-tools' : 'pip-image-tools'}
      role="toolbar" aria-label="选中图片工具">
      {multi && <span className="pip-sel-label">已选 {selection.image_ids.length} 张</span>}
      <button type="button" className="pip-text-btn pip-tool" aria-pressed={item?.grayscale}
        aria-label="灰度" title="灰度：变成黑白看明暗（G）" data-tip="灰度 · G" onClick={() => onToggleFlag('grayscale')}><Icon icon={Contrast} /><span className="pip-tool-label">灰度</span></button>
      <button type="button" className="pip-text-btn pip-tool" aria-pressed={item?.flip_x}
        aria-label="水平翻转" title="水平翻转：左右镜像" data-tip="翻转" onClick={() => onToggleFlag('flip_x')}><Icon icon={FlipHorizontal2} /><span className="pip-tool-label">翻转</span></button>
      {!multi && <button type="button" className="pip-text-btn pip-tool" aria-pressed={cropMode}
        aria-label="裁剪" title="裁剪：只留图片的一部分" data-tip="裁剪" onClick={onToggleCrop}><Icon icon={Crop} /><span className="pip-tool-label">裁剪</span></button>}
      <button type="button" className="pip-text-btn pip-tool pip-popup-trigger" aria-label="图片操作" title="更多：复制、置顶、删除等" data-tip="更多" aria-haspopup="true"
        onClick={event => onMenu(event.currentTarget)}><Icon icon={Ellipsis} /><span className="pip-tool-label">更多</span></button>
    </div>
  )
}

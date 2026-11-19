import { useRef, useState } from 'react'
import type { DragEvent } from 'react'
import type { ImportResult } from '../api'
import { collectDroppedFiles, collectInputFiles } from './collect'
import { useLocalImport } from './useLocalImport'

export interface LocalImportPanelProps {
  onImported: () => void
  /** Test/embedding seam: defaults to the real batch endpoint. */
  importBatch?: (files: File[]) => Promise<ImportResult>
}

const ACCEPT = '.jpg,.jpeg,.png,.webp,.heic,.heif,image/jpeg,image/png,image/webp,image/heic,image/heif'
const EMPTY_HINT = '没有找到支持的图片（jpg/jpeg/png/webp/heic/heif）'

/**
 * Local image/folder import. It is deliberately self-contained (no global CSS, no App changes)
 * so it can be mounted anywhere; DragData collection, batching and cancellation live in the
 * modules next to it.
 */
export default function LocalImportPanel({ onImported, importBatch }: LocalImportPanelProps) {
  const imageInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [collecting, setCollecting] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const { status, progress, error, cancelled, start, cancel, reset } = useLocalImport({ onImported, importBatch })
  const busy = status === 'importing'

  const collectFolderAttr = (node: HTMLInputElement | null) => {
    folderInputRef.current = node
    if (node && !node.hasAttribute('webkitdirectory')) node.setAttribute('webkitdirectory', '')
  }

  const handleInput = (list: FileList | null) => {
    if (!list || busy) return
    const files = collectInputFiles(list).map(item => item.file)
    if (files.length === 0) {
      setLocalError(EMPTY_HINT)
      return
    }
    setLocalError(null)
    void start(files)
  }

  const handleDrop = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    if (busy || collecting) return
    setCollecting(true)
    setLocalError(null)
    try {
      const collected = await collectDroppedFiles(event.dataTransfer)
      if (collected.length === 0) {
        setLocalError(EMPTY_HINT)
        return
      }
      await start(collected.map(item => item.file))
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : '读取拖入内容失败，请重试')
    } finally {
      setCollecting(false)
    }
  }

  const percent = progress.total > 0 ? Math.round((progress.processed / progress.total) * 100) : 0
  const shownError = error ?? localError

  return (
    <section aria-label="本地图片导入" style={{ display: 'flex', flexDirection: 'column', gap: 6, margin: '8px 0' }}>
      <h2 style={{ margin: 0, fontSize: 16 }}>本地图片导入</h2>
      <div
        data-testid="local-import-dropzone"
        onDragOver={(event) => { event.preventDefault(); if (!busy) setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => { void handleDrop(event) }}
        style={{
          border: '2px dashed ' + (dragging ? '#2563eb' : '#cbd5e1'),
          borderRadius: 8,
          padding: 12,
          textAlign: 'center',
          background: dragging ? '#eff6ff' : '#fff',
          color: '#475569',
        }}
      >
        <p style={{ margin: 0 }}>
          {busy
            ? cancelled ? '正在取消（当前批次结束后停止）…' : '导入中…'
            : collecting ? '正在读取文件夹…' : '把图片或整个文件夹拖到这里'}
        </p>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 8, flexWrap: 'wrap' }}>
          <button type="button" onClick={() => imageInputRef.current?.click()} disabled={busy}>选择图片</button>
          <button type="button" onClick={() => folderInputRef.current?.click()} disabled={busy}>选择文件夹</button>
          {busy && <button type="button" onClick={cancel} disabled={cancelled}>取消</button>}
          {!busy && (status === 'done' || status === 'error') && (
            <button type="button" onClick={() => { reset(); setLocalError(null) }}>重新开始</button>
          )}
        </div>
        <input
          ref={imageInputRef}
          aria-label="选择图片文件"
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={(event) => { handleInput(event.target.files); event.target.value = '' }}
        />
        <input
          ref={collectFolderAttr}
          aria-label="选择文件夹"
          type="file"
          multiple
          hidden
          onChange={(event) => { handleInput(event.target.files); event.target.value = '' }}
        />
      </div>

      {status !== 'idle' && (
        <div>
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={progress.total}
            aria-valuenow={progress.processed}
            style={{ height: 8, borderRadius: 4, background: '#e2e8f0', overflow: 'hidden' }}
          >
            <div style={{ height: '100%', width: percent + '%', background: '#2563eb' }} />
          </div>
          <p style={{ margin: '4px 0' }}>
            已处理 {progress.processed}/{progress.total} 张（新增 {progress.added}，重复 {progress.duplicates}，失败 {progress.failed.length}）
          </p>
        </div>
      )}

      {progress.failed.length > 0 && (
        <ul aria-label="失败原因" className="error" style={{ margin: 0, paddingLeft: 18 }}>
          {progress.failed.map((item, index) => (
            <li key={item.name + ':' + index}>{item.name}：{item.reason}</li>
          ))}
        </ul>
      )}

      {status === 'done' && cancelled && <p className="muted" style={{ margin: 0 }}>已取消；已完成的批次不受影响。</p>}
      {status === 'error' && (
        <p className="muted" style={{ margin: 0 }}>已导入的图片会保留；可以重新选择剩余文件重试。</p>
      )}
      {shownError && <p role="alert" className="error" style={{ margin: 0 }}>{shownError}</p>}

      <p className="muted" style={{ margin: 0 }}>已导入的图片不会因为关闭页面而丢失。</p>
      <p className="muted" style={{ margin: 0 }}>HEIC 会自动转成 JPEG 入库（项目 venv 已安装 pillow-heif）；只有运行环境缺少该组件时才会列在失败原因里。</p>
    </section>
  )
}
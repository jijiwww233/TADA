import { lazy, Suspense, useCallback, useEffect, useImperativeHandle, useState, type Ref } from 'react'
import { createPortal } from 'react-dom'
import type { ImageItem } from './api'
import type { PipSceneApi } from './pip/persistence'
import type { PipBoardProject } from './pip/PipBoard'
import type { PipProjectOption } from './pip/PipToolbar'

// Document Picture-in-Picture 置顶小窗（Chromium 116+ 才有）
// https://developer.chrome.com/docs/web-platform/document-picture-in-picture
interface DocumentPip {
  requestWindow(options?: { width?: number; height?: number }): Promise<Window>
  window: Window | null
}

function getDocumentPip() {
  const pip = (window as Window & { documentPictureInPicture?: DocumentPip }).documentPictureInPicture
  return typeof pip?.requestWindow === 'function' ? pip : undefined
}

// With a project open every pin is seeded; the unprojected "全部收藏" view keeps the old six-image seed.
const PIP_COUNT = 6
const MAX_SEED = 200

// Konva + the board are only downloaded when the window is opened for the first time.
const PipBoard = lazy(() => import('./pip/PipBoard'))

// 小窗是一个空白文档，需要把主页面的样式搬过去
function copyStyles(target: Document) {
  for (const node of document.querySelectorAll('style, link[rel="stylesheet"]')) {
    target.head.appendChild(node.cloneNode(true))
  }
}

export interface PipHandle { open: () => Promise<void>; supported: boolean }

export interface PipButtonProps {
  images: ImageItem[]
  projectId?: number | null
  projectName?: string | null
  projects?: readonly PipProjectOption[]
  libraryImages?: readonly ImageItem[]
  loadProject?: (id: number) => Promise<PipBoardProject>
  /** Hide the built-in trigger when a page provides its own button. */
  showTrigger?: boolean
  imagesById?: Readonly<Record<number, ImageItem | undefined>>
  sceneApi?: PipSceneApi
  openUrl?: (url: string, kind: 'image' | 'post') => void
  ref?: Ref<PipHandle>
}

export default function PipButton({
  images, projectId = null, projectName, projects, libraryImages, loadProject, showTrigger = true, imagesById, sceneApi, openUrl, ref,
}: PipButtonProps) {
  const [pipWindow, setPipWindow] = useState<Window | null>(null)
  const [switched, setSwitched] = useState<{ from: number | null; target: PipBoardProject } | null>(null)
  const target = switched?.from === projectId ? switched.target : null
  const activeId = target?.id ?? projectId
  const activeImages = target?.images ?? images

  useEffect(() => () => pipWindow?.close(), [pipWindow])

  const open = useCallback(async () => {
    setSwitched(null)
    const documentPip = getDocumentPip()
    const existing = documentPip?.window ?? pipWindow
    if (existing && !existing.closed) {
      existing.focus()
      return
    }
    // Both APIs must be called directly from the opening gesture.
    const win = documentPip
      ? await documentPip.requestWindow({ width: 720, height: 620 })
      : window.open('about:blank', '_blank', 'popup,width=480,height=640')
    if (!win) throw new Error('参考小窗被浏览器拦截')
    copyStyles(win.document)
    win.document.title = '参考板'
    win.document.body.classList.add('pip-body')
    win.addEventListener('pagehide', () => setPipWindow(current => current === win ? null : current), { once: true })
    setPipWindow(win)
  }, [pipWindow])

  useImperativeHandle(ref, () => ({ open, supported: !!getDocumentPip() || typeof window.open === 'function' }), [open])

  return (
    <>
      {showTrigger && (
        <button type="button" className="pip-button" onClick={open}>
          弹出小窗
        </button>
      )}
      {pipWindow &&
        createPortal(
          <Suspense fallback={null}>
            <PipBoard
              images={activeImages.slice(0, activeId === null ? PIP_COUNT : MAX_SEED)}
              projectImages={activeImages}
              libraryImages={libraryImages}
              projects={projects}
              loadProject={loadProject}
              onProjectChange={next => setSwitched({ from: projectId, target: next })}
              imagesById={imagesById}
              projectId={activeId}
              projectName={target?.name ?? projectName}
              win={pipWindow}
              sceneApi={sceneApi}
              openUrl={openUrl}
            />
          </Suspense>,
          pipWindow.document.body,
        )}
    </>
  )
}
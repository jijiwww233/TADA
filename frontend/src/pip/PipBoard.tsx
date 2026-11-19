import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import type { DragEvent } from 'react'
import type { ImageItem } from '../api'
import { useDemoMode } from '../api/mode'
import type { ImageEntity } from '../search/contract'
import PipCanvas, { type PipSelection } from '../PipCanvas'
import { hasFileDragType, hasImageDragType, readDraggedImageId } from './dragData'
import { flushSceneOnExit } from './exitFlush'
import fileDropStyles from './fileDrop.css?inline'
import { importBoardFiles, isDroppedImage, loadBoardImage } from './fileImport'
import { emptyHistory, recordHistory, redoHistory, undoHistory, type History } from './history'
import { PipSceneConflict, PipSceneError, createPipSceneApi, type PipSceneApi } from './persistence'
import {
  addImageAt, addImages, addNote, compactScene, cropItem, emptyScene, fitSceneView, MAX_ITEMS, nextNoteId, normalizeNotePlaceholders,
  raiseItems, resetItem, noteAABB,
  removeItem, removeNote, resetCrop, setAllOpacity, setView, toggleFlag, updateItem, updateNote,
  type PipItem, type PipNote, type PipScene,
} from './scene'
import { itemAABB, zoomAround, type CropRect, type View } from './geometry'
import { isTextEntryTarget, mapPipShortcut } from './shortcuts'
import PipToolbar, { type PipPanel, type PipProjectOption, type PipTool } from './PipToolbar'
import { PipContextMenu, type PipMenuPoint } from './PipContextMenu'
import PipSelectionTools from './PipSelectionTools'

export type PipBoardProject = PipProjectOption & { images: ImageItem[] }
type ContextMenu = { kind: 'canvas' | 'images'; point: PipMenuPoint }
const NO_PROJECTS: readonly PipProjectOption[] = []
// Leave room for the selection toolbar above the topmost image after fitting.
const FIT_PADDING = 72

export interface PipBoardProps {
  images: ImageItem[]
  imagesById?: Readonly<Record<number, ImageItem | undefined>>
  projectId: number | null
  projectName?: string | null
  projects?: readonly PipProjectOption[]
  libraryImages?: readonly ImageItem[]
  projectImages?: readonly ImageItem[]
  loadProject?: (id: number) => Promise<PipBoardProject>
  onProjectChange?: (project: PipBoardProject) => void
  win: Window
  sceneApi?: PipSceneApi
  openUrl?: (url: string, kind: 'image' | 'post') => void
}

const seedOf = (image: ImageItem) => ({ image_id: image.id, width: image.width ?? 300, height: image.height ?? 300 })

/** Resolve relative /images/… URLs against the workbench origin (PiP documents are about:blank). */
function resolveUrl(url: string): string {
  if (/^[a-z][a-z0-9+.-]*:/i.test(url)) return url
  try {
    return new URL(url, location.href).href
  } catch {
    return url
  }
}

const errorMessage = (error: unknown) => error instanceof PipSceneError || error instanceof Error
  ? error.message : '布局操作失败，请重试'

export default function PipBoard({
  images, imagesById, projectId, projectName, projects = NO_PROJECTS, libraryImages = images, projectImages = images,
  loadProject, onProjectChange, win, sceneApi, openUrl,
}: PipBoardProps) {
  const [demo] = useDemoMode()
  const [extraImages, setExtraImages] = useState<Record<number, ImageItem>>({})
  const requestedImages = useRef(new Set<number>())
  const lookup = useMemo<Readonly<Record<number, ImageItem | undefined>>>(
    () => ({
      ...(demo ? {} : extraImages),
      ...Object.fromEntries(libraryImages.map(image => [image.id, image])),
      ...Object.fromEntries(projectImages.map(image => [image.id, image])),
      ...imagesById,
    }),
    [demo, extraImages, projectImages, imagesById, libraryImages],
  )
  const pickerImages = useMemo(() => demo ? libraryImages : [
    ...libraryImages,
    ...Object.values(extraImages).filter(image => !libraryImages.some(item => item.id === image.id)),
  ], [demo, extraImages, libraryImages])
  const seedInputs = useMemo(() => images.map(seedOf), [images])
  const seedRef = useRef(seedInputs)
  useEffect(() => { seedRef.current = seedInputs }, [seedInputs])
  const api = useMemo(() => sceneApi ?? createPipSceneApi(), [sceneApi])
  const apiRef = useRef(api)
  useEffect(() => { apiRef.current = api }, [api])

  const [scene, setScene] = useState<PipScene>(emptyScene)
  const sceneRef = useRef(scene)
  const [status, setStatus] = useState<'loading' | 'ready'>('loading')
  const statusRef = useRef(status)
  const [, setRevision] = useState(0)
  const revisionRef = useRef(0)
  const projectIdRef = useRef(projectId)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error' | 'conflict'>('idle')
  const [conflict, setConflict] = useState<{ revision: number; scene: PipScene | null } | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [fileDrag, setFileDrag] = useState(false)
  const fileDragDepth = useRef(0)
  const [importingFiles, setImportingFiles] = useState(false)
  const fileImport = useRef<symbol | null>(null)
  const [selection, setSelection] = useState<PipSelection | null>(null)
  const [cropMode, setCropMode] = useState(false)
  const [tool, setTool] = useState<PipTool>('select')
  const [panel, setPanel] = useState<PipPanel | null>(null)
  const [contextMenu, setContextMenu] = useState<ContextMenu | null>(null)
  const projectSwitchRef = useRef(false)
  const [viewport, setViewport] = useState({ width: Math.max(1, win.innerWidth), height: Math.max(1, win.innerHeight) })
  const [dirty, setDirtyState] = useState(false)
  const dirtyRef = useRef(false)
  // Shared by loadScene and saveNow: increments on every project switch / reload so a late save
  // response can never write its revision or scene into the next project.
  const generationRef = useRef(0)
  const saveRunRef = useRef<{ generation: number; promise: Promise<void> } | null>(null)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const exitSentRef = useRef(false)
  const historyRef = useRef<History<PipScene>>(emptyHistory())
  const lastMutationRef = useRef<{ key: string; at: number } | null>(null)
  const removedRef = useRef(new Set<number>())
  const loadTicket = useRef(0)
  const [reloadKey, setReloadKey] = useState(0)
  const [resetArmed, setResetArmed] = useState(false)
  const [noteEditToken, setNoteEditToken] = useState(0)
  const [boardOpacity, setBoardOpacity] = useState(1)
  const opacityKey = 'wominmin:pip-board-opacity:' + (projectId ?? 'all')
  useEffect(() => {
    try {
      const saved = localStorage.getItem(opacityKey)
      const value = saved === null ? 1 : Number(saved)
      setBoardOpacity(Number.isFinite(value) ? Math.min(1, Math.max(0.3, value)) : 1)
    } catch {
      setBoardOpacity(1)
    }
  }, [opacityKey])
  const changeBoardOpacity = useCallback((value: number) => {
    setBoardOpacity(value)
    try { localStorage.setItem(opacityKey, String(value)) } catch { /* PiP documents may have no storage */ }
  }, [opacityKey])

  // Dev-only diagnostics for browser/Cdp verification; dead-code-eliminated in production builds.
  useEffect(() => {
    if (!import.meta.env.DEV) return
    const debug = win as Window & { __PIP_DEBUG?: Record<string, unknown> }
    debug.__PIP_DEBUG = { ...debug.__PIP_DEBUG, mountedAt: performance.now(), projectId, items: sceneRef.current.items.length }
  }, [win, projectId])

  useEffect(() => {
    if (!import.meta.env.DEV) return
    const debug = win as Window & { __PIP_DEBUG?: Record<string, unknown> }
    debug.__PIP_DEBUG = { ...debug.__PIP_DEBUG, selection, boardOpacity }
  }, [win, selection, boardOpacity])

  useEffect(() => {
    if (!import.meta.env.DEV) return
    const debug = win as Window & { __PIP_DEBUG?: Record<string, unknown> }
    debug.__PIP_DEBUG = { ...debug.__PIP_DEBUG, scene }
  }, [win, scene])

  const setDirty = useCallback((value: boolean) => {
    dirtyRef.current = value
    setDirtyState(value)
  }, [])

  const clearHistory = useCallback(() => {
    historyRef.current = emptyHistory()
    lastMutationRef.current = null
  }, [])

  const mutate = useCallback((update: (previous: PipScene) => PipScene,
    options: { history?: boolean; coalesceKey?: string } = {}) => {
    const next = update(sceneRef.current)
    if (next === sceneRef.current) return
    if (options.history !== false) {
      const key = options.coalesceKey
      const last = lastMutationRef.current
      // A slider drag emits many changes within one gesture; keep a single undo step per burst.
      const coalesce = key !== undefined && last?.key === key
        && Date.now() - last.at < 600 && historyRef.current.future.length === 0
      if (!coalesce) {
        historyRef.current = recordHistory(historyRef.current, sceneRef.current)
      }
      lastMutationRef.current = key !== undefined ? { key, at: Date.now() } : null
    }
    sceneRef.current = next
    dirtyRef.current = true
    // A fresh edit re-arms the exit flush after a previous pagehide (bfcache restore).
    exitSentRef.current = false
    setScene(next)
    setDirtyState(true)
  }, [])

  /** Dynamic z-order: raising is presentation state, so it never enters the undo stack. */
  const raiseImages = useCallback((imageIds: readonly number[]) => {
    if (imageIds.length === 0) return
    mutate(previous => raiseItems(previous, imageIds), { history: false })
  }, [mutate])

  const applyHistory = useCallback((direction: 'undo' | 'redo') => {
    const current = sceneRef.current
    const step = direction === 'undo'
      ? undoHistory(historyRef.current, current)
      : redoHistory(historyRef.current, current)
    if (!step) return
    historyRef.current = step.history
    lastMutationRef.current = null
    // Only content edits are undoable; keep the current camera so Ctrl+Z never jumps the view.
    const next = setView(step.value, current.view)
    sceneRef.current = next
    dirtyRef.current = true
    exitSentRef.current = false
    setScene(next)
    setDirtyState(true)
    for (const item of next.items) removedRef.current.delete(item.image_id)
    setSelection(previous => {
      if (!previous) return previous
      if (previous.kind === 'image') return next.items.some(item => item.image_id === previous.image_id) ? previous : null
      if (previous.kind === 'note') return next.notes.some(note => note.id === previous.id) ? previous : null
      if (previous.kind === 'images') {
        const ids = previous.image_ids.filter(id => next.items.some(item => item.image_id === id))
        return ids.length > 0 ? { kind: 'images', image_ids: ids } : null
      }
      return previous
    })
  }, [])

  const loadScene = useCallback(async (id: number | null) => {
    generationRef.current += 1
    fileImport.current = null
    setImportingFiles(false)
    requestedImages.current.clear()
    const ticket = ++loadTicket.current
    projectIdRef.current = id
    statusRef.current = 'loading'
    setStatus('loading')
    setPanel(null)
    setContextMenu(null)
    setConflict(null)
    setMessage(null)
    setSaveState('idle')
    setSelection(null)
    setCropMode(false)
    setResetArmed(false)
    removedRef.current.clear()
    dirtyRef.current = false
    setDirtyState(false)
    exitSentRef.current = false
    clearHistory()
    const tidy = (value: PipScene) => {
      if (value.items.length === 0) return value
      // compactScene reserves gap*2 around each box: 4 => the requested 8px visual spacing.
      const packed = compactScene(value, 4)
      return { ...packed, view: fitSceneView(packed, { width: Math.max(1, win.innerWidth), height: Math.max(1, win.innerHeight) }, FIT_PADDING) }
    }
    const initial = tidy(addImages(emptyScene(), seedRef.current))
    if (id === null) {
      if (ticket !== loadTicket.current) return
      sceneRef.current = initial
      setScene(initial)
      revisionRef.current = 0
      setRevision(0)
      statusRef.current = 'ready'
      setStatus('ready')
      return
    }
    try {
      const snapshot = await apiRef.current.get(id)
      if (ticket !== loadTicket.current) return
      const stored = snapshot.scene ?? emptyScene()
      const normalized = normalizeNotePlaceholders(stored)
      const next = snapshot.scene ? addImages(normalized, seedRef.current) : tidy(addImages(normalized, seedRef.current))
      sceneRef.current = next
      setScene(next)
      revisionRef.current = snapshot.revision
      setRevision(snapshot.revision)
      statusRef.current = 'ready'
      setStatus('ready')
      if ((!snapshot.scene && next.items.length > 0) || normalized !== stored) setDirty(true)
    } catch (error) {
      if (ticket !== loadTicket.current) return
      sceneRef.current = initial
      setScene(initial)
      statusRef.current = 'ready'
      setStatus('ready')
      setMessage(errorMessage(error))
      setSaveState('error')
    }
  }, [setDirty, clearHistory, win])

  useEffect(() => {
    // Deferred so the loading state change is not a synchronous setState inside the effect.
    const timer = setTimeout(() => { void loadScene(projectId) }, 0)
    return () => clearTimeout(timer)
  }, [loadScene, projectId, reloadKey])

  // Auto-synced images (project pins or the newest library images) join an existing board.
  useEffect(() => {
    if (statusRef.current !== 'ready' || projectIdRef.current !== projectId) return
    const fresh = seedRef.current.filter(input => !removedRef.current.has(input.image_id)
      && !sceneRef.current.items.some(item => item.image_id === input.image_id))
    if (fresh.length > 0) mutate(previous => addImages(previous, fresh), { history: false })
  }, [seedInputs, mutate, status, projectId])

  // A saved project scene can refer to images outside the current library or project list.
  useEffect(() => {
    if (demo || status !== 'ready') return
    for (const { image_id } of scene.items) {
      if (lookup[image_id] || requestedImages.current.has(image_id)) continue
      requestedImages.current.add(image_id)
      void loadBoardImage(image_id).then(image => {
        setExtraImages(previous => ({ ...previous, [image.id]: image }))
      }, () => { /* Missing images keep their place in the saved layout. */ })
    }
  }, [demo, lookup, scene.items, status])

  useEffect(() => () => { fileImport.current = null }, [])

  const saveNow = useCallback(() => {
    const generation = generationRef.current
    const id = projectIdRef.current
    if (id === null || statusRef.current !== 'ready') return
    if (!dirtyRef.current) return
    // Only one save loop per generation: a save from an older project may still be in flight,
    // but it must neither block nor be mistaken for the current project's save.
    if (saveRunRef.current?.generation === generation) return saveRunRef.current.promise
    const stale = () => generation !== generationRef.current || id !== projectIdRef.current
    const save = async () => {
      try {
        while (dirtyRef.current) {
          if (stale()) return
          const snapshot = sceneRef.current
          setSaveState('saving')
          const result = await apiRef.current.save(id, revisionRef.current, snapshot)
          // After every await: a project switch/reload invalidates this response entirely.
          if (stale()) return
          revisionRef.current = result.revision
          setRevision(result.revision)
          if (sceneRef.current === snapshot) setDirty(false)
        }
        if (stale()) return
        setSaveState('saved')
      } catch (error) {
        if (stale()) return
        if (error instanceof PipSceneConflict) {
          setConflict({ revision: error.currentRevision, scene: error.currentScene })
          setSaveState('conflict')
        } else {
          setMessage(errorMessage(error))
          setSaveState('error')
        }
      }
    }
    const promise = save().finally(() => {
      if (saveRunRef.current?.promise === promise) saveRunRef.current = null
    })
    saveRunRef.current = { generation, promise }
    return promise
  }, [setDirty])

  useEffect(() => {
    if (!dirty || status !== 'ready' || projectId === null) return
    const timer = setTimeout(() => {
      if (saveTimerRef.current === timer) saveTimerRef.current = null
      void saveNow()
    }, 700)
    saveTimerRef.current = timer
    return () => {
      clearTimeout(timer)
      if (saveTimerRef.current === timer) saveTimerRef.current = null
    }
  }, [scene, dirty, status, projectId, saveNow])

  /**
   * Last-chance fire-and-forget save for real unloads (close, navigation, bfcache). The response is
   * unobservable, so revision bookkeeping stays with the regular save path; visibilitychange:hidden
   * is deliberately NOT routed here (a hidden window can still receive the response).
   * Skip while a normal save is already in flight or when nothing changed: a second concurrent PUT
   * would race on the same revision and turn into a spurious 409.
   */
  const flushForExit = useCallback(() => {
    const id = projectIdRef.current
    if (id === null || !dirtyRef.current || exitSentRef.current) return
    if (saveRunRef.current?.generation === generationRef.current) return
    if (saveTimerRef.current !== null) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
    // No UI and no revision bookkeeping on the exit path: 409 just means we do not overwrite.
    if (flushSceneOnExit({ projectId: id, revision: revisionRef.current, scene: sceneRef.current }).sent) {
      exitSentRef.current = true
    }
  }, [])

  useEffect(() => {
    const doc = win.document
    const onPageHide = () => { flushForExit() }
    const onBeforeUnload = () => { flushForExit() }
    const onVisibilityChange = () => {
      if (doc.visibilityState === 'hidden') {
        // Hidden but still alive: use the response-tracking save so the learned revision is kept
        // and coming back does not self-conflict with a stale revision.
        if (saveTimerRef.current !== null) {
          clearTimeout(saveTimerRef.current)
          saveTimerRef.current = null
        }
        if (dirtyRef.current) void saveNow()
      } else {
        // Coming back visible does not optimistically mark anything as saved; it only re-arms
        // the last-chance keepalive for a later real unload.
        exitSentRef.current = false
      }
    }
    win.addEventListener('pagehide', onPageHide)
    win.addEventListener('beforeunload', onBeforeUnload)
    doc.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      win.removeEventListener('pagehide', onPageHide)
      win.removeEventListener('beforeunload', onBeforeUnload)
      doc.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [win, flushForExit, saveNow])

  const open = useCallback((url: string, kind: 'image' | 'post') => {
    if (openUrl) { openUrl(kind === 'post' ? url : resolveUrl(url), kind); return }
    try {
      win.open(resolveUrl(url), '_blank', 'noopener,noreferrer')
    } catch {
      setMessage('浏览器阻止了打开新窗口')
    }
  }, [openUrl, win])

  const handleOpenOriginal = useCallback((imageId: number) => {
    const image = lookup[imageId]
    if (!image) { setMessage('找不到这张图'); return }
    open(image.url, 'image')
  }, [lookup, open])

  const handleOpenPost = useCallback((imageId: number, shift: boolean) => {
    const image = lookup[imageId]
    if (!image) { setMessage('找不到这张图'); return }
    if (!shift) { open(image.url, 'image'); return }
    const source = (image as ImageEntity).sources?.[0]
    if (!source) { setMessage('这张图没有原帖链接'); return }
    if (source.platform === 'xhs') {
      // XHS CDN / detail pages stay behind the existing cooldown-aware source endpoint.
      open('/api/posts/' + source.post_id + '/source', 'post')
      return
    }
    if (source.canonical_url && /^https?:\/\//i.test(source.canonical_url)) {
      open(source.canonical_url, 'post')
      return
    }
    setMessage('这张图没有可打开的原帖链接')
  }, [lookup, open])

  const addImage = useCallback((image: ImageItem, point: PipMenuPoint) => {
    setMessage(null)
    setCropMode(false)
    if (sceneRef.current.items.some(item => item.image_id === image.id)) {
      raiseImages([image.id])
    } else {
      const { x, y, scale } = sceneRef.current.view
      mutate(previous => addImageAt(previous, seedOf(image), (point.x - x) / scale, (point.y - y) / scale))
      if (!sceneRef.current.items.some(item => item.image_id === image.id)) return
    }
    removedRef.current.delete(image.id)
    setSelection({ kind: 'image', image_id: image.id })
  }, [mutate, raiseImages])

  const dropFiles = async (incoming: File[], point: PipMenuPoint) => {
    if (demo) { setMessage('切换到真实素材库后即可拖图'); return }
    if (statusRef.current !== 'ready' || projectSwitchRef.current) { setMessage('画布还在加载，请稍后再拖'); return }
    if (fileImport.current) return
    const files = incoming.filter(isDroppedImage)
    if (files.length === 0) { setMessage('请拖入图片文件'); return }
    const available = MAX_ITEMS - sceneRef.current.items.length
    if (available === 0) { setMessage('画布已满，请先移除几张图'); return }

    const { x, y, scale } = sceneRef.current.view
    const worldPoint = { x: (point.x - x) / scale, y: (point.y - y) / scale }
    const token = Symbol()
    fileImport.current = token
    const active = () => fileImport.current === token && !win.closed
    setMessage(null)
    setImportingFiles(true)
    try {
      const { ids, error } = await importBoardFiles(files.slice(0, available), active)
      if (!active()) return
      const images = (await Promise.all(ids.map(id => lookup[id]
        ?? loadBoardImage(id).catch(() => null)))).filter(image => image !== null)
      if (!active()) return

      setExtraImages(previous => ({ ...previous, ...Object.fromEntries(images.map(image => [image.id, image])) }))
      const placed: number[] = []
      mutate(previous => {
        let next = previous
        for (const image of images) {
          const offset = placed.length * 24 / scale
          next = addImageAt(next, seedOf(image), worldPoint.x + offset, worldPoint.y + offset)
          if (next.items.some(item => item.image_id === image.id)) {
            placed.push(image.id)
            removedRef.current.delete(image.id)
          }
        }
        return next
      })
      if (placed.length > 0) {
        setCropMode(false)
        setSelection(placed.length === 1
          ? { kind: 'image', image_id: placed[0] }
          : { kind: 'images', image_ids: placed })
      }
      const full = placed.length < images.length || files.length > available
      setMessage(error ?? (full ? '画布已满，部分图片未放入'
        : images.length < ids.length ? '有图片暂时无法显示，请重新拖入' : null))
    } finally {
      if (fileImport.current === token) {
        fileImport.current = null
        setImportingFiles(false)
      }
    }
  }

  const acceptDrag = (event: DragEvent<HTMLDivElement>, entering = false) => {
    const files = hasFileDragType(event.dataTransfer)
    if (!files && !hasImageDragType(event.dataTransfer)) return
    event.stopPropagation()
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
    if (!files) return
    if (entering) fileDragDepth.current += 1
    setFileDrag(true)
  }

  const leaveDrag = (event: DragEvent<HTMLDivElement>) => {
    if (!hasFileDragType(event.dataTransfer) && !fileDragDepth.current) return
    event.stopPropagation()
    fileDragDepth.current = Math.max(0, fileDragDepth.current - 1)
    if (fileDragDepth.current === 0) setFileDrag(false)
  }

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    const files = hasFileDragType(event.dataTransfer)
    if (!files && !hasImageDragType(event.dataTransfer)) return
    // React portal events would otherwise reach the main window's library drop target.
    event.stopPropagation()
    fileDragDepth.current = 0
    setFileDrag(false)
    const rect = event.currentTarget.querySelector('.pip-canvas-host')?.getBoundingClientRect()
    const point = {
      x: (Number.isFinite(event.clientX) ? event.clientX : 0) - (rect?.left ?? 0),
      y: (Number.isFinite(event.clientY) ? event.clientY : 0) - (rect?.top ?? 0),
    }
    if (files) { void dropFiles(Array.from(event.dataTransfer.files), point); return }
    const imageId = readDraggedImageId(event.dataTransfer)
    if (imageId === null) return
    const image = lookup[imageId]
    if (!image) { setMessage('只能拖入图库里的图片'); return }
    addImage(image, point)
  }

  const selectedItem = selection?.kind === 'image'
    ? scene.items.find(item => item.image_id === selection.image_id) : undefined
  const selectedImages = selection?.kind === 'image' ? [selection.image_id]
    : selection?.kind === 'images' ? selection.image_ids : []
  const selectedItems = scene.items.filter(item => selectedImages.includes(item.image_id))
  const selectedNote = selection?.kind === 'note'
    ? scene.notes.find(note => note.id === selection.id) : undefined
  const selectionBounds = useMemo(() => {
    if (!selection) return null
    if (selection.kind === 'note') {
      const note = scene.notes.find(entry => entry.id === selection.id)
      return note ? noteAABB(note) : null
    }
    const ids = selection.kind === 'image' ? [selection.image_id] : selection.image_ids
    const boxes = scene.items.filter(item => ids.includes(item.image_id)).map(itemAABB)
    if (boxes.length === 0) return null
    return {
      x: Math.min(...boxes.map(box => box.x)),
      y: Math.min(...boxes.map(box => box.y)),
      width: Math.max(...boxes.map(box => box.x + box.width)) - Math.min(...boxes.map(box => box.x)),
      height: Math.max(...boxes.map(box => box.y + box.height)) - Math.min(...boxes.map(box => box.y)),
    }
  }, [selection, scene.items, scene.notes])

  const updateView = useCallback((view: View) => { mutate(previous => setView(previous, view), { history: false }) }, [mutate])
  const changeItem = useCallback((imageId: number, patch: Partial<Omit<PipItem, 'image_id'>>) => {
    mutate(previous => updateItem(previous, imageId, patch))
  }, [mutate])
  const changeNote = useCallback((noteId: string, patch: Partial<Omit<PipNote, 'id'>>,
    options?: { coalesceKey?: string }) => {
    mutate(previous => updateNote(previous, noteId, patch), options)
  }, [mutate])
  const cropImage = useCallback((imageId: number, rect: CropRect) => {
    mutate(previous => cropItem(previous, imageId, rect))
    setCropMode(false)
    setMessage(null)
  }, [mutate])

  const addNoteAt = (x: number, y: number) => {
    const id = nextNoteId(sceneRef.current.notes)
    mutate(previous => addNote(previous, x, y))
    setSelection({ kind: 'note', id })
  }

  /** Double-clicking a note focuses the toolbar editor (the explicit controls stay usable too). */
  const editNote = useCallback((noteId: string) => {
    if (!sceneRef.current.notes.some(note => note.id === noteId)) return
    setSelection({ kind: 'note', id: noteId })
    setCropMode(false)
    setNoteEditToken(token => token + 1)
  }, [])

  const addNoteAtCenter = () => {
    const view = sceneRef.current.view
    addNoteAt((viewport.width / 2 - view.x) / view.scale, (viewport.height / 2 - view.y) / view.scale)
  }

  const toggleCrop = () => {
    if (cropMode) { setCropMode(false); return }
    if (selection?.kind !== 'image') { setMessage('先选中一张图再裁剪'); return }
    setCropMode(true)
  }

  const removeSelection = useCallback(() => {
    if (selection?.kind === 'image') {
      removedRef.current.add(selection.image_id)
      mutate(previous => removeItem(previous, selection.image_id))
    } else if (selection?.kind === 'note') {
      mutate(previous => removeNote(previous, selection.id))
    } else if (selection?.kind === 'images' && selection.image_ids.length > 0) {
      const ids = selection.image_ids
      for (const id of ids) removedRef.current.add(id)
      mutate(previous => ids.reduce((current, id) => removeItem(current, id), previous))
    } else return
    setSelection(null)
    setCropMode(false)
  }, [selection, mutate])

  const toggleSelectedFlag = useCallback((flag: 'flip_x' | 'grayscale') => {
    if (selection?.kind === 'image') {
      mutate(previous => toggleFlag(previous, selection.image_id, flag))
    } else if (selection?.kind === 'images' && selection.image_ids.length > 0) {
      const ids = selection.image_ids
      mutate(previous => ids.reduce((current, id) => toggleFlag(current, id, flag), previous))
    }
  }, [selection, mutate])

  const selectAll = useCallback(() => {
    const ids = sceneRef.current.items.map(item => item.image_id)
    setSelection(ids.length > 0 ? { kind: 'images', image_ids: ids } : null)
    setCropMode(false)
    setMessage(null)
    raiseImages(ids)
  }, [raiseImages])

  const fitAll = useCallback(() => {
    mutate(previous => setView(previous, fitSceneView(previous, viewport, FIT_PADDING)), { history: false })
  }, [mutate, viewport])

  useEffect(() => {
    const doc = win.document
    const dismiss = (event: PointerEvent) => {
      if ((event.target as HTMLElement | null)?.closest?.('.pip-popover, .pip-popup-trigger')) return
      setPanel(null)
      setContextMenu(null)
      setResetArmed(false)
    }
    doc.addEventListener('pointerdown', dismiss)
    return () => doc.removeEventListener('pointerdown', dismiss)
  }, [win])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setPanel(null)
        setContextMenu(null)
        setResetArmed(false)
        return
      }
      if (isTextEntryTarget(event.target)) return
      const shortcut = mapPipShortcut(event)
      if (!shortcut) return
      event.preventDefault()
      if (shortcut === 'select-tool' || shortcut === 'hand-tool') {
        setTool(shortcut === 'select-tool' ? 'select' : 'hand')
        return
      }
      if (shortcut === 'undo' || shortcut === 'redo') { applyHistory(shortcut); return }
      if (shortcut === 'select-all') { selectAll(); return }
      if (shortcut === 'delete') { setContextMenu(null); removeSelection(); return }
      if (shortcut === 'fit') { fitAll(); return }
      toggleSelectedFlag('grayscale')
    }
    win.addEventListener('keydown', onKeyDown)
    return () => win.removeEventListener('keydown', onKeyDown)
  }, [win, applyHistory, selectAll, removeSelection, fitAll, toggleSelectedFlag])

  const changeSelectedImages = (update: (scene: PipScene, id: number) => PipScene, coalesceKey?: string) => {
    mutate(previous => selectedImages.reduce(update, previous), { coalesceKey })
  }

  const chooseProject = async (id: number) => {
    if (!loadProject || !onProjectChange || projectSwitchRef.current || statusRef.current !== 'ready') return
    if (conflict || fileImport.current) return
    projectSwitchRef.current = true
    const generation = generationRef.current
    try {
      const next = await loadProject(id)
      if (generation !== generationRef.current || win.closed) return
      await saveNow()
      if (generation !== generationRef.current || win.closed) return
      if (projectIdRef.current !== null && dirtyRef.current) return
      setPanel(null)
      onProjectChange(next)
    } catch {
      if (generation === generationRef.current) setMessage('无法打开项目画板')
    } finally {
      projectSwitchRef.current = false
    }
  }

  const openContextMenu = (event: MouseEvent, imageId: number | null) => {
    setPanel(null)
    if (imageId !== null && !selectedImages.includes(imageId)) {
      setSelection({ kind: 'image', image_id: imageId })
      setCropMode(false)
    }
    setContextMenu({
      kind: imageId === null ? 'canvas' : 'images',
      point: { x: event.clientX, y: event.clientY },
    })
  }

  const resetBoard = () => {
    if (!resetArmed) { setResetArmed(true); setMessage('再点一次“确认重置”会清空本项目保存的布局'); return }
    removedRef.current.clear()
    setResetArmed(false)
    clearHistory()
    mutate(() => addImages(emptyScene(), seedRef.current), { history: false })
    setSelection(null)
    setCropMode(false)
    setPanel(null)
    setMessage('布局已重置，稍后自动保存')
  }

  const toggleSelection = useCallback((imageId: number) => {
    setSelection(previous => {
      if (!previous || previous.kind === 'note') return { kind: 'image', image_id: imageId }
      const ids = previous.kind === 'image' ? [previous.image_id] : previous.image_ids
      const next = ids.includes(imageId) ? ids.filter(id => id !== imageId) : [...ids, imageId]
      if (next.length === 0) return null
      return next.length === 1 ? { kind: 'image', image_id: next[0] } : { kind: 'images', image_ids: next }
    })
  }, [])

  const moveItems = useCallback((imageIds: readonly number[], dx: number, dy: number) => {
    if (dx === 0 && dy === 0) return
    const ids = new Set(imageIds)
    mutate(previous => ({
      ...previous,
      items: previous.items.map(item => ids.has(item.image_id) ? { ...item, x: item.x + dx, y: item.y + dy } : item),
    }))
  }, [mutate])

  const bringSelectionFront = useCallback(() => {
    if (!selection) return
    raiseImages(selection.kind === 'image' ? [selection.image_id] : selection.kind === 'images' ? selection.image_ids : [])
  }, [selection, raiseImages])

  /** Move the selection to the bottom of the z-order without changing its internal order. */
  const sendSelectionBack = useCallback(() => {
    if (!selection || selection.kind === 'note') return
    const ids = selection.kind === 'image' ? [selection.image_id] : selection.image_ids
    const set = new Set(ids)
    mutate(previous => ({
      ...previous,
      items: [...previous.items.filter(item => set.has(item.image_id)), ...previous.items.filter(item => !set.has(item.image_id))],
    }))
  }, [selection, mutate])

  const resolveConflict = (useServer: boolean) => {
    const current = conflict
    setConflict(null)
    if (!current) return
    if (useServer) {
      const next = normalizeNotePlaceholders(addImages(current.scene ?? emptyScene(), seedRef.current))
      sceneRef.current = next
      setScene(next)
      revisionRef.current = current.revision
      setRevision(current.revision)
      setDirty(false)
      setSaveState('idle')
      setSelection(null)
      clearHistory()
      setMessage('已加载另一个窗口保存的布局')
    } else {
      revisionRef.current = current.revision
      setRevision(current.revision)
      setDirty(true)
      setSaveState('idle')
      void saveNow()
    }
  }

  const compact = () => mutate(previous => compactScene(previous, 4))
  const runMenuAction = (action: () => void) => { setContextMenu(null); action() }

  return (
    <div className="pip-board"
      style={{ '--pip-alpha': boardOpacity } as CSSProperties}
      onDragEnter={event => acceptDrag(event, true)}
      onDragOver={event => acceptDrag(event)}
      onDragLeave={leaveDrag}
      onDrop={handleDrop}>
      <style>{fileDropStyles}</style>
      <PipCanvas
        scene={scene}
        imagesById={lookup}
        selection={selection}
        cropMode={cropMode}
        handTool={tool === 'hand'}
        win={win}
        onContextMenu={openContextMenu}
        onSelect={value => {
          setSelection(value)
          if (!value) { setCropMode(false); return }
          if (value.kind === 'image') raiseImages([value.image_id])
          else if (value.kind === 'images') raiseImages(value.image_ids)
        }}
        onToggleSelect={toggleSelection}
        onViewChange={updateView}
        onItemChange={changeItem}
        onMoveItems={moveItems}
        onNoteChange={changeNote}
        onEditNote={editNote}
        onCrop={cropImage}
        onAddNote={addNoteAt}
        onOpenOriginal={handleOpenOriginal}
        onOpenPost={handleOpenPost}
        onViewportChange={setViewport}
      />
      <PipToolbar
        saveState={saveState} projectId={projectId} projectName={projectName} dirty={dirty}
        projects={projects} projectImages={projectImages} libraryImages={pickerImages}
        resetArmed={resetArmed} boardOpacity={boardOpacity} scale={scene.view.scale}
        tool={tool} panel={panel}
        onPanel={next => { setContextMenu(null); setPanel(next); if (next !== 'project') setResetArmed(false) }}
        onTool={setTool}
        onProject={id => { void chooseProject(id) }}
        onAddImage={image => {
          addImage(image, { x: viewport.width / 2, y: viewport.height / 2 })
          setPanel(null)
        }}
        onZoom={direction => updateView(zoomAround(sceneRef.current.view,
          { x: viewport.width / 2, y: viewport.height / 2 }, 1.1 ** direction))}
        onSave={() => { void saveNow() }}
        onReload={() => setReloadKey(key => key + 1)}
        onReset={resetBoard}
        onAddNote={addNoteAtCenter}
        onArrange={() => { compact(); fitAll() }}
        onFit={fitAll}
        onAllOpacity={opacity => mutate(previous => setAllOpacity(previous, opacity), { coalesceKey: 'all-opacity' })}
        onBoardOpacity={changeBoardOpacity}
      />
      {selection && selectionBounds && (() => {
        const scale = scene.view.scale
        const rawCenter = (selectionBounds.x + selectionBounds.width / 2) * scale + scene.view.x
        const above = selectionBounds.y * scale + scene.view.y - 36
        const below = (selectionBounds.y + selectionBounds.height) * scale + scene.view.y + 36
        const half = Math.min(selectedNote ? 134 : 84, Math.max(0, viewport.width / 2 - 8))
        const centerX = Math.max(half, Math.min(rawCenter, viewport.width - half))
        const placeBelow = selectedNote
          ? (above < 132 && below + 124 <= viewport.height - 8) || above < 44
          : above < 44 && below + 40 <= viewport.height - 8
        const top = placeBelow ? Math.max(8, below) : selectedNote ? above : Math.max(44, above)
        return (
          <div className={'pip-sel-anchor' + (placeBelow ? ' pip-sel-below' : '')}
            style={{ left: centerX, top }}>
            <PipSelectionTools
              selection={selection}
              item={selectedItem}
              note={selectedNote}
              cropMode={cropMode}
              noteEditToken={noteEditToken}
              onToggleFlag={toggleSelectedFlag}
              onToggleCrop={toggleCrop}
              onMenu={button => {
                const rect = button.getBoundingClientRect()
                setPanel(null)
                setContextMenu({ kind: 'images', point: { x: rect.left, y: rect.bottom } })
              }}
              onDelete={removeSelection}
              onNoteText={text => { if (selectedNote) changeNote(selectedNote.id, { text }, { coalesceKey: 'note-text:' + selectedNote.id }) }}
              onNoteColor={color => { if (selectedNote) changeNote(selectedNote.id, { color }, { coalesceKey: 'note-color:' + selectedNote.id }) }}
              onNoteSize={size => { if (selectedNote) changeNote(selectedNote.id, { font_size: size }, { coalesceKey: 'note-size:' + selectedNote.id }) }}
            />
          </div>
        )
      })()}
      {contextMenu?.kind === 'images' && selectedItems.length > 0 && (
        <PipContextMenu point={contextMenu.point} viewport={viewport} label="图片操作">
          <button type="button" className="pip-text-btn" onClick={() => runMenuAction(bringSelectionFront)}>置顶</button>
          <button type="button" className="pip-text-btn" onClick={() => runMenuAction(sendSelectionBack)}>置底</button>
          <label className="pip-field">不透明度
            <input type="range" min={10} max={100} step={5} value={Math.round(selectedItems[0].opacity * 100)}
              onChange={event => {
                const opacity = Number(event.target.value) / 100
                changeSelectedImages((previous, id) => updateItem(previous, id, { opacity }),
                  'item-opacity:' + selectedImages.join(','))
              }} />
          </label>
          <button type="button" className="pip-text-btn" onClick={() => runMenuAction(() => {
            changeSelectedImages((previous, id) => {
              const image = lookup[id]
              return resetItem(previous, id, image ? { width: image.width, height: image.height } : null)
            })
          })}>重置图片</button>
          <button type="button" className="pip-text-btn" disabled={!selectedItems.some(item => item.crop)}
            onClick={() => runMenuAction(() => changeSelectedImages(resetCrop))}>恢复原图</button>
          <button type="button" className="pip-text-btn pip-danger" title="删除（Delete）"
            onClick={() => runMenuAction(removeSelection)}>删除</button>
        </PipContextMenu>
      )}
      {contextMenu?.kind === 'canvas' && (
        <PipContextMenu point={contextMenu.point} viewport={viewport} label="画板操作">
          <button type="button" className="pip-text-btn" onClick={() => runMenuAction(compact)}>自动排列</button>
          <button type="button" className="pip-text-btn" title="适应画板（Shift+1）"
            onClick={() => runMenuAction(fitAll)}>适应画板</button>
          <button type="button" className="pip-text-btn" onClick={() => runMenuAction(addNoteAtCenter)}>加便签</button>
        </PipContextMenu>
      )}
      {fileDrag && <div className="pip-file-drop" role="status">
        <div>
          {importingFiles ? '正在导入图片…' : demo ? '切换到真实素材库后即可拖图' : '松手放进画布'}
          {!demo && !importingFiles && <small>图片也会存进{import.meta.env.VITE_PUBLIC_DEMO === '1' ? '共用' : ''}素材库</small>}
        </div>
      </div>}
      {importingFiles && !fileDrag && <div className="pip-toast m-frost" role="status">正在导入图片…</div>}
      {message && <div className="pip-toast m-frost" role="status">{message}
        <button type="button" className="pip-text-btn" onClick={() => setMessage(null)}>关闭</button></div>}
      {conflict && <div className="pip-toast pip-conflict m-frost" role="alert">
        <p>布局已在另一个窗口更新，本次修改尚未保存。</p>
        <button type="button" className="pip-text-btn" onClick={() => resolveConflict(true)}>加载服务器布局</button>
        <button type="button" className="pip-text-btn" onClick={() => resolveConflict(false)}>保留本地并覆盖</button>
      </div>}
      {saveState === 'error' && <div className="pip-toast pip-error m-frost" role="alert">自动保存失败，可点“保存布局”重试</div>}
    </div>
  )
}

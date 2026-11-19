import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import type Konva from 'konva'
import { Image as KImage, Label, Layer, Rect, Stage, Tag, Text, Transformer } from 'react-konva'
import type { ImageItem } from './api'
import {
  intersects, itemAABB, localToScene, normalizeDragRect, sceneToLocal, visibleSceneRect, zoomAround,
  type CropRect, type View,
} from './pip/geometry'
import {
  cropFromDrag, noteAABB, noteDisplayText, visibleItems, type PipItem, type PipNote, type PipScene,
} from './pip/scene'
import { chooseTextureTier, createTextureCache, TEXTURE_MAX_LONG_EDGE, type Texture } from './pip/texture'
import { commitTransform, TRANSFORMER_SCALE_PROPS } from './pip/transform'
import { isTextEntryTarget } from './pip/shortcuts'

export type PipSelection =
  | { kind: 'image'; image_id: number }
  | { kind: 'note'; id: string }
  | { kind: 'images'; image_ids: number[] }

export interface PipCanvasProps {
  scene: PipScene
  imagesById: Readonly<Record<number, ImageItem | undefined>>
  selection: PipSelection | null
  cropMode: boolean
  handTool: boolean
  win: Window
  onContextMenu: (event: MouseEvent, imageId: number | null) => void
  onSelect: (selection: PipSelection | null) => void
  onToggleSelect: (imageId: number) => void
  onViewChange: (view: View) => void
  onItemChange: (imageId: number, patch: Partial<Omit<PipItem, 'image_id'>>) => void
  onMoveItems: (imageIds: readonly number[], dx: number, dy: number) => void
  onNoteChange: (noteId: string, patch: Partial<Omit<PipNote, 'id'>>) => void
  onEditNote?: (noteId: string) => void
  onCrop: (imageId: number, rect: CropRect) => void
  onAddNote: (x: number, y: number) => void
  onOpenOriginal: (imageId: number) => void
  onOpenPost: (imageId: number, shift: boolean) => void
  onViewportChange?: (viewport: { width: number; height: number }) => void
}

const gridSizeFor = (scale: number) => {
  const pixels = 40 * scale
  return pixels + 'px ' + pixels + 'px'
}

const ITEM_TRANSFORM_ANCHORS = [
  'top-left', 'top-center', 'top-right',
  'middle-left', 'middle-right',
  'bottom-left', 'bottom-center', 'bottom-right',
]

interface DragState {
  items: PipItem[]
  anchor: PipItem
  nodes: Map<number, { image?: Konva.Node; backing?: Konva.Node }>
}

function useItemTexture(cache: ReturnType<typeof createTextureCache>, item: PipItem, url: string | undefined, tier: number) {
  const crop = item.crop
  const variantKey = JSON.stringify([url, crop, item.flip_x, item.grayscale])
  const requestKey = variantKey + '|' + tier
  const [loaded, setLoaded] = useState<{ requestKey: string; variantKey: string; texture: Texture } | null>(null)
  useEffect(() => {
    if (!url) return
    const guard = { disposed: false }
    cache.get(url, crop, item.flip_x, item.grayscale, tier)
      .then(texture => { if (!guard.disposed) setLoaded({ requestKey, variantKey, texture }) })
      .catch(() => {})
    return () => { guard.disposed = true }
  }, [cache, url, crop, item.flip_x, item.grayscale, tier, requestKey, variantKey])
  if (!loaded) return null
  if (loaded.requestKey === requestKey) return loaded.texture
  return loaded.variantKey === variantKey ? loaded.texture : null
}

function useTextureTier(displayLongSide: number, naturalLongSide: number): number {
  const [tier, setTier] = useState(() => chooseTextureTier(displayLongSide, naturalLongSide, null))
  useEffect(() => {
    setTier(current => chooseTextureTier(displayLongSide, naturalLongSide, current))
  }, [displayLongSide, naturalLongSide])
  return tier
}

function noteTextWidth(note: PipNote) {
  return Math.max(120, Math.min(600, note.text.length * note.font_size * 0.72))
}

function PipNoteNode({ note, selected, hand, onSelect, onEdit, onChange }: {
  note: PipNote
  selected: boolean
  hand: boolean
  onSelect: () => void
  onEdit: () => void
  onChange: (patch: Partial<Omit<PipNote, 'id'>>) => void
}) {
  const empty = note.text.length === 0
  return (
    <Label
      id={`pip-note-${note.id}`}
      x={note.x}
      y={note.y}
      draggable={!hand}
      listening={!hand}
      onClick={event => { if (event.evt.button === 0) onSelect() }}
      onTap={onSelect}
      onDblClick={event => { event.cancelBubble = true; onEdit() }}
      onDblTap={event => { event.cancelBubble = true; onEdit() }}
      onDragEnd={event => onChange({ x: event.target.x(), y: event.target.y() })}
    >
      <Tag
        fill={note.color}
        cornerRadius={1}
        stroke={selected ? 'rgba(47,42,36,0.85)' : 'rgba(47,42,36,0.14)'}
        strokeWidth={selected ? 1 : 1}
        shadowColor="#2a231c"
        shadowBlur={10}
        shadowOpacity={0.14}
        shadowOffset={{ x: 0, y: 4 }}
      />
      <Text
        text={noteDisplayText(note)}
        fontSize={note.font_size}
        padding={8}
        fill={empty ? 'rgba(47,42,36,0.42)' : '#2f2a24'}
        width={noteTextWidth(note)}
        fontFamily="Songti SC, SimSun, Georgia, serif"
      />
    </Label>
  )
}

function PipImageNode({ item, url, view, displayLongSide, naturalLongSide, selected, outline, cropMode, panMode, cache, onPointerDown, onTouchSelect, onDragStart, onDragMove, onDragEnd, onChange, onCrop, onOpenOriginal, onOpenPost }: {
  item: PipItem
  url: string | undefined
  view: View
  displayLongSide: number
  naturalLongSide: number
  selected: boolean
  outline: boolean
  cropMode: boolean
  panMode: boolean
  cache: ReturnType<typeof createTextureCache>
  onPointerDown: (event: Konva.KonvaEventObject<MouseEvent>) => void
  onTouchSelect: () => void
  onDragStart: (node: Konva.Node) => void
  onDragMove: (node: Konva.Node) => void
  onDragEnd: (node: Konva.Node) => void
  onChange: (patch: Partial<Omit<PipItem, 'image_id'>>) => void
  onCrop: (rect: CropRect) => void
  onOpenOriginal: () => void
  onOpenPost: (shift: boolean) => void
}) {
  const tier = useTextureTier(displayLongSide, naturalLongSide)
  const texture = useItemTexture(cache, item, url, tier)
  const [drag, setDrag] = useState<{ start: { x: number; y: number }; end: { x: number; y: number } } | null>(null)

  const pointerLocal = (event: Konva.KonvaEventObject<MouseEvent>) => {
    const pointer = event.target.getStage()?.getPointerPosition()
    if (!pointer) return null
    return sceneToLocal(item, (pointer.x - view.x) / view.scale, (pointer.y - view.y) / view.scale)
  }

  const cropRect = drag ? normalizeDragRect(drag.start, drag.end, item.width, item.height) : null
  const overlay = cropRect
    ? localToScene(item, cropRect.x * item.width, cropRect.y * item.height)
    : null

  return (
    <>
      <Rect
        id={'pip-backing-' + item.image_id}
        x={item.x}
        y={item.y}
        width={item.width}
        height={item.height}
        rotation={item.rotation}
        fill="#e4ddd0"
        listening={false}
      />
      <KImage
        id={'pip-image-' + item.image_id}
        image={texture?.source}
        x={item.x}
        y={item.y}
        width={item.width}
        height={item.height}
        scaleX={1}
        scaleY={1}
        rotation={item.rotation}
        opacity={item.opacity}
        stroke={outline ? 'rgba(47,42,36,0.5)' : undefined}
        strokeWidth={outline ? 1 / view.scale : undefined}
        strokeScaleEnabled={false}
        draggable={!panMode && !(cropMode && selected)}
        dragDistance={0}
        onDragStart={event => onDragStart(event.target)}
        onDragMove={event => onDragMove(event.target)}
        onDragEnd={event => onDragEnd(event.target)}
        onMouseDown={event => {
          if (event.evt.button !== 0 || panMode) return
          event.cancelBubble = true
          if (cropMode && selected) {
            const local = pointerLocal(event)
            if (local) setDrag({ start: local, end: local })
            return
          }
          onPointerDown(event)
        }}
        onTouchStart={event => { if (!panMode) { event.cancelBubble = true; onTouchSelect() } }}
        onMouseMove={event => {
          if (!drag) return
          event.cancelBubble = true
          const local = pointerLocal(event)
          if (local) setDrag({ start: drag.start, end: local })
        }}
        onMouseUp={event => {
          if (!drag) return
          event.cancelBubble = true
          const local = pointerLocal(event) ?? drag.end
          const rect = cropFromDrag(item, drag.start, local)
          setDrag(null)
          if (rect) onCrop(rect)
        }}
        onTransformEnd={event => {
          // Bake scale into width/height and reset it before React re-renders (see pip/transform.ts).
          onChange(commitTransform(event.target))
        }}
        onDblClick={event => {
          if (panMode) return
          event.cancelBubble = true
          if (cropMode) return
          if (event.evt.shiftKey) onOpenPost(true)
          else onOpenOriginal()
        }}
      />
      {overlay && cropRect && (
        <Rect
          x={overlay.x}
          y={overlay.y}
          width={cropRect.width * item.width}
          height={cropRect.height * item.height}
          rotation={item.rotation}
          fill="rgba(47,42,36,0.08)"
          stroke="rgba(47,42,36,0.75)"
          strokeWidth={1 / view.scale}
          listening={false}
        />
      )}
    </>
  )
}

export default function PipCanvas({
  scene, imagesById, selection, cropMode, handTool, win, onContextMenu, onSelect, onToggleSelect, onViewChange, onItemChange, onMoveItems,
  onNoteChange, onCrop, onAddNote, onOpenOriginal, onOpenPost, onViewportChange, onEditNote,
}: PipCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: Math.max(1, win.innerWidth), height: Math.max(1, win.innerHeight) })
  const [spaceHeld, setSpaceHeld] = useState(false)
  const hand = handTool || spaceHeld
  const stageRef = useRef<Konva.Stage>(null)
  const transformerRef = useRef<Konva.Transformer>(null)
  const panStart = useRef<{ pointer: { x: number; y: number }; view: View } | null>(null)
  const cache = useMemo(() => createTextureCache(), [])
  const viewRef = useRef(scene.view)
  if (!panStart.current) viewRef.current = scene.view
  const dragRef = useRef<DragState | null>(null)

  useEffect(() => () => cache.clear(), [cache])

  useEffect(() => {
    if (!import.meta.env.DEV) return
    const debug = win as Window & { __PIP_DEBUG?: Record<string, unknown> }
    const write = () => {
      debug.__PIP_DEBUG = { ...debug.__PIP_DEBUG, texture: cache.stats() }
    }
    write()
    const timer = setInterval(write, 500)
    return () => clearInterval(timer)
  }, [win, cache])

  useEffect(() => {
    const element = hostRef.current
    const update = () => {
      const next = element
        ? { width: Math.max(1, element.clientWidth), height: Math.max(1, element.clientHeight) }
        : { width: Math.max(1, win.innerWidth), height: Math.max(1, win.innerHeight) }
      setSize(next)
      onViewportChange?.(next)
    }
    update()
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(update) : null
    if (element) observer?.observe(element)
    win.addEventListener('resize', update)
    return () => { observer?.disconnect(); win.removeEventListener('resize', update) }
  }, [win, onViewportChange])

  useEffect(() => {
    const down = (event: KeyboardEvent) => {
      if (event.code !== 'Space' || isTextEntryTarget(event.target) ||
        (event.target as HTMLElement | null)?.closest?.('button')) return
      setSpaceHeld(true)
      event.preventDefault()
    }
    const up = (event: KeyboardEvent) => {
      if (event.code === 'Space') setSpaceHeld(false)
    }
    const blur = () => setSpaceHeld(false)
    win.addEventListener('blur', blur)
    win.addEventListener('keydown', down)
    win.addEventListener('keyup', up)
    return () => {
      win.removeEventListener('blur', blur)
      win.removeEventListener('keydown', down)
      win.removeEventListener('keyup', up)
    }
  }, [win])

  useEffect(() => {
    const stage = stageRef.current
    const transformer = transformerRef.current
    if (!stage || !transformer) return
    if (cropMode || !selection || selection.kind !== 'image') {
      transformer.nodes([])
      return
    }
    const node = stage.findOne('#pip-image-' + selection.image_id)
    transformer.nodes(node ? [node] : [])
  }, [selection, cropMode, scene.items, scene.view])

  const pointerScene = (): { x: number; y: number } | null => {
    const pointer = stageRef.current?.getPointerPosition()
    if (!pointer) return null
    return { x: (pointer.x - scene.view.x) / scene.view.scale, y: (pointer.y - scene.view.y) / scene.view.scale }
  }

  const selectedIds = useMemo(() => {
    if (!selection) return new Set<number>()
    if (selection.kind === 'image') return new Set([selection.image_id])
    if (selection.kind === 'images') return new Set(selection.image_ids)
    return new Set<number>()
  }, [selection])
  const selectedNoteId = selection?.kind === 'note' ? selection.id : null

  const setGesture = (mode: 'image' | 'pan' | null) => {
    const host = hostRef.current
    if (!host) return
    if (mode) host.dataset.drag = mode
    else delete host.dataset.drag
  }

  const beginDrag = (item: PipItem) => {
    const stage = stageRef.current
    if (!stage) return
    const items = selectedIds.has(item.image_id) ? scene.items.filter(entry => selectedIds.has(entry.image_id)) : [item]
    dragRef.current = {
      items,
      anchor: item,
      nodes: new Map(items.map(entry => [entry.image_id, {
        image: stage.findOne('#pip-image-' + entry.image_id),
        backing: stage.findOne('#pip-backing-' + entry.image_id),
      }])),
    }
    setGesture('image')
  }

  const moveDrag = (node: Konva.Node) => {
    const drag = dragRef.current
    if (!drag) return
    const dx = node.x() - drag.anchor.x
    const dy = node.y() - drag.anchor.y
    for (const item of drag.items) {
      const nodes = drag.nodes.get(item.image_id)
      const point = { x: item.x + dx, y: item.y + dy }
      if (nodes?.image !== node) nodes?.image?.position(point)
      nodes?.backing?.position(point)
    }
    node.getLayer()?.batchDraw()
  }

  const endDrag = (node: Konva.Node) => {
    const drag = dragRef.current
    if (!drag) return
    moveDrag(node)
    dragRef.current = null
    setGesture(null)
    onMoveItems(drag.items.map(item => item.image_id), node.x() - drag.anchor.x, node.y() - drag.anchor.y)
  }

  const handleImagePointerDown = (item: PipItem, event: Konva.KonvaEventObject<MouseEvent>) => {
    if (event.evt.shiftKey) {
      event.target.stopDrag(event.evt)
      onToggleSelect(item.image_id)
    } else if (!selectedIds.has(item.image_id)) {
      onSelect({ kind: 'image', image_id: item.image_id })
    }
  }

  const applyView = (view: View) => {
    viewRef.current = view
    const stage = stageRef.current
    stage?.setAttrs({ x: view.x, y: view.y, scaleX: view.scale, scaleY: view.scale })
    stage?.batchDraw()
    const host = hostRef.current
    if (host) {
      host.style.backgroundSize = gridSizeFor(view.scale)
      host.style.backgroundPosition = view.x + 'px ' + view.y + 'px'
    }
  }

  const finishPan = () => {
    const start = panStart.current
    if (!start) return
    panStart.current = null
    setGesture(null)
    const next = viewRef.current
    if (next.x !== start.view.x || next.y !== start.view.y) onViewChange(next)
  }

  // Mount one viewport of overscan so newly exposed nodes are already present during a pan.
  const overscan = Math.max(size.width, size.height)
  const visible = visibleItems(scene, scene.view, size, overscan)
  useEffect(() => {
    if (!import.meta.env.DEV) return
    const debug = win as Window & { __PIP_DEBUG?: Record<string, unknown> }
    debug.__PIP_DEBUG = {
      ...debug.__PIP_DEBUG,
      render: { at: performance.now(), total: scene.items.length, visible: visible.length, scale: scene.view.scale },
    }
  }, [win, scene.items, scene.notes, visible])

  useEffect(() => {
    if (!import.meta.env.DEV) return
    const stage = stageRef.current
    const debug = win as Window & { __PIP_DEBUG?: Record<string, unknown> }
    const item = selection?.kind === 'image'
      ? scene.items.find(entry => entry.image_id === selection.image_id) : undefined
    const node = item && stage ? stage.findOne('#pip-image-' + item.image_id) : null
    if (!item || !node) {
      debug.__PIP_DEBUG = { ...debug.__PIP_DEBUG, selected: null }
      return
    }
    const rect = node.getClientRect()
    const scale = scene.view.scale
    debug.__PIP_DEBUG = {
      ...debug.__PIP_DEBUG,
      selected: {
        imageId: item.image_id,
        itemWidth: item.width, itemHeight: item.height, rotation: item.rotation,
        crop: item.crop,
        nodeWidth: node.width(), nodeHeight: node.height(),
        nodeScaleX: node.scaleX(), nodeScaleY: node.scaleY(),
        drawnWidth: rect.width / scale, drawnHeight: rect.height / scale,
      },
    }
  }, [win, selection, scene.items, scene.view])

  const visibleWindow = visibleSceneRect(scene.view, size, overscan)
  const visibleNoteIds = new Set(
    scene.notes.filter(note => intersects(noteAABB(note), visibleWindow)).map(note => note.id),
  )


  return (
    <div ref={hostRef} className="pip-canvas-host"
      style={{
        cursor: hand ? 'grab' : 'default',
        backgroundSize: gridSizeFor(scene.view.scale),
        backgroundPosition: scene.view.x + 'px ' + scene.view.y + 'px',
      } as CSSProperties}>
    <Stage
      ref={stageRef}
      width={size.width}
      height={size.height}
      x={scene.view.x}
      y={scene.view.y}
      scaleX={scene.view.scale}
      scaleY={scene.view.scale}
      onWheel={event => {
        event.evt.preventDefault()
        const stage = stageRef.current
        let pointer = stage?.getPointerPosition()
        if (!pointer && stage) {
          const box = stage.container().getBoundingClientRect()
          pointer = { x: event.evt.clientX - box.left, y: event.evt.clientY - box.top }
        }
        if (!pointer) return
        if (!event.evt.deltaY || dragRef.current || panStart.current) return
        const factor = event.evt.deltaY > 0 ? 1 / 1.1 : 1.1
        const next = zoomAround(viewRef.current, pointer, factor)
        applyView(next)
        onViewChange(next)
      }}
      onContextMenu={event => {
        event.evt.preventDefault()
        const match = /^pip-image-(\d+)$/.exec(event.target.id())
        onContextMenu(event.evt, match ? Number(match[1]) : null)
      }}
      onMouseDown={event => {
        if (event.evt.button > 1) return
        const pointer = stageRef.current?.getPointerPosition()
        if (!pointer) return
        if (event.evt.button === 1) event.evt.preventDefault()
        const background = event.target === stageRef.current
        if (event.evt.button === 1 || hand || background) {
          panStart.current = { pointer, view: viewRef.current }
          setGesture('pan')
        }
        if (background && !hand && event.evt.button === 0) onSelect(null)
      }}
      onMouseMove={() => {
        const start = panStart.current
        const pointer = stageRef.current?.getPointerPosition()
        if (!start || !pointer) return
        applyView({
          ...start.view,
          x: start.view.x + pointer.x - start.pointer.x,
          y: start.view.y + pointer.y - start.pointer.y,
        })
      }}
      onMouseUp={finishPan}
      onMouseLeave={finishPan}
      onDblClick={event => {
        if (hand || event.target !== stageRef.current) return
        const point = pointerScene()
        if (point) onAddNote(point.x, point.y)
      }}
    >
      <Layer>
        {visible.map(item => {
          const image = imagesById[item.image_id]
          const bounds = itemAABB(item)
          const displayLongSide = Math.max(bounds.width, bounds.height) * scene.view.scale
          const naturalLongSide = Math.max(image?.width ?? 0, image?.height ?? 0) || TEXTURE_MAX_LONG_EDGE
          const isSelected = selectedIds.has(item.image_id)
          return (
            <PipImageNode
              key={item.image_id}
              item={item}
              url={image?.url}
              view={scene.view}
              displayLongSide={displayLongSide}
              naturalLongSide={naturalLongSide}
              selected={isSelected}
              outline={selection?.kind === 'images' && isSelected}
              cropMode={cropMode}
              panMode={hand}
              cache={cache}
              onDragStart={() => beginDrag(item)}
              onDragMove={moveDrag}
              onDragEnd={endDrag}
              onPointerDown={event => handleImagePointerDown(item, event)}
              onTouchSelect={() => onSelect({ kind: 'image', image_id: item.image_id })}
              onChange={patch => onItemChange(item.image_id, patch)}
              onCrop={rect => onCrop(item.image_id, rect)}
              onOpenOriginal={() => onOpenOriginal(item.image_id)}
              onOpenPost={shift => onOpenPost(item.image_id, shift)}
            />
          )
        })}
        {scene.notes.filter(note => visibleNoteIds.has(note.id)).map(note => (
          <PipNoteNode
            key={note.id}
            note={note}
            hand={hand}
            selected={note.id === selectedNoteId}
            onSelect={() => onSelect({ kind: 'note', id: note.id })}
            onEdit={() => onEditNote?.(note.id)}
            onChange={patch => onNoteChange(note.id, patch)}
          />
        ))}
        <Transformer
          ref={transformerRef}
          listening={!hand}
          rotateEnabled={!cropMode}
          enabledAnchors={cropMode ? [] : ITEM_TRANSFORM_ANCHORS}
          {...TRANSFORMER_SCALE_PROPS}
          anchorSize={8}
          anchorCornerRadius={1}
          anchorFill="#f7f2e7"
          anchorStroke="rgba(47,42,36,0.9)"
          borderStroke="rgba(47,42,36,0.8)"
          borderStrokeWidth={1}
          rotateAnchorOffset={26}
          rotationSnaps={[0, 90, 180, 270]}
          ignoreStroke
          boundBoxFunc={(oldBox, newBox) => (Math.abs(newBox.width) < 20 || Math.abs(newBox.height) < 20 ? oldBox : newBox)}
        />
      </Layer>
    </Stage>
    </div>
  )
}
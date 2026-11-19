import potpack, { type PotpackBox } from 'potpack'
import {
  DEFAULT_CROP, clampCrop, composeCrop, fitView, intersects, itemAABB, normalizeDragRect,
  rotateOffset, unionRects, visibleSceneRect, type CropRect, type Rect, type View, type Viewport,
} from './geometry'

export interface PipItem {
  image_id: number
  x: number
  y: number
  width: number
  height: number
  rotation: number
  flip_x: boolean
  grayscale: boolean
  opacity: number
  crop: CropRect | null
}

export interface PipNote {
  id: string
  x: number
  y: number
  text: string
  color: string
  font_size: number
}

export interface PipScene {
  version: 1
  view: View
  items: PipItem[]
  notes: PipNote[]
}

export const SCENE_VERSION = 1 as const
export const MAX_ITEMS = 200
export const MAX_NOTES = 100
export const MIN_ZOOM = 0.05
export const MAX_ZOOM = 20
export const NEW_ITEM_LONG_EDGE = 300
export const DEFAULT_NOTE_COLOR = '#f5d76e'
/** Display-only hint for an empty note; it is never stored as note content. */
export const NOTE_PLACEHOLDER = '双击编辑'
/** Older versions stored this placeholder as real content; normalized away on load. */
export const LEGACY_NOTE_PLACEHOLDER = '双击编辑文字'

export class SceneError extends Error {
  constructor(message = '布局数据格式不正确') {
    super(message)
    this.name = 'SceneError'
  }
}

export function emptyScene(): PipScene {
  return { version: SCENE_VERSION, view: { x: 0, y: 0, scale: 1 }, items: [], notes: [] }
}

export function noteAABB(note: PipNote): Rect {
  const lines = note.text.split('\n').length
  const width = Math.max(40, Math.min(4000, note.text.length * note.font_size * 0.62))
  return { x: note.x, y: note.y, width, height: Math.max(20, lines * note.font_size * 1.4) }
}

export function sceneBounds(scene: PipScene): Rect | null {
  const rects: Rect[] = scene.items.map(itemAABB)
  for (const note of scene.notes) rects.push(noteAABB(note))
  return unionRects(rects)
}

export function fitSceneView(scene: PipScene, viewport: Viewport, padding = 24): View {
  return fitView(sceneBounds(scene), viewport, padding, MIN_ZOOM, MAX_ZOOM)
}

export function visibleItems(scene: PipScene, view: View, viewport: Viewport, marginPx = 200): PipItem[] {
  const window = visibleSceneRect(view, viewport, marginPx)
  return scene.items.filter(item => intersects(itemAABB(item), window))
}

export function setView(scene: PipScene, view: View): PipScene {
  if (scene.view.x === view.x && scene.view.y === view.y && scene.view.scale === view.scale) return scene
  return { ...scene, view }
}

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)

/** Add freshly synced images below everything already placed; two per row, like the original canvas. */
export function addImages(scene: PipScene, incoming: readonly { image_id: number; width: number; height: number }[]): PipScene {
  const known = new Set(scene.items.map(item => item.image_id))
  const fresh = incoming.filter(image => !known.has(image.image_id) && image.image_id > 0)
  if (fresh.length === 0) return scene
  let top = 0
  for (const item of scene.items) {
    const aabb = itemAABB(item)
    top = Math.max(top, aabb.y + aabb.height + 20)
  }
  const items = [...scene.items]
  for (let i = 0; i < fresh.length && items.length < MAX_ITEMS; i += 2) {
    let x = 0
    let rowHeight = 0
    for (const image of fresh.slice(i, i + 2)) {
      if (items.length >= MAX_ITEMS) break
      const width = image.width > 0 ? image.width : NEW_ITEM_LONG_EDGE
      const height = image.height > 0 ? image.height : NEW_ITEM_LONG_EDGE
      const scale = NEW_ITEM_LONG_EDGE / Math.max(width, height)
      const itemWidth = Math.max(1, width * scale)
      const itemHeight = Math.max(1, height * scale)
      items.push({
        image_id: image.image_id, x, y: top, width: itemWidth, height: itemHeight,
        rotation: 0, flip_x: false, grayscale: false, opacity: 1, crop: null,
      })
      x += itemWidth + 20
      rowHeight = Math.max(rowHeight, itemHeight)
    }
    top += rowHeight + 20
  }
  return { ...scene, items }
}

/** Place one image dropped from the library at a scene point (long edge 300 unless tiny). */
export function addImageAt(scene: PipScene, image: { image_id: number; width: number; height: number },
  x: number, y: number): PipScene {
  if (scene.items.length >= MAX_ITEMS || scene.items.some(item => item.image_id === image.image_id)) return scene
  const width = image.width > 0 ? image.width : NEW_ITEM_LONG_EDGE
  const height = image.height > 0 ? image.height : NEW_ITEM_LONG_EDGE
  const scale = NEW_ITEM_LONG_EDGE / Math.max(width, height)
  return {
    ...scene,
    items: [...scene.items, {
      image_id: image.image_id,
      x: clamp(x, -1e6, 1e6), y: clamp(y, -1e6, 1e6),
      width: Math.max(1, width * scale), height: Math.max(1, height * scale),
      rotation: 0, flip_x: false, grayscale: false, opacity: 1, crop: null,
    }],
  }
}

export function updateItem(scene: PipScene, imageId: number, patch: Partial<Omit<PipItem, 'image_id'>>): PipScene {
  let changed = false
  const items = scene.items.map(item => {
    if (item.image_id !== imageId) return item
    const next: PipItem = { ...item, ...patch }
    next.width = clamp(finite(next.width) ? next.width : item.width, 1, 100000)
    next.height = clamp(finite(next.height) ? next.height : item.height, 1, 100000)
    next.x = finite(next.x) ? clamp(next.x, -1e6, 1e6) : item.x
    next.y = finite(next.y) ? clamp(next.y, -1e6, 1e6) : item.y
    next.rotation = finite(next.rotation) ? clamp(next.rotation, -3600, 3600) : item.rotation
    next.opacity = clamp(finite(next.opacity) ? next.opacity : item.opacity, 0, 1)
    if (next.crop) next.crop = clampCrop(next.crop)
    changed = true
    return next
  })
  return changed ? { ...scene, items } : scene
}

export function removeItem(scene: PipScene, imageId: number): PipScene {
  const items = scene.items.filter(item => item.image_id !== imageId)
  return items.length === scene.items.length ? scene : { ...scene, items }
}

/**
 * Raise the given images to the top of the stacking order in one step (used by dynamic z-order:
 * selecting or dragging any image raises it). The raised group keeps its own relative order and
 * everything else keeps its order below. Unknown ids are ignored; an already-raised selection
 * returns the same scene object so callers can skip dirty/undo work.
 */
export function raiseItems(scene: PipScene, imageIds: readonly number[]): PipScene {
  const ids = new Set(imageIds)
  if (ids.size === 0) return scene
  const raised = scene.items.filter(item => ids.has(item.image_id))
  if (raised.length === 0) return scene
  const rest = scene.items.filter(item => !ids.has(item.image_id))
  const items = [...rest, ...raised]
  return items.every((item, index) => item === scene.items[index]) ? scene : { ...scene, items }
}

export function bringToFront(scene: PipScene, imageId: number): PipScene {
  return raiseItems(scene, [imageId])
}

export function sendToBack(scene: PipScene, imageId: number): PipScene {
  const item = scene.items.find(entry => entry.image_id === imageId)
  if (!item || scene.items[0]?.image_id === imageId) return scene
  return { ...scene, items: [item, ...scene.items.filter(entry => entry.image_id !== imageId)] }
}

export function toggleFlag(scene: PipScene, imageId: number, flag: 'flip_x' | 'grayscale'): PipScene {
  let changed = false
  const items = scene.items.map(item => {
    if (item.image_id !== imageId) return item
    changed = true
    return { ...item, [flag]: !item[flag] }
  })
  return changed ? { ...scene, items } : scene
}

export function setAllOpacity(scene: PipScene, opacity: number): PipScene {
  const value = clamp(finite(opacity) ? opacity : 1, 0, 1)
  const items = scene.items.map(item => item.opacity === value ? item : { ...item, opacity: value })
  return items.some((item, index) => item !== scene.items[index]) ? { ...scene, items } : scene
}

/**
 * Crop an item using a rectangle normalized to its currently displayed (already cropped) face.
 * The texture pipeline applies the source crop first and flips the whole cropped face afterwards,
 * so a screen-space rectangle must be mirrored in x before composing it into the source crop when
 * flip_x is on. The item position offset stays in display space (and rotation) as before.
 */
export function cropItem(scene: PipScene, imageId: number, rect: CropRect): PipScene {
  let changed = false
  const items = scene.items.map(item => {
    if (item.image_id !== imageId) return item
    changed = true
    const local = clampCrop(rect)
    const sourceLocal = item.flip_x
      ? { x: 1 - (local.x + local.width), y: local.y, width: local.width, height: local.height }
      : local
    const offset = rotateOffset(local.x * item.width, local.y * item.height, item.rotation)
    return {
      ...item,
      x: clamp(item.x + offset.x, -1e6, 1e6),
      y: clamp(item.y + offset.y, -1e6, 1e6),
      width: Math.max(1, item.width * local.width),
      height: Math.max(1, item.height * local.height),
      crop: composeCrop(item.crop, sourceLocal),
    }
  })
  return changed ? { ...scene, items } : scene
}

export function resetCrop(scene: PipScene, imageId: number): PipScene {
  let changed = false
  const items = scene.items.map(item => {
    if (item.image_id !== imageId || !item.crop) return item
    changed = true
    const crop = item.crop
    const width = item.width / crop.width
    const height = item.height / crop.height
    // The visible face is flipped, so its top-left corresponds to the crop's right edge:
    // the display-space origin is 1 - crop.x - crop.width instead of crop.x.
    const originX = item.flip_x ? 1 - crop.x - crop.width : crop.x
    const offset = rotateOffset(originX * width, crop.y * height, item.rotation)
    return { ...item, x: item.x - offset.x, y: item.y - offset.y, width, height, crop: null }
  })
  return changed ? { ...scene, items } : scene
}

/**
 * Reset one image to the state it had right after being dropped onto the board:
 * original aspect ratio, default 300px long edge, no rotation/crop/flip/grayscale,
 * opacity 100%. The visual center stays where it is. This is a normal undoable edit.
 */
export function resetItem(scene: PipScene, imageId: number,
  natural?: { width?: number | null; height?: number | null } | null): PipScene {
  let changed = false
  const items = scene.items.map(item => {
    if (item.image_id !== imageId) return item
    changed = true
    const bounds = itemAABB(item)
    const centerX = bounds.x + bounds.width / 2
    const centerY = bounds.y + bounds.height / 2
    const crop = item.crop
    const positive = (value: number | null | undefined): number | null =>
      typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
    const sourceWidth = positive(natural?.width) ?? item.width / (crop && crop.width > 0 ? crop.width : 1)
    const sourceHeight = positive(natural?.height) ?? item.height / (crop && crop.height > 0 ? crop.height : 1)
    const scale = NEW_ITEM_LONG_EDGE / Math.max(1, sourceWidth, sourceHeight)
    const width = Math.max(1, sourceWidth * scale)
    const height = Math.max(1, sourceHeight * scale)
    return {
      ...item,
      x: clamp(centerX - width / 2, -1e6, 1e6),
      y: clamp(centerY - height / 2, -1e6, 1e6),
      width, height, rotation: 0, flip_x: false, grayscale: false, opacity: 1, crop: null,
    }
  })
  return changed ? { ...scene, items } : scene
}

/** Tightly pack every item with potpack (ISC licence), preserving z-order. */
export function compactScene(scene: PipScene, gap = 20): PipScene {
  if (scene.items.length === 0) return scene
  const boxes: (PotpackBox & { i: number; dx: number; dy: number })[] = scene.items.map((item, index) => {
    const aabb = itemAABB(item)
    return { w: Math.max(1, aabb.width) + gap * 2, h: Math.max(1, aabb.height) + gap * 2, i: index, dx: item.x - aabb.x, dy: item.y - aabb.y }
  })
  potpack(boxes)
  const items = scene.items.map((item, index) => {
    const box = boxes.find(entry => entry.i === index)!
    return { ...item, x: (box.x ?? 0) + gap + box.dx, y: (box.y ?? 0) + gap + box.dy }
  })
  return { ...scene, items }
}

export function nextNoteId(notes: readonly PipNote[]): string {
  const used = new Set(notes.map(note => note.id))
  let index = notes.length + 1
  while (used.has(`note-${index}`)) index += 1
  return `note-${index}`
}

export function addNote(scene: PipScene, x: number, y: number, text = ''): PipScene {
  if (scene.notes.length >= MAX_NOTES) return scene
  const note: PipNote = {
    id: nextNoteId(scene.notes), x, y, text: text.slice(0, 2000),
    color: DEFAULT_NOTE_COLOR, font_size: 16,
  }
  return { ...scene, notes: [...scene.notes, note] }
}

/** Text drawn on the canvas: an empty note only shows the gray placeholder. */
export function noteDisplayText(note: PipNote): string {
  return note.text.length > 0 ? note.text : NOTE_PLACEHOLDER
}

/** Turn the placeholder older versions saved as content back into an empty note. */
export function normalizeNotePlaceholders(scene: PipScene): PipScene {
  let changed = false
  const notes = scene.notes.map(note => {
    if (note.text !== LEGACY_NOTE_PLACEHOLDER) return note
    changed = true
    return { ...note, text: '' }
  })
  return changed ? { ...scene, notes } : scene
}

export function updateNote(scene: PipScene, id: string, patch: Partial<Omit<PipNote, 'id'>>): PipScene {
  let changed = false
  const notes = scene.notes.map(note => {
    if (note.id !== id) return note
    changed = true
    const next: PipNote = { ...note, ...patch }
    next.x = finite(next.x) ? clamp(next.x, -1e6, 1e6) : note.x
    next.y = finite(next.y) ? clamp(next.y, -1e6, 1e6) : note.y
    next.text = typeof next.text === 'string' ? next.text.slice(0, 2000) : note.text
    next.font_size = clamp(finite(next.font_size) ? next.font_size : note.font_size, 8, 200)
    next.color = /^#[0-9a-fA-F]{6}$/.test(next.color) ? next.color : note.color
    return next
  })
  return changed ? { ...scene, notes } : scene
}

export function removeNote(scene: PipScene, id: string): PipScene {
  const notes = scene.notes.filter(note => note.id !== id)
  return notes.length === scene.notes.length ? scene : { ...scene, notes }
}

/**
 * Drag inside an item, in the item's local unrotated pixels, normalized into a crop.
 * Returns null for a click (no rectangle), so a plain click never crops.
 */
export function cropFromDrag(item: PipItem, start: { x: number; y: number }, end: { x: number; y: number }): CropRect | null {
  return normalizeDragRect(start, end, item.width, item.height)
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)
const exactKeys = (value: Record<string, unknown>, keys: readonly string[]) => {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(key => Object.hasOwn(value, key))
}
const numberIn = (value: unknown, min: number, max: number): value is number =>
  finite(value) && value >= min && value <= max
const positiveId = (value: unknown): value is number => finite(value) && Number.isSafeInteger(value) && value > 0
const boolean = (value: unknown): value is boolean => typeof value === 'boolean'

function parseCrop(value: unknown): CropRect | null {
  if (value === null) return null
  if (!isObject(value) || !exactKeys(value, ['x', 'y', 'width', 'height'])) throw new SceneError('裁剪参数不正确')
  const { x, y, width, height } = value
  if (!numberIn(x, 0, 1) || !numberIn(y, 0, 1) || !numberIn(width, 0.0001, 1) || !numberIn(height, 0.0001, 1)) {
    throw new SceneError('裁剪范围超出图片')
  }
  if (x + width > 1.0000001 || y + height > 1.0000001) throw new SceneError('裁剪范围超出图片')
  return clampCrop({ x, y, width, height })
}

function parseItem(value: unknown): PipItem {
  if (!isObject(value) || !exactKeys(value, ['image_id', 'x', 'y', 'width', 'height', 'rotation', 'flip_x', 'grayscale', 'opacity', 'crop'])) {
    throw new SceneError('图片布局字段不正确')
  }
  if (!positiveId(value.image_id)) throw new SceneError('图片编号不正确')
  if (!numberIn(value.x, -1e6, 1e6) || !numberIn(value.y, -1e6, 1e6)) throw new SceneError('图片位置超出范围')
  if (!numberIn(value.width, 1, 100000) || !numberIn(value.height, 1, 100000)) throw new SceneError('图片尺寸超出范围')
  if (!numberIn(value.rotation, -3600, 3600)) throw new SceneError('旋转角度超出范围')
  if (!boolean(value.flip_x) || !boolean(value.grayscale)) throw new SceneError('图片开关不正确')
  if (!numberIn(value.opacity, 0, 1)) throw new SceneError('不透明度超出范围')
  const crop = parseCrop(value.crop)
  return {
    image_id: value.image_id, x: value.x, y: value.y, width: value.width, height: value.height,
    rotation: value.rotation, flip_x: value.flip_x, grayscale: value.grayscale, opacity: value.opacity, crop,
  }
}

function parseNote(value: unknown): PipNote {
  if (!isObject(value) || !exactKeys(value, ['id', 'x', 'y', 'text', 'color', 'font_size'])) {
    throw new SceneError('便签字段不正确')
  }
  const { id, x, y, text, color, font_size } = value
  if (typeof id !== 'string' || id.length === 0 || id.length > 64) throw new SceneError('便签编号不正确')
  if (!numberIn(x, -1e6, 1e6) || !numberIn(y, -1e6, 1e6)) throw new SceneError('便签位置超出范围')
  if (typeof text !== 'string' || text.length > 2000) throw new SceneError('便签文字过长')
  if (typeof color !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(color)) throw new SceneError('便签颜色不正确')
  if (!numberIn(font_size, 8, 200)) throw new SceneError('便签字号超出范围')
  return { id, x, y, text, color, font_size }
}

function parseView(value: unknown): View {
  if (!isObject(value) || !exactKeys(value, ['x', 'y', 'scale'])) throw new SceneError('视图字段不正确')
  if (!numberIn(value.x, -1e6, 1e6) || !numberIn(value.y, -1e6, 1e6)) throw new SceneError('视图位置超出范围')
  if (!numberIn(value.scale, MIN_ZOOM, MAX_ZOOM)) throw new SceneError('视图缩放超出范围')
  return { x: value.x, y: value.y, scale: value.scale }
}

/** Strict mirror of the backend scene contract; rejects unknown keys, bool-as-number, NaN and infra. */
export function parseScene(value: unknown): PipScene {
  if (!isObject(value) || !exactKeys(value, ['version', 'view', 'items', 'notes'])) throw new SceneError('布局缺少字段')
  if (value.version !== SCENE_VERSION) throw new SceneError('布局版本不支持')
  if (!Array.isArray(value.items) || value.items.length > MAX_ITEMS) throw new SceneError('画布图片过多')
  if (!Array.isArray(value.notes) || value.notes.length > MAX_NOTES) throw new SceneError('便签过多')
  const view = parseView(value.view)
  const items = value.items.map(parseItem)
  const imageIds = new Set(items.map(item => item.image_id))
  if (imageIds.size !== items.length) throw new SceneError('同一张图重复摆放')
  const notes = value.notes.map(parseNote)
  const noteIds = new Set(notes.map(note => note.id))
  if (noteIds.size !== notes.length) throw new SceneError('便签编号重复')
  return { version: SCENE_VERSION, view, items, notes }
}

/** Round-trip before saving, so an invalid in-memory scene is caught before the network. */
export function toWireScene(scene: PipScene): PipScene {
  return parseScene(JSON.parse(JSON.stringify(scene)))
}

export function cloneScene(scene: PipScene): PipScene {
  return parseScene(JSON.parse(JSON.stringify(scene)))
}

export { DEFAULT_CROP }
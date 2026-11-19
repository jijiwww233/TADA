export interface Rect { x: number; y: number; width: number; height: number }
export interface CropRect { x: number; y: number; width: number; height: number }
export interface View { x: number; y: number; scale: number }
export interface Viewport { width: number; height: number }

export const DEFAULT_CROP: CropRect = { x: 0, y: 0, width: 1, height: 1 }
export const MIN_CROP_SIZE = 0.02

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))

/** Rotate an offset around the origin, in degrees. Mirrors Konva's rotation direction. */
export function rotateOffset(dx: number, dy: number, degrees: number): { x: number; y: number } {
  if (!degrees) return { x: dx, y: dy }
  const rad = (degrees * Math.PI) / 180
  const cos = Math.cos(rad)
  const sin = Math.sin(rad)
  return { x: dx * cos - dy * sin, y: dx * sin + dy * cos }
}

/** Map a point in an item's local unrotated pixels into scene coordinates. */
export function localToScene(item: { x: number; y: number; rotation: number }, px: number, py: number) {
  const offset = rotateOffset(px, py, item.rotation)
  return { x: item.x + offset.x, y: item.y + offset.y }
}

/** Inverse of localToScene: a scene point expressed in the item's local unrotated pixels. */
export function sceneToLocal(item: { x: number; y: number; rotation: number }, sx: number, sy: number) {
  return rotateOffset(sx - item.x, sy - item.y, -item.rotation)
}

/** Axis-aligned bounds of a rotated item whose origin is its unrotated top-left corner. */
export function itemAABB(item: { x: number; y: number; width: number; height: number; rotation: number }): Rect {
  const corners = [
    [0, 0],
    [item.width, 0],
    [item.width, item.height],
    [0, item.height],
  ].map(([dx, dy]) => rotateOffset(dx, dy, item.rotation))
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const p of corners) {
    const x = item.x + p.x
    const y = item.y + p.y
    minX = Math.min(minX, x); minY = Math.min(minY, y)
    maxX = Math.max(maxX, x); maxY = Math.max(maxY, y)
  }
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY }
}

export function unionRects(rects: readonly Rect[]): Rect | null {
  if (rects.length === 0) return null
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const r of rects) {
    minX = Math.min(minX, r.x); minY = Math.min(minY, r.y)
    maxX = Math.max(maxX, r.x + r.width); maxY = Math.max(maxY, r.y + r.height)
  }
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY }
}

export function intersects(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
}

/** Scene-space rectangle currently visible for a viewport, with an optional margin in screen pixels. */
export function visibleSceneRect(view: View, viewport: Viewport, marginPx = 200): Rect {
  const scale = view.scale > 0 ? view.scale : 1
  return {
    x: -view.x / scale - marginPx / scale,
    y: -view.y / scale - marginPx / scale,
    width: viewport.width / scale + (2 * marginPx) / scale,
    height: viewport.height / scale + (2 * marginPx) / scale,
  }
}

/** Center a scene bounds inside the viewport; never zooms out past MIN_ZOOM or in past maxScale. */
export function fitView(bounds: Rect | null, viewport: Viewport, padding = 12,
  minScale = 0.05, maxScale = 20): View {
  if (!bounds || bounds.width <= 0 || bounds.height <= 0) return { x: 0, y: 0, scale: 1 }
  const scale = clamp(Math.min(
    (viewport.width - padding * 2) / bounds.width,
    (viewport.height - padding * 2) / bounds.height,
    maxScale,
  ), minScale, maxScale)
  return {
    scale,
    x: (viewport.width - bounds.width * scale) / 2 - bounds.x * scale,
    y: (viewport.height - bounds.height * scale) / 2 - bounds.y * scale,
  }
}

/** Zoom around a screen-space anchor so the scene point under the cursor stays put. */
export function zoomAround(view: View, anchor: { x: number; y: number }, factor: number,
  minScale = 0.05, maxScale = 20): View {
  const scale = clamp(view.scale * factor, minScale, maxScale)
  if (scale === view.scale) return view
  const sceneX = (anchor.x - view.x) / view.scale
  const sceneY = (anchor.y - view.y) / view.scale
  return { scale, x: anchor.x - sceneX * scale, y: anchor.y - sceneY * scale }
}

/** Clamp a normalized crop to the unit square, keeping a minimum size. */
export function clampCrop(crop: CropRect): CropRect {
  const x = clamp(crop.x, 0, 1 - MIN_CROP_SIZE)
  const y = clamp(crop.y, 0, 1 - MIN_CROP_SIZE)
  return {
    x,
    y,
    width: clamp(crop.width, MIN_CROP_SIZE, 1 - x),
    height: clamp(crop.height, MIN_CROP_SIZE, 1 - y),
  }
}

/** Compose a crop rectangle expressed in the currently displayed image into the source crop. */
export function composeCrop(current: CropRect | null, rect: CropRect): CropRect {
  const base = current ?? DEFAULT_CROP
  const local = clampCrop(rect)
  return clampCrop({
    x: base.x + local.x * base.width,
    y: base.y + local.y * base.height,
    width: local.width * base.width,
    height: local.height * base.height,
  })
}

/** A drag inside an item, in local displayed pixels, normalized to the displayed image. */
export function normalizeDragRect(start: { x: number; y: number }, end: { x: number; y: number },
  width: number, height: number): CropRect | null {
  if (!(width > 0) || !(height > 0)) return null
  const x1 = clamp(Math.min(start.x, end.x), 0, width)
  const y1 = clamp(Math.min(start.y, end.y), 0, height)
  const x2 = clamp(Math.max(start.x, end.x), 0, width)
  const y2 = clamp(Math.max(start.y, end.y), 0, height)
  const rect = { x: x1 / width, y: y1 / height, width: (x2 - x1) / width, height: (y2 - y1) / height }
  // Require a visible rectangle; a click without a drag must not silently crop the item.
  if (rect.width < 0.005 || rect.height < 0.005) return null
  return rect
}

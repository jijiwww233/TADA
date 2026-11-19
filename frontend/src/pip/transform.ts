/**
 * Convert the imperative state left behind by a Konva Transformer into the single scene
 * representation (x/y/width/height/rotation/...).
 *
 * Konva's Transformer mutates node.scaleX/scaleY during a drag, while react-konva only writes
 * attrs whose props changed since the previous render. If the leftover scale is not consumed
 * here, the next committed width/height is drawn multiplied by it — the image ends up bigger or
 * smaller "by a ring" after every transform (and crop resets look stretched too). So we bake the
 * scale into width/height and reset the node scale to 1 synchronously before React re-renders.
 */

/** Minimal imperative surface implemented by Konva.Node (the Transformer target). */
export interface TransformNode {
  width(): number
  height(): number
  scaleX(): number
  scaleY(): number
  x(): number
  y(): number
  rotation(): number
  setAttrs(attrs: { width: number; height: number; scaleX: number; scaleY: number }): unknown
}

export const MIN_ITEM_SIZE = 4

export interface TransformPatch {
  x: number
  y: number
  width: number
  height: number
  rotation: number
}

/**
 * Crop note: the canvas renders a pre-cropped texture, so node.width/height already describe the
 * visible cropped face and node.x/y its visible top-left. A crop offset must NOT be added again.
 */
export function commitTransform(node: TransformNode): TransformPatch {
  const width = Math.max(MIN_ITEM_SIZE, Math.abs(node.width() * node.scaleX()))
  const height = Math.max(MIN_ITEM_SIZE, Math.abs(node.height() * node.scaleY()))
  const patch: TransformPatch = {
    x: node.x(), y: node.y(), width, height, rotation: node.rotation(),
  }
  node.setAttrs({ width, height, scaleX: 1, scaleY: 1 })
  return patch
}

/**
 * Free scaling by default; hold Shift to keep the aspect ratio.
 * Konva's 'default' shift behavior toggles keepRatio while Shift is held, so keepRatio:false
 * plus shiftBehavior:'default' means: drag = free stretch, Shift+drag = proportional.
 * flipEnabled is off because mirroring is owned by the scene flip_x flag (negative node scales
 * would break the width/height representation this board persists).
 */
export const TRANSFORMER_SCALE_PROPS = {
  keepRatio: false,
  shiftBehavior: 'default',
  flipEnabled: false,
} as const

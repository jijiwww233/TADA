import type { DragEvent } from 'react'

/** Custom MIME only: the PiP board never accepts dropped URLs or files from outside. */
export const IMAGE_DRAG_MIME = 'application/x-wominmin-image'

export function startImageDrag(event: DragEvent<HTMLElement>, imageId: number): boolean {
  if (!Number.isSafeInteger(imageId) || imageId <= 0) return false
  const transfer = event.dataTransfer
  if (!transfer) return false
  transfer.setData(IMAGE_DRAG_MIME, String(imageId))
  transfer.effectAllowed = 'copy'
  return true
}

export function readDraggedImageId(transfer: Pick<DataTransfer, 'getData'> | null | undefined): number | null {
  if (!transfer) return null
  let raw = ''
  try {
    raw = transfer.getData(IMAGE_DRAG_MIME)
  } catch {
    return null
  }
  if (!/^[1-9][0-9]{0,9}$/.test(raw)) return null
  const value = Number(raw)
  return Number.isSafeInteger(value) && value > 0 ? value : null
}

export function hasImageDragType(transfer: Pick<DataTransfer, 'types'> | null | undefined): boolean {
  if (!transfer) return false
  return Array.from(transfer.types).includes(IMAGE_DRAG_MIME)
}

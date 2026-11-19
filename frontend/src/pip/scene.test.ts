import { describe, expect, it } from 'vitest'
import {
  addImages, addNote, bringToFront, cloneScene, compactScene, cropFromDrag, cropItem, emptyScene, LEGACY_NOTE_PLACEHOLDER,
  normalizeNotePlaceholders, noteDisplayText, NOTE_PLACEHOLDER, raiseItems, resetItem,
  fitSceneView, MAX_ITEMS, MAX_NOTES, parseScene, removeItem, removeNote, resetCrop, sceneBounds,
  sendToBack, setAllOpacity, setView, toggleFlag, toWireScene, updateItem, updateNote, visibleItems,
  type PipItem, type PipScene,
} from './scene'
import { DEFAULT_CROP, fitView, itemAABB, normalizeDragRect, rotateOffset, zoomAround } from './geometry'

const item = (patch: Partial<PipItem> = {}): PipItem => ({
  image_id: 1, x: 100, y: 50, width: 200, height: 100, rotation: 0,
  flip_x: false, grayscale: false, opacity: 1, crop: null, ...patch,
})
const scene = (items: PipItem[] = [item()], notes: PipScene['notes'] = []): PipScene =>
  ({ version: 1, view: { x: 0, y: 0, scale: 1 }, items, notes })

describe('geometry', () => {
  it('rotates offsets and keeps flipped layout stable', () => {
    expect(rotateOffset(10, 0, 90)).toEqual({ x: expect.closeTo(0, 10), y: expect.closeTo(10, 10) })
    expect(rotateOffset(10, 0, 180)).toEqual({ x: expect.closeTo(-10, 10), y: expect.closeTo(0, 10) })
  })
  it('computes the axis-aligned bounds of a rotated item', () => {
    const box = itemAABB({ ...item(), rotation: 90 })
    expect(box.width).toBeCloseTo(100)
    expect(box.height).toBeCloseTo(200)
    expect(box.x).toBeCloseTo(0)
    expect(box.y).toBeCloseTo(50)
  })
  it('fits a scene into the viewport and zooms around the pointer', () => {
    const view = fitView({ x: 0, y: 0, width: 1000, height: 500 }, { width: 500, height: 500 }, 0)
    expect(view.scale).toBeCloseTo(0.5)
    const zoomed = zoomAround(view, { x: 250, y: 250 }, 2)
    expect(zoomed.scale).toBeCloseTo(1)
    expect(zoomed.x).toBeCloseTo(-250)
    expect(zoomed.y).toBeCloseTo(0)
  })
  it('rejects a click as a crop rectangle', () => {
    expect(normalizeDragRect({ x: 10, y: 10 }, { x: 10, y: 10 }, 100, 100)).toBeNull()
    const rect = normalizeDragRect({ x: 10, y: 20 }, { x: 60, y: 80 }, 100, 100)!
    expect(rect).toEqual({ x: 0.1, y: 0.2, width: 0.5, height: 0.6 })
  })
})

describe('scene operations', () => {
  it('adds new images two per row below existing ones and skips duplicates', () => {
    const one = addImages(emptyScene(), [{ image_id: 1, width: 400, height: 800 }])
    expect(one.items).toHaveLength(1)
    expect(one.items[0].width).toBeCloseTo(150)
    expect(one.items[0].height).toBeCloseTo(300)
    const more = addImages(one, [
      { image_id: 2, width: 300, height: 300 }, { image_id: 3, width: 300, height: 300 }, { image_id: 1, width: 1, height: 1 },
    ])
    expect(more.items.map(entry => entry.image_id)).toEqual([1, 2, 3])
    expect(more.items[1].y).toBeCloseTo(320)
    expect(more.items[1].x).toBe(0)
    expect(more.items[2].x).toBeCloseTo(320)
    expect(addImages(more, [{ image_id: 1, width: 1, height: 1 }])).toBe(more)
  })
  it('clamps item edits and keeps immutability', () => {
    const before = scene()
    const next = updateItem(before, 1, { opacity: 4, width: -5 })
    expect(next).not.toBe(before)
    expect(next.items[0].opacity).toBe(1)
    expect(next.items[0].width).toBe(1)
    expect(updateItem(before, 99, { opacity: 0.5 })).toBe(before)
  })
  it('removes, reorders and toggles items', () => {
    const two = scene([item({ image_id: 1 }), item({ image_id: 2 })])
    expect(removeItem(two, 1).items.map(entry => entry.image_id)).toEqual([2])
    expect(bringToFront(two, 1).items.map(entry => entry.image_id)).toEqual([2, 1])
    expect(sendToBack(two, 2).items.map(entry => entry.image_id)).toEqual([2, 1])
    const flipped = toggleFlag(two, 1, 'flip_x')
    expect(flipped.items[0].flip_x).toBe(true)
    expect(toggleFlag(flipped, 1, 'flip_x').items[0].flip_x).toBe(false)
  })
  it('sets the same content opacity for every item', () => {
    const two = scene([item({ image_id: 1 }), item({ image_id: 2, opacity: 0.5 })])
    expect(setAllOpacity(two, 0.4).items.map(entry => entry.opacity)).toEqual([0.4, 0.4])
    expect(setAllOpacity(two, 2)).not.toBe(two)
    expect(setAllOpacity(two, 1).items[1].opacity).toBe(1)
  })
  it('crops without destroying the source and can restore it', () => {
    const before = scene([item({ x: 100, y: 50, width: 200, height: 100 })])
    const cropped = cropItem(before, 1, { x: 0.25, y: 0.5, width: 0.5, height: 0.5 })
    expect(cropped.items[0]).toMatchObject({ x: 150, y: 100, width: 100, height: 50, crop: { x: 0.25, y: 0.5, width: 0.5, height: 0.5 } })
    const restored = resetCrop(cropped, 1)
    expect(restored.items[0].x).toBeCloseTo(100)
    expect(restored.items[0].y).toBeCloseTo(50)
    expect(restored.items[0].width).toBeCloseTo(200)
    expect(restored.items[0].height).toBeCloseTo(100)
    expect(restored.items[0].crop).toBeNull()
  })
  it('composes crops and accounts for rotation', () => {
    const rotated = scene([item({ rotation: 90, x: 0, y: 0, width: 200, height: 100 })])
    const cropped = cropItem(rotated, 1, { x: 0.5, y: 0, width: 0.5, height: 1 })
    expect(cropped.items[0].width).toBeCloseTo(100)
    expect(cropped.items[0].height).toBeCloseTo(100)
    expect(cropped.items[0].x).toBeCloseTo(0)
    expect(cropped.items[0].y).toBeCloseTo(100)
    const again = cropItem(cropped, 1, { x: 0, y: 0.5, width: 1, height: 0.5 })
    expect(again.items[0].crop).toMatchObject({ x: 0.5, y: 0.5, width: 0.5, height: 0.5 })
    const restored = resetCrop(again, 1)
    expect(restored.items[0].width).toBeCloseTo(200)
    expect(restored.items[0].height).toBeCloseTo(100)
    expect(restored.items[0].x).toBeCloseTo(0)
    expect(restored.items[0].y).toBeCloseTo(0)
  })
  it('cropFromDrag uses local displayed pixels', () => {
    const rect = cropFromDrag(item(), { x: 0, y: 0 }, { x: 100, y: 50 })
    expect(rect).toEqual({ x: 0, y: 0, width: 0.5, height: 0.5 })
  })
  it('packs 100 items with potpack without overlapping AABBs', () => {
    const many = scene(Array.from({ length: 100 }, (_, index) => item({ image_id: index + 1, width: 80 + (index % 7) * 30, height: 60 + (index % 5) * 20 })))
    const packed = compactScene(many)
    expect(packed.items).toHaveLength(100)
    const boxes = packed.items.map(itemAABB)
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i], b = boxes[j]
        const overlap = a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
        expect(overlap).toBe(false)
      }
    }
    expect(sceneBounds(packed)!.width).toBeLessThan(3000)
  })
  it('adds and edits notes', () => {
    const withNote = addNote(emptyScene(), 10, 20)
    expect(withNote.notes[0]).toMatchObject({ id: 'note-1', x: 10, y: 20, text: '', font_size: 16 })
    expect(noteDisplayText(withNote.notes[0])).toBe(NOTE_PLACEHOLDER)
    const edited = updateNote(withNote, 'note-1', { text: '构图参考', font_size: 500 })
    expect(edited.notes[0]).toMatchObject({ text: '构图参考', font_size: 200 })
    expect(removeNote(edited, 'note-1').notes).toHaveLength(0)
    const many = scene([], Array.from({ length: MAX_NOTES }, (_, index) => ({ id: 'note-' + index, x: 0, y: 0, text: '', color: '#ffffff', font_size: 16 })))
    expect(addNote(many, 0, 0)).toBe(many)
  })
  it('culls off-screen items for 100-image boards', () => {
    const many = scene(Array.from({ length: 100 }, (_, index) => item({ image_id: index + 1, x: (index % 10) * 400, y: Math.floor(index / 10) * 400 })))
    const visible = visibleItems(many, { x: 0, y: 0, scale: 1 }, { width: 800, height: 800 }, 0)
    expect(visible.length).toBeGreaterThan(0)
    expect(visible.length).toBeLessThan(100)
    expect(visible.every(entry => entry.x < 1000 && entry.y < 1000)).toBe(true)
  })
  it('fits the whole scene', () => {
    const view = fitSceneView(scene([item({ x: 0, y: 0 })]), { width: 500, height: 500 }, 0)
    expect(view.scale).toBeCloseTo(2.5)
    expect(setView(scene(), view).view).toEqual(view)
  })
})

describe('scene validation', () => {
  const valid = toWireScene(scene([item()], [{ id: 'n1', x: 0, y: 0, text: '字', color: '#112233', font_size: 16 }]))
  it('round-trips a wire scene', () => {
    expect(parseScene(JSON.parse(JSON.stringify(valid)))).toEqual(valid)
    expect(cloneScene(valid)).toEqual(valid)
  })
  it.each([
    ['unknown version', { ...valid, version: 2 }],
    ['extra top-level key', { ...valid, extra: true }],
    ['missing view', (({ view: _view, ...rest }) => rest)(valid)],
    ['bool as number', { ...valid, items: [{ ...valid.items[0], width: true }] }],
    ['nan', { ...valid, items: [{ ...valid.items[0], x: Number.NaN }] }],
    ['numeric string', { ...valid, items: [{ ...valid.items[0], x: '10' }] }],
    ['extra item key', { ...valid, items: [{ ...valid.items[0], z: 1 }] }],
    ['opacity over 1', { ...valid, items: [{ ...valid.items[0], opacity: 1.1 }] }],
    ['crop past edge', { ...valid, items: [{ ...valid.items[0], crop: { x: 0.5, y: 0, width: 0.6, height: 1 } }] }],
    ['duplicate image', { ...valid, items: [valid.items[0], valid.items[0]] }],
    ['too many items', { ...valid, items: Array.from({ length: MAX_ITEMS + 1 }, (_, index) => ({ ...valid.items[0], image_id: index + 1 })) }],
    ['duplicate note id', { ...valid, notes: [valid.notes[0], valid.notes[0]] }],
    ['empty note id', { ...valid, notes: [{ ...valid.notes[0], id: '' }] }],
    ['bad note color', { ...valid, notes: [{ ...valid.notes[0], color: 'red' }] }],
    ['long note', { ...valid, notes: [{ ...valid.notes[0], text: 'x'.repeat(2001) }] }],
    ['bad view scale', { ...valid, view: { x: 0, y: 0, scale: 0 } }],
  ])('rejects %s', (_name, value) => {
    expect(() => parseScene(value)).toThrow()
  })
  it('accepts null crop and empty notes', () => {
    const wire = toWireScene(scene([item({ crop: DEFAULT_CROP })]))
    expect(wire.items[0].crop).toEqual(DEFAULT_CROP)
  })
})
describe('z-order', () => {
  it('raises a group to the top while preserving the group and the rest order', () => {
    const three = scene([item({ image_id: 1 }), item({ image_id: 2 }), item({ image_id: 3 })])
    expect(raiseItems(three, [1, 2]).items.map(entry => entry.image_id)).toEqual([3, 1, 2])
    // Relative order inside the raised group follows the current scene order (1 below 3 -> 1 then 3).
    expect(raiseItems(three, [3, 1]).items.map(entry => entry.image_id)).toEqual([2, 1, 3])
    expect(raiseItems(three, [2, 3]).items.map(entry => entry.image_id)).toEqual([1, 2, 3])
    expect(raiseItems(three, [3]).items.map(entry => entry.image_id)).toEqual([1, 2, 3])
  })

  it('keeps identity for no-ops and unknown ids', () => {
    const three = scene([item({ image_id: 1 }), item({ image_id: 2 }), item({ image_id: 3 })])
    expect(raiseItems(three, [])).toBe(three)
    expect(raiseItems(three, [99])).toBe(three)
    expect(raiseItems(three, [2, 3])).toBe(three)
    // bringToFront stays a single-image shortcut for the existing toolbar button.
    expect(bringToFront(three, 1).items.map(entry => entry.image_id)).toEqual([2, 3, 1])
  })
})
describe('reset item', () => {
  const natural = { width: 400, height: 300 }

  it('returns a transformed item to its freshly dropped state around the same center', () => {
    const transformed = scene([item({
      x: 120, y: 80, width: 450, height: 120, rotation: 30,
      flip_x: true, grayscale: true, opacity: 0.4, crop: { x: 0.2, y: 0.1, width: 0.5, height: 0.6 },
    })])
    const before = itemAABB(transformed.items[0])
    const result = resetItem(transformed, 1, natural).items[0]
    expect(result).toMatchObject({
      width: 300, height: 225, rotation: 0, flip_x: false, grayscale: false, opacity: 1, crop: null,
    })
    // Same visual center: only the size/orientation/effects are reset, not the placement.
    const after = itemAABB(result)
    expect(after.x + after.width / 2).toBeCloseTo(before.x + before.width / 2, 9)
    expect(after.y + after.height / 2).toBeCloseTo(before.y + before.height / 2, 9)
  })

  it('derives the original aspect ratio from the crop when no natural size is known', () => {
    const cropped = scene([item({ width: 150, height: 100, crop: { x: 0.5, y: 0, width: 0.5, height: 1 } })])
    const result = resetItem(cropped, 1).items[0]
    expect(result.width).toBeCloseTo(300)
    expect(result.height).toBeCloseTo(100)
    expect(result.crop).toBeNull()
  })

  it('ignores unknown ids and invalid natural sizes', () => {
    const one = scene([item()])
    expect(resetItem(one, 99, natural)).toBe(one)
    // item() is 200x100, so the default 300px long edge gives 300x150.
    const fallback = resetItem(one, 1, { width: 0, height: Number.NaN }).items[0]
    expect(fallback.width).toBeCloseTo(300)
    expect(fallback.height).toBeCloseTo(150)
  })
})
describe('note placeholders', () => {
  it('keeps empty notes empty and only displays a placeholder', () => {
    const withNote = addNote(emptyScene(), 10, 20)
    expect(withNote.notes[0].text).toBe('')
    expect(noteDisplayText(withNote.notes[0])).toBe(NOTE_PLACEHOLDER)
    const typed = updateNote(withNote, 'note-1', { text: '留白' })
    expect(noteDisplayText(typed.notes[0])).toBe('留白')
  })

  it('normalizes the old placeholder text saved by earlier versions', () => {
    const stale = scene([], [{ id: 'note-1', x: 0, y: 0, text: LEGACY_NOTE_PLACEHOLDER, color: '#f5d76e', font_size: 16 }])
    const normalized = normalizeNotePlaceholders(stale)
    expect(normalized.notes[0].text).toBe('')
    expect(noteDisplayText(normalized.notes[0])).toBe(NOTE_PLACEHOLDER)
    expect(normalizeNotePlaceholders(normalized)).toBe(normalized)
  })
})
describe('flip-aware crops', () => {
  it('mirrors the selected display x into the source when the item is flipped', () => {
    const flipped = scene([item({ x: 100, y: 50, width: 200, height: 100, flip_x: true })])
    const left = cropItem(flipped, 1, { x: 0, y: 0, width: 0.5, height: 1 }).items[0]
    // The left half of the screen is the right half of the source after flip_x.
    expect(left).toMatchObject({ width: 100, height: 100, x: 100, y: 50,
      crop: { x: 0.5, y: 0, width: 0.5, height: 1 } })
    const right = cropItem(flipped, 1, { x: 0.5, y: 0, width: 0.5, height: 1 }).items[0]
    expect(right).toMatchObject({ width: 100, height: 100, x: 200, y: 50,
      crop: { x: 0, y: 0, width: 0.5, height: 1 } })
  })

  it('composes a second crop in the flipped source coordinates', () => {
    const already = scene([item({ x: 0, y: 0, width: 100, height: 100, flip_x: true,
      crop: { x: 0.25, y: 0, width: 0.5, height: 1 } })])
    const next = cropItem(already, 1, { x: 0, y: 0, width: 0.25, height: 1 }).items[0]
    expect(next.crop).toMatchObject({ x: 0.625, y: 0, width: 0.125, height: 1 })
    expect(next.width).toBeCloseTo(25)
    expect(next.height).toBeCloseTo(100)
    // The display-space left edge is where the user dragged it.
    expect(next.x).toBeCloseTo(0)
  })

  it('restores a flipped crop from its mirrored display origin', () => {
    const cropped = scene([item({ x: 150, y: 50, width: 100, height: 100, flip_x: true,
      crop: { x: 0.1, y: 0, width: 0.5, height: 1 } })])
    const restored = resetCrop(cropped, 1).items[0]
    // full width 200, display origin = 1 - 0.1 - 0.5 = 0.4 -> 80
    expect(restored.x).toBeCloseTo(70)
    expect(restored.y).toBeCloseTo(50)
    expect(restored.width).toBeCloseTo(200)
    expect(restored.crop).toBeNull()
  })

  it('applies the mirrored origin through rotation', () => {
    const cropped = scene([item({ x: 10, y: 20, width: 100, height: 100, rotation: 90, flip_x: true,
      crop: { x: 0.1, y: 0, width: 0.5, height: 1 } })])
    const restored = resetCrop(cropped, 1).items[0]
    // offset = rotate(80, 0, 90) = (0, 80)
    expect(restored.x).toBeCloseTo(10)
    expect(restored.y).toBeCloseTo(-60)
  })

  it('rotates the display-space offset for a flipped right-half crop', () => {
    const rotated = scene([item({ x: 0, y: 0, width: 200, height: 100, rotation: 90, flip_x: true })])
    const right = cropItem(rotated, 1, { x: 0.5, y: 0, width: 0.5, height: 1 }).items[0]
    expect(right.x).toBeCloseTo(0)
    expect(right.y).toBeCloseTo(100)
    expect(right.crop).toMatchObject({ x: 0, width: 0.5 })
  })
})

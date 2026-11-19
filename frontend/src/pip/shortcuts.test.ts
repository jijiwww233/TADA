import { describe, expect, it } from 'vitest'
import { isTextEntryTarget, mapPipShortcut } from './shortcuts'

const event = (patch: Partial<{ key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean }>) => ({
  key: 'z', ...patch,
})

describe('shortcut mapping', () => {
  it('maps undo and redo variants', () => {
    expect(mapPipShortcut(event({ key: 'z', ctrlKey: true }))).toBe('undo')
    expect(mapPipShortcut(event({ key: 'Z', ctrlKey: true, shiftKey: true }))).toBe('redo')
    expect(mapPipShortcut(event({ key: 'y', ctrlKey: true }))).toBe('redo')
    expect(mapPipShortcut(event({ key: 'Y', ctrlKey: true, shiftKey: true }))).toBeNull()
    expect(mapPipShortcut(event({ key: 'z', ctrlKey: true, altKey: true }))).toBeNull()
    expect(mapPipShortcut(event({ key: 'z' }))).toBeNull()
    expect(mapPipShortcut(event({ key: 'z', metaKey: true }))).toBe('undo')
  })

  it('maps board actions', () => {
    expect(mapPipShortcut(event({ key: 'Delete' }))).toBe('delete')
    expect(mapPipShortcut(event({ key: 'Backspace' }))).toBe('delete')
    expect(mapPipShortcut(event({ key: 'a', ctrlKey: true }))).toBe('select-all')
    expect(mapPipShortcut(event({ key: 'A', ctrlKey: true, shiftKey: true }))).toBeNull()
    expect(mapPipShortcut(event({ key: 'f' }))).toBe('fit')
    expect(mapPipShortcut(event({ key: 'F' }))).toBe('fit')
    expect(mapPipShortcut(event({ key: 'g' }))).toBe('toggle-grayscale')
    expect(mapPipShortcut(event({ key: 'h' }))).toBe('hand-tool')
    expect(mapPipShortcut(event({ key: 'v' }))).toBe('select-tool')
    expect(mapPipShortcut(event({ key: '!', shiftKey: true }))).toBe('fit')
    expect(mapPipShortcut(event({ key: 'f', shiftKey: true }))).toBeNull()
    expect(mapPipShortcut(event({ key: 'f', ctrlKey: true }))).toBeNull()
    expect(mapPipShortcut(event({ key: 'x' }))).toBeNull()
  })

  it('detects text entry targets without relying on the main window realm', () => {
    expect(isTextEntryTarget({ tagName: 'TEXTAREA' } as unknown as EventTarget)).toBe(true)
    expect(isTextEntryTarget({ tagName: 'input' } as unknown as EventTarget)).toBe(true)
    expect(isTextEntryTarget({ tagName: 'div', isContentEditable: true } as unknown as EventTarget)).toBe(true)
    expect(isTextEntryTarget({ tagName: 'div' } as unknown as EventTarget)).toBe(false)
    expect(isTextEntryTarget(null)).toBe(false)
  })
})

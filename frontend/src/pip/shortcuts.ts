/** Keyboard shortcuts for the PiP board. Pure mapping so the rules stay unit-testable. */
export type PipShortcut =
  | 'undo'
  | 'redo'
  | 'delete'
  | 'select-all'
  | 'fit'
  | 'toggle-grayscale'
  | 'select-tool'
  | 'hand-tool'

export interface ShortcutEvent {
  key: string
  code?: string
  ctrlKey?: boolean
  metaKey?: boolean
  shiftKey?: boolean
  altKey?: boolean
}

/** Text fields keep native editing shortcuts (Ctrl+Z must undo typing, not board edits). */
export function isTextEntryTarget(target: EventTarget | null): boolean {
  const element = target as { tagName?: unknown; isContentEditable?: unknown } | null
  if (!element || typeof element.tagName !== 'string') return false
  const tag = element.tagName.toUpperCase()
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return element.isContentEditable === true
}

export function mapPipShortcut(event: ShortcutEvent): PipShortcut | null {
  if (event.altKey) return null
  const modifier = event.ctrlKey === true || event.metaKey === true
  const key = event.key.toLowerCase()
  if (modifier) {
    if (key === 'z') return event.shiftKey ? 'redo' : 'undo'
    if (key === 'y' && !event.shiftKey) return 'redo'
    if (key === 'a' && !event.shiftKey) return 'select-all'
    return null
  }
  if (event.shiftKey) return event.code === 'Digit1' || key === '!' || key === '1' ? 'fit' : null
  if (key === 'delete' || key === 'backspace') return 'delete'
  if (key === 'f') return 'fit'
  if (key === 'g') return 'toggle-grayscale'
  if (key === 'v') return 'select-tool'
  if (key === 'h') return 'hand-tool'
  return null
}

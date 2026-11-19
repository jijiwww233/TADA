import { describe, expect, it } from 'vitest'
import {
  canRedo, canUndo, emptyHistory, HISTORY_LIMIT, recordHistory, redoHistory, undoHistory,
} from './history'

describe('history stacks', () => {
  it('starts empty and exposes both directions', () => {
    const empty = emptyHistory<string>()
    expect(canUndo(empty)).toBe(false)
    expect(canRedo(empty)).toBe(false)
    expect(undoHistory(empty, 'now')).toBeNull()
    expect(redoHistory(empty, 'now')).toBeNull()
  })

  it('undoes then redoes back to the same state', () => {
    const one = recordHistory(emptyHistory<string>(), 'a')
    expect(canUndo(one)).toBe(true)
    expect(canRedo(one)).toBe(false)
    const undone = undoHistory(one, 'b')!
    expect(undone.value).toBe('a')
    expect(canUndo(undone.history)).toBe(false)
    expect(canRedo(undone.history)).toBe(true)
    const redone = redoHistory(undone.history, 'a')!
    expect(redone.value).toBe('b')
    expect(canUndo(redone.history)).toBe(true)
    expect(canRedo(redone.history)).toBe(false)
  })

  it('a new edit discards the redo branch', () => {
    const one = recordHistory(emptyHistory<string>(), 'a')
    const undone = undoHistory(one, 'b')!
    expect(canRedo(undone.history)).toBe(true)
    const edited = recordHistory(undone.history, 'a')
    expect(canRedo(edited)).toBe(false)
    expect(edited.past).toEqual(['a'])
  })

  it('keeps only the newest 50 steps', () => {
    let history = emptyHistory<string>()
    for (let i = 0; i < HISTORY_LIMIT + 20; i += 1) history = recordHistory(history, 's' + i)
    expect(history.past).toHaveLength(HISTORY_LIMIT)
    expect(history.past[0]).toBe('s20')
    expect(history.past[HISTORY_LIMIT - 1]).toBe('s' + (HISTORY_LIMIT + 19))
    const undone = undoHistory(history, 'now')!
    expect(undone.value).toBe('s' + (HISTORY_LIMIT + 19))
  })

  it('caps the redo stack too', () => {
    let history = emptyHistory<string>()
    for (let i = 0; i < 5; i += 1) history = recordHistory(history, 's' + i)
    let current = 'current'
    for (let i = 0; i < 5; i += 1) {
      const step = undoHistory(history, current)!
      history = step.history
      current = step.value
    }
    expect(history.future).toHaveLength(5)
    expect(history.past).toHaveLength(0)
  })
})

/** Bounded in-memory undo/redo stacks. Only the board session keeps them; nothing is persisted. */
export interface History<T> {
  past: readonly T[]
  future: readonly T[]
}

export const HISTORY_LIMIT = 50

export function emptyHistory<T>(): History<T> {
  return { past: [], future: [] }
}

export function canUndo<T>(history: History<T>): boolean {
  return history.past.length > 0
}

export function canRedo<T>(history: History<T>): boolean {
  return history.future.length > 0
}

/** Record the state before a new edit: newest stays last and any redo branch is discarded. */
export function recordHistory<T>(history: History<T>, previous: T, limit = HISTORY_LIMIT): History<T> {
  const past = [...history.past, previous]
  return {
    past: past.length > limit ? past.slice(past.length - limit) : past,
    future: [],
  }
}

export interface HistoryStep<T> {
  history: History<T>
  value: T
}

export function undoHistory<T>(history: History<T>, current: T, limit = HISTORY_LIMIT): HistoryStep<T> | null {
  if (history.past.length === 0) return null
  const value = history.past[history.past.length - 1]
  const future = [...history.future, current]
  return {
    value,
    history: {
      past: history.past.slice(0, -1),
      future: future.length > limit ? future.slice(future.length - limit) : future,
    },
  }
}

export function redoHistory<T>(history: History<T>, current: T, limit = HISTORY_LIMIT): HistoryStep<T> | null {
  if (history.future.length === 0) return null
  const value = history.future[history.future.length - 1]
  const past = [...history.past, current]
  return {
    value,
    history: {
      past: past.length > limit ? past.slice(past.length - limit) : past,
      future: history.future.slice(0, -1),
    },
  }
}

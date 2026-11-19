import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ImportResult } from '../api'
import { useLocalImport } from './useLocalImport'

const makeFiles = (count: number) => Array.from({ length: count }, (_, index) =>
  new File(['x'], 'image-' + index + '.png', { type: 'image/png', lastModified: index }))

const importResult = (added = 0, duplicates = 0, failed: { original_name: string; reason: string }[] = []): ImportResult => ({
  post_id: null,
  added: Array.from({ length: added }, (_, index) => ({
    id: index + 1, original_name: 'a' + index + '.png', sha256: 'sha', url: '/images/a' + index,
  })),
  duplicates: Array.from({ length: duplicates }, (_, index) => ({
    original_name: 'd' + index + '.png', sha256: 'sha', existing_image_id: 100 + index,
  })),
  failed,
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe('useLocalImport', () => {
  it('uploads 8 files per batch and accumulates progress', async () => {
    const sizes: number[] = []
    const importBatch = vi.fn(async (files: File[]) => {
      sizes.push(files.length)
      return importResult(files.length)
    })
    const onImported = vi.fn()
    const { result } = renderHook(() => useLocalImport({ importBatch, onImported }))
    await act(async () => { await result.current.start(makeFiles(20)) })
    expect(sizes).toEqual([8, 8, 4])
    expect(result.current.status).toBe('done')
    expect(result.current.progress).toMatchObject({ total: 20, processed: 20, added: 20, duplicates: 0 })
    expect(onImported).toHaveBeenCalledTimes(3)
  })

  it('stops on a failed batch but keeps the successful batches', async () => {
    const importBatch = vi.fn()
      .mockResolvedValueOnce(importResult(2, 1, [{ original_name: 'bad.png', reason: 'HEIC 暂不支持' }]))
      .mockRejectedValueOnce(new Error('导入失败，请重试'))
    const onImported = vi.fn()
    const { result } = renderHook(() => useLocalImport({ importBatch, onImported }))
    await act(async () => { await result.current.start(makeFiles(12)) })
    expect(importBatch).toHaveBeenCalledTimes(2)
    expect(result.current.status).toBe('error')
    expect(result.current.error).toBe('导入失败，请重试')
    expect(result.current.progress.processed).toBe(12)
    expect(result.current.progress.added).toBe(2)
    expect(result.current.progress.duplicates).toBe(1)
    expect(result.current.progress.failed).toHaveLength(5)
    expect(onImported).toHaveBeenCalledTimes(1)
  })

  it('applies cancel after the current batch and never starts another one', async () => {
    const first = deferred<ImportResult>()
    const importBatch = vi.fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(async () => importResult(8))
    const onImported = vi.fn()
    const { result } = renderHook(() => useLocalImport({ importBatch, onImported }))
    let started: Promise<void> = Promise.resolve()
    act(() => { started = result.current.start(makeFiles(16)) })
    await waitFor(() => expect(importBatch).toHaveBeenCalledTimes(1))
    act(() => { result.current.cancel() })
    expect(result.current.cancelled).toBe(true)
    await act(async () => { first.resolve(importResult(8)); await started })
    expect(importBatch).toHaveBeenCalledTimes(1)
    expect(result.current.status).toBe('done')
    expect(result.current.progress.processed).toBe(8)
    expect(result.current.progress.added).toBe(8)
    expect(onImported).toHaveBeenCalledTimes(1)
  })

  it('refuses a second start while a run is active, and can be reset afterwards', async () => {
    const pending = deferred<ImportResult>()
    const importBatch = vi.fn(() => pending.promise)
    const { result } = renderHook(() => useLocalImport({ importBatch }))
    let first: Promise<void> = Promise.resolve()
    act(() => { first = result.current.start(makeFiles(8)) })
    await waitFor(() => expect(importBatch).toHaveBeenCalledTimes(1))
    await act(async () => { await result.current.start(makeFiles(3)) })
    expect(importBatch).toHaveBeenCalledTimes(1)
    await act(async () => { pending.resolve(importResult(8)); await first })
    expect(result.current.status).toBe('done')
    act(() => { result.current.reset() })
    expect(result.current.status).toBe('idle')
    expect(result.current.progress.total).toBe(0)
  })

  it('reports an empty selection as an error', async () => {
    const { result } = renderHook(() => useLocalImport({ importBatch: vi.fn() }))
    await act(async () => { await result.current.start([]) })
    expect(result.current.status).toBe('error')
    expect(result.current.error).toBe('没有可导入的图片文件')
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ImportResult } from '../api'
import LocalImportPanel from './LocalImportPanel'

const makeFile = (name: string) => new File(['x'], name, { type: 'image/png' })

const importResult = (added = 0, failed: { original_name: string; reason: string }[] = []): ImportResult => ({
  post_id: null,
  added: Array.from({ length: added }, (_, index) => ({
    id: index + 1, original_name: 'a' + index + '.png', sha256: 'sha', url: '/images/a' + index,
  })),
  duplicates: [],
  failed,
})

describe('LocalImportPanel', () => {
  it('renders both pickers and imports the selected supported images', async () => {
    const importBatch = vi.fn(async (files: File[]) => importResult(files.length))
    const onImported = vi.fn()
    render(<LocalImportPanel onImported={onImported} importBatch={importBatch} />)
    expect(screen.getByRole('button', { name: '选择图片' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '选择文件夹' })).toBeInTheDocument()
    expect(screen.getByText(/已导入的图片不会因为关闭页面而丢失/)).toBeInTheDocument()
    expect(screen.getByText(/HEIC 会自动转成 JPEG 入库/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('选择图片文件'), {
      target: { files: [makeFile('a.png'), makeFile('b.txt'), makeFile('c.JPG')] },
    })
    await waitFor(() => expect(importBatch).toHaveBeenCalledTimes(1))
    expect((importBatch.mock.calls[0] as unknown as [File[]])[0].map(file => file.name)).toEqual(['a.png', 'c.JPG'])
    await waitFor(() => expect(
      screen.getByText(/已处理 2\/2 张（新增 2，重复 0，失败 0）/),
    ).toBeInTheDocument())
    expect(onImported).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '2')
  })

  it('shows failure reasons and supports a webkitdirectory folder input', async () => {
    const importBatch = vi.fn(async () => importResult(0, [{ original_name: 'bad.heic', reason: 'HEIC 不支持' }]))
    render(<LocalImportPanel onImported={() => {}} importBatch={importBatch} />)
    const folder = screen.getByLabelText('选择文件夹')
    expect(folder).toHaveAttribute('webkitdirectory')
    fireEvent.change(folder, { target: { files: [makeFile('bad.heic')] } })
    await waitFor(() => expect(screen.getByText(/bad.heic：HEIC 不支持/)).toBeInTheDocument())
    expect(screen.getByText(/已处理 1\/1 张（新增 0，重复 0，失败 1）/)).toBeInTheDocument()
  })

  it('imports files dropped from the OS through the fallback file list', async () => {
    const importBatch = vi.fn(async (files: File[]) => importResult(files.length))
    render(<LocalImportPanel onImported={() => {}} importBatch={importBatch} />)
    fireEvent.drop(screen.getByTestId('local-import-dropzone'), {
      dataTransfer: { items: [], files: [makeFile('drop.png')] },
    })
    await waitFor(() => expect(importBatch).toHaveBeenCalledTimes(1))
    expect((importBatch.mock.calls[0] as unknown as [File[]])[0][0].name).toBe('drop.png')
  })
})
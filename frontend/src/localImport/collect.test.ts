import { describe, expect, it } from 'vitest'
import {
  collectDroppedFiles, collectFromEntries, collectInputFiles, filterImageFiles, isHeicName,
  type FsEntry,
} from './collect'

const makeFile = (name: string) => new File(['x'], name, { type: 'image/png', lastModified: 123 })

const fileEntry = (name: string, fullPath: string): FsEntry => ({
  isFile: true, isDirectory: false, name, fullPath,
  file: (success) => success(makeFile(name)),
})

const directoryEntry = (name: string, fullPath: string, pages: FsEntry[][]): FsEntry => {
  let page = 0
  return {
    isFile: false, isDirectory: true, name, fullPath,
    createReader: () => ({
      readEntries: (success) => {
        const next = pages[page] ?? []
        page += 1
        success(next)
      },
    }),
  }
}

describe('local import file collection', () => {
  it('filters to supported extensions case-insensitively', () => {
    const names = ['a.jpg', 'b.JPEG', 'c.png', 'd.webp', 'e.heic', 'f.HEIF', 'g.gif', 'h.txt', 'noext']
    expect(filterImageFiles(names.map(name => ({ name }))).map(item => item.name))
      .toEqual(['a.jpg', 'b.JPEG', 'c.png', 'd.webp', 'e.heic', 'f.HEIF'])
    expect(isHeicName('x.heic')).toBe(true)
    expect(isHeicName('x.heif')).toBe(true)
    expect(isHeicName('x.jpg')).toBe(false)
  })

  it('recursively walks fake folder entries and keeps relative paths', async () => {
    const root = directoryEntry('root', '/root', [[
      fileEntry('a.png', '/root/a.png'),
      directoryEntry('sub', '/root/sub', [[
        fileEntry('b.JPG', '/root/sub/b.JPG'),
        directoryEntry('empty', '/root/sub/empty', [[]]),
      ]]),
      fileEntry('note.txt', '/root/note.txt'),
    ], []])
    const collected = await collectFromEntries([root])
    expect(collected.map(item => item.path)).toEqual(['/root/a.png', '/root/sub/b.JPG'])
    expect(collected.map(item => item.file.name)).toEqual(['a.png', 'b.JPG'])
  })

  it('de-duplicates by full/relative path', async () => {
    const entries = [
      fileEntry('a.png', '/same/a.png'),
      directoryEntry('same', '/same', [[fileEntry('a.png', '/same/a.png')], []]),
    ]
    const collected = await collectFromEntries(entries)
    expect(collected).toHaveLength(1)
    expect(collected[0].path).toBe('/same/a.png')
  })

  it('collects dropped files through webkitGetAsEntry, then falls back to dataTransfer.files', async () => {
    const root = directoryEntry('drop', '/drop', [[fileEntry('x.png', '/drop/x.png')], []])
    const fromEntries = await collectDroppedFiles({
      items: [{ webkitGetAsEntry: () => root }],
      files: [],
    })
    expect(fromEntries.map(item => item.path)).toEqual(['/drop/x.png'])
    const fallback = await collectDroppedFiles({
      items: [],
      files: [makeFile('one.png'), makeFile('skip.txt'), makeFile('one.png')],
    })
    expect(fallback.map(item => item.path)).toEqual(['one.png'])
  })

  it('collects input files (including webkitRelativePath) without duplicates', () => {
    const withPath = Object.assign(makeFile('nested.png'), { webkitRelativePath: 'folder/nested.png' })
    const collected = collectInputFiles([withPath, makeFile('nested.png'), makeFile('bad.gif')])
    expect(collected.map(item => item.path)).toEqual(['folder/nested.png', 'nested.png'])
  })
})

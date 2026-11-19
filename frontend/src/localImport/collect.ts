import type { CollectedFile } from './contract'

export const IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif'] as const

export function extensionOf(name: string): string {
  const index = name.lastIndexOf('.')
  return index >= 0 ? name.slice(index + 1).toLowerCase() : ''
}

export function isImageName(name: string): boolean {
  return (IMAGE_EXTENSIONS as readonly string[]).includes(extensionOf(name))
}

export function isHeicName(name: string): boolean {
  const extension = extensionOf(name)
  return extension === 'heic' || extension === 'heif'
}

/** Keep only supported image files, preserving the input order. */
export function filterImageFiles<T extends { name: string }>(files: readonly T[]): T[] {
  return files.filter(file => isImageName(file.name))
}

/** Structure shared by FileSystemFileEntry / FileSystemDirectoryEntry (only what we use). */
export interface FsEntry {
  isFile: boolean
  isDirectory: boolean
  name: string
  fullPath?: string
  file?: (success: (file: File) => void, error?: (error: unknown) => void) => void
  createReader?: () => {
    readEntries: (success: (entries: FsEntry[]) => void, error?: (error: unknown) => void) => void
  }
}

export interface DropItem {
  webkitGetAsEntry?: () => FsEntry | null
  getAsFile?: () => File | null
}

export interface DropData {
  items?: ArrayLike<DropItem>
  files?: ArrayLike<File>
}

const normalizePath = (path: string) => path.replace(/\\/g, '/').replace(/\/{2,}/g, '/')

function readEntryFile(entry: FsEntry): Promise<File> {
  return new Promise<File>((resolve, reject) => {
    if (!entry.file) {
      reject(new Error(`无法读取文件：${entry.name}`))
      return
    }
    entry.file(resolve, reject)
  })
}

/** readEntries() returns a page of entries and must be called again until it returns []. */
async function readDirectoryEntries(entry: FsEntry): Promise<FsEntry[]> {
  const reader = entry.createReader?.()
  if (!reader) return []
  const all: FsEntry[] = []
  for (;;) {
    const batch = await new Promise<FsEntry[]>((resolve, reject) => reader.readEntries(resolve, reject))
    if (batch.length === 0) return all
    all.push(...batch)
  }
}

function pathOf(entry: FsEntry, parentPath: string): string {
  const raw = entry.fullPath && entry.fullPath.length > 0 ? entry.fullPath : parentPath + '/' + entry.name
  const normalized = normalizePath(raw)
  return normalized.startsWith('/') ? normalized : '/' + normalized
}

/**
 * Recursively collect supported image files from dropped FileSystemEntry objects.
 * Paths are used both for display and de-duplication (dropping the same folder twice,
 * or a folder plus one of its files, keeps only the first occurrence).
 */
export async function collectFromEntries(entries: readonly FsEntry[]): Promise<CollectedFile[]> {
  const collected: CollectedFile[] = []
  const seen = new Set<string>()
  const walk = async (entryList: readonly FsEntry[], parentPath: string): Promise<void> => {
    for (const entry of entryList) {
      const path = pathOf(entry, parentPath)
      if (entry.isDirectory) {
        await walk(await readDirectoryEntries(entry), path)
        continue
      }
      if (!entry.isFile || !isImageName(entry.name)) continue
      const key = path.toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      collected.push({ file: await readEntryFile(entry), path })
    }
  }
  await walk(entries, '')
  return collected
}

function fileNameOfPath(path: string): string {
  const parts = normalizePath(path).split('/')
  return parts[parts.length - 1] || path
}

/** Collect from a drop target; falls back to dataTransfer.files when entries are unavailable. */
export async function collectDroppedFiles(data: DropData): Promise<CollectedFile[]> {
  const items = data.items ? Array.from(data.items) : []
  if (items.length > 0) {
    const entries = items
      .map(item => item.webkitGetAsEntry?.() ?? null)
      .filter((entry): entry is FsEntry => entry !== null)
    if (entries.length > 0) return collectFromEntries(entries)
  }
  const collected: CollectedFile[] = []
  const seen = new Set<string>()
  for (const file of Array.from(data.files ?? [])) {
    if (!isImageName(file.name)) continue
    const path = normalizePath(file.name)
    const key = path.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    collected.push({ file, path })
  }
  return collected
}

/** Collect from a <input type="file"> FileList (webkitRelativePath is kept when present). */
export function collectInputFiles(files: ArrayLike<File>): CollectedFile[] {
  const collected: CollectedFile[] = []
  const seen = new Set<string>()
  for (const file of Array.from(files)) {
    if (!isImageName(file.name)) continue
    const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath
    const path = normalizePath(relative && relative.length > 0 ? relative : file.name)
    const key = path.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    collected.push({ file, path })
  }
  return collected
}

export { fileNameOfPath }

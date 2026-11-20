import { responseError } from './errors'
import type { LibraryCounts, LibraryImage } from './wiring'

export async function wiringJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) throw responseError(response)
  return response.json()
}

// 只把预取结果交给素材库的第一次读取。导入、整理后的刷新仍然读最新数据。
function warmableRead<T>(path: string) {
  let warmed: Promise<T> | undefined
  const fetchLatest = () => wiringJson<T>(path)
  return {
    preload() {
      if (warmed) return
      const request = fetchLatest()
      warmed = request
      void request.catch(() => {
        if (warmed === request) warmed = undefined
      })
    },
    read() {
      const request = warmed
      warmed = undefined
      return request ? request.catch(fetchLatest) : fetchLatest()
    },
    fetchLatest,
  }
}

export const libraryImages = warmableRead<LibraryImage[]>('/api/images?include_palette=true')
export const libraryCounts = warmableRead<LibraryCounts>('/api/stats/library')

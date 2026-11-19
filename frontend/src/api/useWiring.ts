import { useEffect, useMemo, useState } from 'react'
import { useLocalImport } from '../localImport/useLocalImport'
import type { UseLocalImportOptions } from '../localImport/useLocalImport'
import { createWiringApi } from './wiring'
import type { DemoData, WiringApi } from './wiring'

export function useWiringApi(demo: boolean, fixture?: DemoData) {
  return useMemo(() => createWiringApi(demo ? 'demo' : 'real', fixture), [demo, fixture])
}

type Resource<T> = { status: 'loading' } | { status: 'ready'; data: T } | { status: 'error'; error: unknown }

/**
 * Use a stable loader (module function or useCallback). Writes stay in event handlers.
 * enabled = false：先不发请求，状态一直是 loading（首屏图片出来之前，把不急的接口往后放）。
 */
export function useWiringResource<T>(api: WiringApi, load: (api: WiringApi) => Promise<T>, enabled = true) {
  const [revision, setRevision] = useState(0)
  const [result, setResult] = useState<{ api: WiringApi; load: typeof load; revision: number; value: Resource<T> }>()
  useEffect(() => {
    if (!enabled) return
    let live = true
    load(api).then(
      data => { if (live) setResult({ api, load, revision, value: { status: 'ready', data } }) },
      error => { if (live) setResult({ api, load, revision, value: { status: 'error', error } }) },
    )
    return () => { live = false }
  }, [api, load, revision, enabled])
  const value: Resource<T> = result?.api === api && result.load === load && result.revision === revision
    ? result.value : { status: 'loading' }
  return { value, reload: () => setRevision(current => current + 1) }
}

export function useWiringImport(api: WiringApi, options?: Omit<UseLocalImportOptions, 'importBatch'>) {
  const importer = useLocalImport({ ...options, importBatch: api.importLocalBatch })
  const { cancel } = importer
  useEffect(() => cancel, [api, cancel])
  return importer
}

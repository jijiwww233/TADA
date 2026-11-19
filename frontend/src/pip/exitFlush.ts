import { toWireScene, type PipScene } from './scene'

/** Chromium caps the total keepalive body at roughly 64 KiB; stay at the documented ceiling. */
export const KEEPALIVE_LIMIT_BYTES = 64 * 1024

export interface ExitFlushResult {
  /** Whether a request was actually started. */
  sent: boolean
  /** False when the scene was too large for keepalive and a plain fetch was used instead. */
  keepalive: boolean
  /** Serialized UTF-8 body size. */
  bytes: number
}

const encoder = typeof TextEncoder === 'function' ? new TextEncoder() : null

export function utf8ByteLength(text: string): number {
  return encoder ? encoder.encode(text).length : text.length
}

export interface ExitFlushOptions {
  projectId: number | null
  revision: number
  scene: PipScene
  fetcher?: typeof fetch
  limit?: number
}

const skipped = (bytes = 0): ExitFlushResult => ({ sent: false, keepalive: false, bytes })

/**
 * Last-chance save for pagehide / beforeunload / visibilitychange:hidden.
 *
 * Fire-and-forget: the response is deliberately ignored. In particular a 409 means some other
 * window saved first — the exit path must never overwrite it, and the CAS semantics stay exactly
 * the same as the regular save. Network failures are swallowed because throwing from an unload
 * handler only produces a useless console error.
 *
 * keepalive bodies are capped (~64 KiB in Chromium), so oversized scenes fall back to a plain
 * fetch — best effort by definition, never an exception.
 */
export function flushSceneOnExit(options: ExitFlushOptions): ExitFlushResult {
  const { projectId, revision, scene, fetcher = fetch, limit = KEEPALIVE_LIMIT_BYTES } = options
  if (projectId === null || !Number.isSafeInteger(projectId) || projectId <= 0) return skipped()
  if (!Number.isSafeInteger(revision) || revision < 0) return skipped()
  let body: string
  try {
    body = JSON.stringify({ revision, scene: toWireScene(scene) })
  } catch {
    return skipped()
  }
  const bytes = utf8ByteLength(body)
  const keepalive = bytes <= Math.max(0, limit)
  try {
    const request = fetcher('/api/projects/' + projectId + '/pip-scene', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body,
      ...(keepalive ? { keepalive: true } : {}),
    })
    // Never leak a rejected promise from the exit path.
    void Promise.resolve(request).catch(() => {})
  } catch {
    return { sent: false, keepalive, bytes }
  }
  return { sent: true, keepalive, bytes }
}

import { parseScene, type PipScene } from './scene'

export interface PipSceneSnapshot {
  revision: number
  scene: PipScene | null
}

export class PipSceneError extends Error {
  code: string
  status: number
  constructor(code: string, status = 0) {
    super(messageFor(code))
    this.name = 'PipSceneError'
    this.code = code
    this.status = status
  }
}

export class PipSceneConflict extends PipSceneError {
  currentRevision: number
  currentScene: PipScene | null
  constructor(currentRevision: number, currentScene: PipScene | null) {
    super('revision_conflict', 409)
    this.name = 'PipSceneConflict'
    this.currentRevision = currentRevision
    this.currentScene = currentScene
  }
}

const messages: Record<string, string> = {
  revision_conflict: '布局已在另一个窗口更新，本次修改尚未保存',
  invalid_scene: '布局数据格式不正确，未保存',
  invalid_stored_scene: '已保存的布局无法读取，请重置后再保存',
  project_not_found: '项目不存在，请刷新项目列表',
  image_not_found: '布局里有图片已不存在，未保存',
  pip_scene_store_unavailable: '布局服务暂不可用，请稍后重试',
  network: '网络请求失败，布局可能未保存',
  invalid_response: '布局接口返回格式不符，未保存',
  request_failed: '布局保存失败，请重试',
}
const messageFor = (code: string) => messages[code] ?? messages.request_failed

export interface PipSceneApi {
  get(projectId: number): Promise<PipSceneSnapshot>
  save(projectId: number, revision: number, scene: PipScene): Promise<PipSceneSnapshot>
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)
const revision = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const projectId = (value: number) => {
  if (!Number.isSafeInteger(value) || value <= 0) throw new PipSceneError('project_not_found', 404)
  return String(value)
}

function readSnapshot(body: unknown): PipSceneSnapshot {
  if (!isObject(body) || !revision(body.revision)) throw new PipSceneError('invalid_response', 0)
  let scene: PipScene | null
  try {
    scene = body.scene === null || body.scene === undefined ? null : parseScene(body.scene)
  } catch {
    throw new PipSceneError('invalid_response', 0)
  }
  return { revision: body.revision, scene }
}

function detailCode(body: unknown): string | null {
  if (!isObject(body) || !isObject(body.detail)) return null
  return typeof body.detail.code === 'string' ? body.detail.code : null
}

/** Real Day8 adapter: strict envelopes, no retries, never invents a revision. */
export function createPipSceneApi(fetcher: typeof fetch = (...args) => fetch(...args)): PipSceneApi {
  async function request(path: string, init?: RequestInit): Promise<unknown> {
    let response: Response
    try {
      response = await fetcher('/api/projects' + path, init)
    } catch {
      throw new PipSceneError('network', 0)
    }
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      throw new PipSceneError(response.ok ? 'invalid_response' : 'request_failed', response.status)
    }
    if (response.ok) return body
    if (response.status === 409) {
      if (isObject(body) && isObject(body.detail) && body.detail.code === 'revision_conflict') {
        const currentRevision = body.detail.current_revision
        if (!revision(currentRevision)) throw new PipSceneError('invalid_response', 409)
        let currentScene: PipScene | null = null
        try {
          currentScene = body.detail.current_scene == null ? null : parseScene(body.detail.current_scene)
        } catch {
          currentScene = null
        }
        throw new PipSceneConflict(currentRevision, currentScene)
      }
      throw new PipSceneError(detailCode(body) ?? 'request_failed', 409)
    }
    const code = detailCode(body)
    if (code && Object.hasOwn(messages, code)) throw new PipSceneError(code, response.status)
    throw new PipSceneError('request_failed', response.status)
  }
  return {
    async get(projectIdValue) {
      const body = await request('/' + projectId(projectIdValue) + '/pip-scene')
      return readSnapshot(body)
    },
    async save(projectIdValue, revisionValue, scene) {
      if (!revision(revisionValue)) throw new PipSceneError('invalid_scene', 422)
      let wire: PipScene
      try {
        wire = parseScene(JSON.parse(JSON.stringify(scene)))
      } catch {
        throw new PipSceneError('invalid_scene', 422)
      }
      const body = await request('/' + projectId(projectIdValue) + '/pip-scene', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision: revisionValue, scene: wire }),
      })
      const snapshot = readSnapshot(body)
      if (snapshot.scene === null) throw new PipSceneError('invalid_response', 0)
      return snapshot
    },
  }
}

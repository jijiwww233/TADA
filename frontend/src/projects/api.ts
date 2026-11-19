import type { Pin, PinsResponse, Project, ProjectApi, ProjectDetail, ProjectList } from './contract'

const messages: Record<string, string> = {
  project_not_found: '项目不存在，请刷新项目列表', image_not_found: '图片不存在',
  invalid_stored_purposes: '旧钉选用途需要人工检查，未覆盖原记录',
  invalid_project_or_pin_command: '项目或钉选参数无效', project_store_unavailable: '项目服务暂不可用，请重试',
  network: '网络请求失败，结果可能未知；请刷新确认后再重试',
  invalid_response: '项目接口返回格式不符，请刷新确认', request_failed: '项目请求失败，请重试',
}
export class ProjectApiError extends Error {
  code: string
  status: number
  constructor(code: string, status = 0) {
    const safeCode = Object.hasOwn(messages, code) ? code : 'request_failed'
    super(messages[safeCode]); this.code = safeCode; this.status = status
  }
}
export const projectErrorMessage = (error: unknown) => error instanceof ProjectApiError ? error.message : messages.request_failed
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(s => typeof s === 'string')
const id = (v: unknown) => typeof v === 'number' && Number.isSafeInteger(v) && v > 0
const project = (v: unknown): v is Project => object(v) && id(v.id) && typeof v.name === 'string' && typeof v.brief === 'string' && typeof v.created_at === 'string'
const pin = (v: unknown): v is Pin => object(v) && id(v.id) && id(v.project_id) && id(v.image_id) && strings(v.purposes)
  && typeof v.note === 'string' && typeof v.created_at === 'string' && typeof v.needs_purpose === 'boolean'
function images(v: unknown) {
  return Array.isArray(v) && v.every(i => object(i) && id(i.image_id) && typeof i.url === 'string'
    && (i.width === null || typeof i.width === 'number') && (i.height === null || typeof i.height === 'number')
    && (i.original_name === null || typeof i.original_name === 'string')
    && typeof i.created_at === 'string' && Array.isArray(i.sources) && i.sources.every(s => object(s) && id(s.post_id)
      && typeof s.platform === 'string' && Number.isSafeInteger(s.position) && (s.position as number) >= 0
      && (s.canonical_url === null || typeof s.canonical_url === 'string') && (s.title === null || typeof s.title === 'string')
      && (s.source_id === null || typeof s.source_id === 'string') && typeof s.is_video === 'boolean') && typeof i.is_video === 'boolean')
}
function expect<T>(body: unknown, valid: boolean): T {
  if (!valid) throw new ProjectApiError('invalid_response')
  return body as T
}
const positive = (value: number) => { if (!id(value)) throw new ProjectApiError('invalid_project_or_pin_command'); return String(value) }

/** Exact e27ffe3 routes/envelopes. No automatic retry, note defaults, or hidden pin. */
export function createProjectApi(fetcher: typeof fetch = (...args) => fetch(...args)): ProjectApi {
  async function request(path: string, method = 'GET', data?: unknown): Promise<unknown> {
    let response: Response
    try {
      response = await fetcher('/api/projects' + path, { method,
        ...(data === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }) })
    } catch { throw new ProjectApiError('network') }
    let body: unknown
    try { body = await response.json() } catch { throw new ProjectApiError(response.ok ? 'invalid_response' : 'request_failed', response.status) }
    if (!response.ok) {
      const code = object(body) && object(body.detail) && typeof body.detail.code === 'string' ? body.detail.code : 'request_failed'
      throw new ProjectApiError(code, response.status) // never return raw bodies/messages
    }
    return body
  }
  return {
    async listProjects() {
      const body = await request('')
      return expect<ProjectList>(body, object(body) && Array.isArray(body.items) && body.items.every(project) && strings(body.allowed_purposes))
    },
    async createProject(input) {
      const body = await request('', 'POST', { name: input.name, brief: input.brief })
      return expect<Project>(body, project(body))
    },
    async getProject(projectId) {
      const body = await request('/' + positive(projectId))
      return expect<ProjectDetail>(body, project(body) && body.id === projectId && object(body)
        && Array.isArray(body.pins) && body.pins.every(p => pin(p) && p.project_id === projectId)
        && Array.isArray(body.image_ids) && body.image_ids.every(id) && images(body.images) && strings(body.allowed_purposes))
    },
    async updateProject(projectId, input) {
      const body = await request('/' + positive(projectId), 'PATCH', { brief: input.brief })
      return expect<Project>(body, project(body) && body.id === projectId)
    },
    async listPins(projectId) {
      const body = await request('/' + positive(projectId) + '/pins')
      return expect<PinsResponse>(body, object(body) && Array.isArray(body.items) && body.items.every(p => pin(p) && p.project_id === projectId)
        && images(body.images) && strings(body.allowed_purposes))
    },
    async pinImage(projectId, imageId, purposes, note) {
      positive(imageId)
      const body = await request('/' + positive(projectId) + '/pins', 'POST', {
        image_id: imageId, purposes, ...(note === undefined ? {} : { note }),
      })
      return expect<Pin>(body, pin(body) && body.project_id === projectId && body.image_id === imageId)
    },
    async removePurpose(projectId, imageId, purpose) {
      const body = await request('/' + positive(projectId) + '/pins/' + positive(imageId) + '/purposes/' + encodeURIComponent(purpose), 'DELETE')
      return expect<{ pin: Pin | null }>(body, object(body) && (body.pin === null || pin(body.pin)
        && body.pin.project_id === projectId && body.pin.image_id === imageId)).pin
    },
  }
}

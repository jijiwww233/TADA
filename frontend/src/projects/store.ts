import type { Pin, Project, ProjectApi, ProjectImage } from './contract'
import { projectErrorMessage } from './api'

export type LoadState = { status: 'idle' | 'loading' | 'ok' | 'error'; error?: string }
export interface ProjectState {
  projects: Project[]; activeProjectId: number | null; pinsByProject: Record<number, Pin[]>
  allowedPurposes: string[]; listState: LoadState; detailState: LoadState; mutationState: LoadState
  busyPins: string[]; pinError: string | null
  /** Last successfully created project; seq increments on every creation so the same brief fires again. */
  lastCreated: { id: number; name: string; brief: string; seq: number } | null
}
const idle: LoadState = { status: 'idle' }
const failed = (e: unknown): LoadState => ({ status: 'error', error: projectErrorMessage(e) })
const metadata = (project: Project): Project => ({ id: project.id, name: project.name, brief: project.brief, created_at: project.created_at })
const upsert = (rows: Project[], project: Project) => rows.some(p => p.id === project.id)
  ? rows.map(p => p.id === project.id ? metadata(project) : p) : [...rows, metadata(project)]

export class ProjectStore {
  private state: ProjectState = { projects: [], activeProjectId: null, pinsByProject: {}, allowedPurposes: [],
    listState: idle, detailState: idle, mutationState: idle, busyPins: [], pinError: null, lastCreated: null }
  private listeners = new Set<() => void>()
  private listTicket = 0
  private detailTicket = 0
  private createdSeq = 0
  private versions: Record<number, number> = {}
  private api: ProjectApi
  private mergeImages: (images: readonly ProjectImage[]) => void
  constructor(api: ProjectApi, mergeImages: (images: readonly ProjectImage[]) => void) {
    this.api = api; this.mergeImages = mergeImages
  }
  snapshot = () => this.state
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn) } }
  private patch(update: Partial<ProjectState>) { this.state = { ...this.state, ...update }; this.listeners.forEach(fn => fn()) }
  private changed(id: number) { this.versions[id] = (this.versions[id] ?? 0) + 1 }
  async loadProjects() {
    const ticket = ++this.listTicket
    this.patch({ listState: { status: 'loading' } })
    try { const data = await this.api.listProjects()
      if (ticket === this.listTicket) this.patch({ projects: data.items.map(metadata), allowedPurposes: data.allowed_purposes, listState: { status: 'ok' } })
    } catch (e) { if (ticket === this.listTicket) this.patch({ listState: failed(e) }) }
  }
  async selectProject(id: number | null): Promise<void> {
    const ticket = ++this.detailTicket; const version = id === null ? 0 : this.versions[id] ?? 0
    this.patch({ activeProjectId: id, detailState: id === null ? idle : { status: 'loading' }, pinError: null })
    if (id === null) return
    try {
      const detail = await this.api.getProject(id)
      if (ticket !== this.detailTicket) return
      if (version !== (this.versions[id] ?? 0)) return this.selectProject(id)
      this.mergeImages(detail.images)
      const project = { id: detail.id, name: detail.name, brief: detail.brief, created_at: detail.created_at }
      this.patch({ projects: upsert(this.state.projects, project), allowedPurposes: detail.allowed_purposes,
        pinsByProject: { ...this.state.pinsByProject, [id]: detail.pins }, detailState: { status: 'ok' } })
    } catch (e) { if (ticket === this.detailTicket) this.patch({ detailState: failed(e) }) }
  }
  async refreshPins(): Promise<void> {
    const id = this.state.activeProjectId
    if (id === null) return
    const ticket = ++this.detailTicket; const version = this.versions[id] ?? 0
    this.patch({ detailState: { status: 'loading' } })
    try { const data = await this.api.listPins(id)
      if (ticket !== this.detailTicket) return
      if (version !== (this.versions[id] ?? 0)) return this.refreshPins()
      this.mergeImages(data.images)
      this.patch({ pinsByProject: { ...this.state.pinsByProject, [id]: data.items }, allowedPurposes: data.allowed_purposes, detailState: { status: 'ok' } })
    } catch (e) { if (ticket === this.detailTicket) this.patch({ detailState: failed(e) }) }
  }
  async createProject(name: string, brief: string) {
    if (!name.trim() || this.state.mutationState.status === 'loading') return
    const selection = this.detailTicket
    this.patch({ mutationState: { status: 'loading' } })
    try { const project = await this.api.createProject({ name, brief }); ++this.listTicket
      const seq = ++this.createdSeq
      this.patch({ projects: upsert(this.state.projects, project), listState: { status: 'ok' }, mutationState: { status: 'ok' },
        lastCreated: { id: project.id, name: project.name, brief: project.brief, seq } })
      if (selection === this.detailTicket) await this.selectProject(project.id)
    } catch (e) { this.patch({ mutationState: failed(e) }) }
  }
  async updateBrief(brief: string) {
    const id = this.state.activeProjectId
    if (id === null || this.state.mutationState.status === 'loading') return
    this.patch({ mutationState: { status: 'loading' } })
    try { const project = await this.api.updateProject(id, { brief }); ++this.listTicket; this.changed(id)
      this.patch({ projects: upsert(this.state.projects, project), listState: { status: 'ok' }, mutationState: { status: 'ok' } })
    } catch (e) { this.patch({ mutationState: this.state.activeProjectId === id ? failed(e) : idle }) }
  }
  private async mutate(imageId: number, action: (projectId: number) => Promise<Pin | null>) {
    const projectId = this.state.activeProjectId
    if (projectId === null || this.state.detailState.status !== 'ok') return
    const key = `${projectId}:${imageId}`
    if (this.state.busyPins.includes(key)) return
    this.patch({ busyPins: [...this.state.busyPins, key], pinError: null })
    try { const pin = await action(projectId); this.changed(projectId)
      const old = this.state.pinsByProject[projectId] ?? []
      const exists = old.some(p => p.image_id === imageId)
      this.patch({ pinsByProject: { ...this.state.pinsByProject, [projectId]: pin
        ? exists ? old.map(p => p.image_id === imageId ? pin : p) : [...old, pin]
        : old.filter(p => p.image_id !== imageId) } })
    } catch (e) { if (projectId === this.state.activeProjectId) this.patch({ pinError: projectErrorMessage(e) }) }
    finally { this.patch({ busyPins: this.state.busyPins.filter(k => k !== key) }) }
  }
  pinImage(imageId: number, purposes: string[], note?: string | null) {
    const unique = [...new Set(purposes)]
    if (!unique.length || unique.some(p => !this.state.allowedPurposes.includes(p))) {
      this.patch({ pinError: '请选择有效用途' }); return Promise.resolve()
    }
    return this.mutate(imageId, id => this.api.pinImage(id, imageId, unique, note))
  }
  removePurpose(imageId: number, purpose: string) {
    return this.mutate(imageId, id => this.api.removePurpose(id, imageId, purpose))
  }
}

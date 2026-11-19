import { useEffect, useSyncExternalStore, type ReactNode } from 'react'
import type { ImageItem } from '../api'
import type { ImageEntity } from '../search/contract'
import { PinControl, ProjectBriefEditor, ProjectFeedback, ProjectPins, ProjectSelector } from './components'
import { sendProjectPinsToPip } from './pip'
import type { ProjectStore } from './store'

/** Optional portable assembly, NOT mounted in App. Host owns shared image entities. */
export function ProjectsPanel({ store, imagesById, libraryIds, onOpen, onPip, projectExtras }: {
  store: ProjectStore; imagesById: Readonly<Record<number, ImageEntity>>; libraryIds: readonly number[]
  onOpen: (id: number) => void; onPip?: (images: ImageItem[]) => void
  projectExtras?: ReactNode
}) {
  const state = useSyncExternalStore(store.subscribe, store.snapshot)
  useEffect(() => { void store.loadProjects() }, [store])
  const project = state.projects.find(p => p.id === state.activeProjectId)
  return <section aria-label="项目管理">
    <ProjectSelector projects={state.projects} activeId={state.activeProjectId} state={state.listState}
      busy={state.mutationState.status === 'loading'} select={id => void store.selectProject(id)}
      create={(name, brief) => void store.createProject(name, brief)} retry={() => void store.loadProjects()} />
    {state.mutationState.status === 'loading' && <p role="status">保存项目中…</p>}
    {state.mutationState.status === 'error' && <p role="alert">{state.mutationState.error}</p>}
    <ProjectFeedback state={state.detailState} retry={() => void store.selectProject(state.activeProjectId)} />
    {project && state.detailState.status === 'ok' && <>
      <h2>{project.name}</h2><p>项目 #{project.id} · {project.created_at}</p>
      {projectExtras}
      <ProjectBriefEditor project={project} busy={state.mutationState.status === 'loading'} save={brief => void store.updateBrief(brief)} />
      <button type="button" onClick={() => void store.refreshPins()}>刷新钉选</button>
      <ProjectPins pins={state.pinsByProject[project.id] ?? []} imagesById={imagesById} busyKeys={state.busyPins} onOpen={onOpen}
        remove={(id, purpose) => void store.removePurpose(id, purpose)}
        onPip={onPip ? () => { sendProjectPinsToPip(store.snapshot(), imagesById, libraryIds, onPip) } : undefined} />
    </>}
    {state.pinError && <p role="alert">{state.pinError}</p>}
  </section>
}

/** Mount beside ImageDetail or RecommendationCard. Suggestions are never persisted on render. */
export function ProjectPinAction({ store, imageId, suggestedPurposes = [], manageExisting = false }: {
  store: ProjectStore; imageId: number; suggestedPurposes?: string[]; manageExisting?: boolean
}) {
  const state = useSyncExternalStore(store.subscribe, store.snapshot)
  const existing = state.activeProjectId === null ? undefined : state.pinsByProject[state.activeProjectId]?.find(p => p.image_id === imageId)
  return <div>
    <PinControl key={`${state.activeProjectId}:${imageId}:${state.allowedPurposes.join('|')}:${state.detailState.status === 'ok'}:${existing?.id ?? 'new'}:${existing?.purposes.join('|') ?? ''}`}
      imageId={imageId} allowed={state.allowedPurposes} suggested={existing?.purposes ?? suggestedPurposes} initialNote={existing?.note}
      enabled={state.activeProjectId !== null && state.detailState.status === 'ok'}
      busy={state.busyPins.includes(`${state.activeProjectId}:${imageId}`)} pin={(purposes, note) => void store.pinImage(imageId, purposes, note)} />
    {manageExisting && existing && state.detailState.status === 'ok' && <div>
      <p>当前项目用途（取消勾选不会删除已有用途，请使用移除按钮）：</p>
      <ul>{existing.purposes.map(purpose => <li key={purpose}>{purpose}{' '}
        <button type="button" aria-label={`移除详情用途 ${purpose} #${imageId}`}
          disabled={state.busyPins.includes(`${state.activeProjectId}:${imageId}`)}
          onClick={() => void store.removePurpose(imageId, purpose)}>移除此用途</button>
      </li>)}</ul>
    </div>}
    {state.pinError && <p role="alert">{state.pinError}</p>}
  </div>
}

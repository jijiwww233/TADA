import {streamRecommendations} from './recommendation/interactive'
import { loadReferences, isVisual, type ReferenceRow } from './ReferenceLabel'
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import AnalysisBar from './AnalysisBar'
import { listImages, type ImageItem, type ImageDetail as Detail } from './api'
import DropZone from './DropZone'
import ImportPanel from './ImportPanel'
import ImageDetail from './ImageDetail'
import ImageGrid from './ImageGrid'
import PipButton, { type PipHandle } from './PipButton'
import SyncBar from './SyncBar'
import SyncAll from './SyncAll'
import AutoSyncControl from './AutoSyncControl'
import WatchFolderPanel from './watch/WatchFolderPanel'
import { searchImages, type SearchApi } from './search/contract'
import { mergeHits, SearchStore } from './search/store'
import { findSimilar, type SimilarApi } from './search/similar'
import SearchPanel from './search/SearchPanel'
import { getRecommendations, type RecommendationApi } from './recommendation/contract'
import RecommendationPanel from './recommendation/RecommendationPanel'
import { RecommendationStore } from './recommendation/store'
import { createProjectApi } from './projects/api'
import type { ProjectApi } from './projects/contract'
import { ProjectStore } from './projects/store'
import { ProjectsPanel, ProjectPinAction } from './projects/integration'
import { ProjectReferenceFlow } from './projects/reference-flow'
import { projectPipIds } from './projects/pip'
import { ProjectVibe } from './vibe/ProjectVibe'
import { VibeSimilarityControls } from './vibe/VibeSimilarityControls'

function App({ searchApi = searchImages, libraryApi = listImages, detailLoader, recommendationApi = getRecommendations, projectApi, similarApi = findSimilar }: {
  similarApi?: SimilarApi
  searchApi?: SearchApi; libraryApi?: () => Promise<ImageItem[]>; detailLoader?: (id: number) => Promise<Detail>
  recommendationApi?: RecommendationApi
  projectApi?: ProjectApi
}) {
  const [store] = useState(() => new SearchStore(searchApi, libraryApi, similarApi))
  const [recommender] = useState(() => new RecommendationStore(recommendationApi, store.mergeImages,recommendationApi===getRecommendations?streamRecommendations:undefined))
  const [projectClient] = useState(() => projectApi ?? createProjectApi())
  const [projects] = useState(() => new ProjectStore(projectClient, incoming => store.mergeImages([...incoming])))
  const state = useSyncExternalStore(store.subscribe, store.snapshot)
  const projectState = useSyncExternalStore(projects.subscribe, projects.snapshot)
  const activeProject = projectState.projects.find(project => project.id === projectState.activeProjectId)
  const refreshVibePins = (projectId: number) => {
    if (projects.snapshot().activeProjectId === projectId) void projects.refreshPins()
  }
  const pipRef = useRef<PipHandle | null>(null)
  const [pipError, setPipError] = useState<string | null>(null)
  const pipImages = projectPipIds(projectState, state.libraryIds).map(id => state.imagesById[id]).filter(Boolean)
  const [references, setReferences] = useState<Record<number,ReferenceRow>>({})
  const [visualOnly,setVisualOnly] = useState(false)
  const [watchSettingsOpen, setWatchSettingsOpen] = useState(false)
  useEffect(()=>{loadReferences().then(setReferences).catch(()=>{})},[state.libraryIds])
  const images = state.libraryIds.map(id => state.imagesById[id]).filter(image=>!visualOnly||isVisual(references[image.id]))
  // Any non-idle search owns the result surface; the library only shows when not searching.
  const searching = state.searchState.status !== 'idle'
  const visibleIds = visualOnly ? new Set(Object.values(references).filter(isVisual).map(r => r.id)) : undefined
  const refresh = store.refresh
  const error = state.libraryState.error
  const [openId, setOpenId] = useState<number | null>(null)
  const openProjectPip = () => {
    if (!pipRef.current?.supported) { setPipError('当前浏览器不支持参考小窗'); return }
    setPipError(null)
    void pipRef.current.open().catch(() => setPipError('打开参考小窗失败，请重试'))
  }

  useEffect(() => {
    refresh()
  }, [refresh])

  return (
    <main>
      <header>
        <h1>我明明收藏过</h1>
        <span className="count">已收录 {images.length} 张</span>
        <PipButton ref={pipRef} images={pipImages} projectId={projectState.activeProjectId} projectName={activeProject?.name}
          imagesById={state.imagesById} libraryImages={state.libraryIds.map(id => state.imagesById[id]).filter(Boolean)}
          projects={projectState.projects}
          loadProject={async id => {
            const project = await projectClient.getProject(id)
            const imagesById = mergeHits({}, project.images)
            return { id, name: project.name, images: project.images.map(image => imagesById[image.image_id]) }
          }} />
      </header>
      <SyncAll />
      <SyncBar onSynced={refresh} />
      <SyncBar platform="huaban" onSynced={refresh} />
      <SyncBar platform="pinterest" onSynced={refresh} />
      <SyncBar platform="zcool" onSynced={refresh} />
      <AutoSyncControl />
      <details onToggle={event => setWatchSettingsOpen(event.currentTarget.open)}>
        <summary>设置 · 自动导入文件夹</summary>
        <div hidden={!watchSettingsOpen}>
          <WatchFolderPanel onImported={refresh} />
        </div>
      </details>
      <AnalysisBar refreshKey={images.length} />
      <p><a href="/reference-review">待确认批量页 / 智能分组</a></p>
      <DropZone onImported={refresh} />
      <ImportPanel onImported={refresh} />
      {error && <p className="msg error">{error}</p>}
      <details open><summary>项目与钉选</summary>
        <ProjectsPanel store={projects} imagesById={state.imagesById} libraryIds={state.libraryIds}
          onOpen={setOpenId} onPip={openProjectPip} projectExtras={activeProject &&
            <ProjectVibe key={activeProject.id} project={activeProject}
              pinned={new Set(projectState.pinsByProject[activeProject.id]?.map(pin => pin.image_id))}
              onOpen={setOpenId} onPinned={refreshVibePins} />} />
        <ProjectReferenceFlow store={projects} recommendationApi={recommendationApi} onOpen={setOpenId} onPip={openProjectPip} />
      </details>
      {pipError && <p role="alert">{pipError}</p>}
      <label><input type="checkbox" checked={visualOnly} onChange={e=>setVisualOnly(e.target.checked)} />只看视觉参考（含不确定）</label>
      <SearchPanel visibleIds={visibleIds} store={store} state={state} onOpen={setOpenId} similarityControls={
        <VibeSimilarityControls key={state.similarityImageId ?? state.searchLabel}
          imageId={state.similarityImageId} mode={state.similarityDimension ?? 'combined'}
          available={state.similarityDimensions}
          resultIds={visibleIds ? state.searchIds.filter(id => visibleIds.has(id)) : state.searchIds}
          loading={state.searchState.status === 'loading'} projects={projectState.projects}
          activeProjectId={projectState.activeProjectId} onPinned={refreshVibePins}
          onSelect={dimension => {
            if (state.similarityImageId !== undefined) void store.similar(state.similarityImageId, dimension)
          }} />
      } />
      <RecommendationPanel projectId={projectState.activeProjectId} store={recommender} imagesById={state.imagesById} onOpen={setOpenId}
        renderPinAction={item => <ProjectPinAction store={projects} imageId={item.image_id} suggestedPurposes={[item.purpose]} />} />
      {!searching && <div style={{display:'flex',gap:18}}><aside aria-label="素材智能分组" style={{width:130,flexShrink:0}}>{[['visual_ref','视觉参考'],['text_card','文字卡片'],['life','生活'],['pending','待确认']].map(([group,label])=><p key={group}><a href={`/reference-review?group=${group}`}>{label}</a></p>)}</aside><div style={{flex:1,minWidth:0}}><ImageGrid images={images} onOpen={setOpenId} /></div></div>}
      {openId !== null && <ImageDetail key={openId} loadImage={detailLoader} imageId={openId} onClose={() => setOpenId(null)}
        onOpen={setOpenId} onSimilar={id => { setOpenId(null); void store.similar(id); document.querySelector('.search-panel')?.scrollIntoView?.({ behavior: 'smooth' }) }}
        pinActions={<ProjectPinAction store={projects} imageId={openId} manageExisting />} />}
    </main>
  )
}

export default App
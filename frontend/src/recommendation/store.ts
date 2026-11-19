import {moreLike,saveFeedback,type StreamApi,type RecommendationOptions} from './interactive'
import type { SearchHit } from '../search/contract'
import type { RecommendationApi, RecommendationColumn } from './contract'
import { RecommendationError } from './contract'

export type RecommendationStatus = 'idle' | 'loading' | 'ok' | 'error' | 'index_empty'
export interface RecommendationState {
  status: RecommendationStatus; error?: string; busyColumn?:string;actionError?:string;feedbackMessage?:string
  usage?: import('./contract').RecommendationUsage;
  columns: RecommendationColumn[]; notices: string[]; effectiveQuery: string
}

/**
 * Recommendation state: three columns of IDs + reference points; image entities go to the
 * shared map through `merge`. Nothing here pins or saves: that is Day7.
 */
export class RecommendationStore {
  private state: RecommendationState = { status: 'idle', columns: [], notices: [], effectiveQuery: '' }
  private listeners = new Set<() => void>()
  private ticket = 0
  private pending: string | null = null
  private api: RecommendationApi
  private stream?: StreamApi
  private options:RecommendationOptions={}
  private negativeIds=new Set<number>()
  private lastBrief=''
  private previous?:string
  private includeNonvisual=false
  /** Project id whose result is on screen; null until a result arrives. */
  private resultProjectId:number|null=null
  configure=(options:Partial<RecommendationOptions>)=>{
    if('project_id' in options&&options.project_id!==this.options.project_id){
      // The visible columns stay (they are just image ids and the pin action may target the
      // new project), but any request still in flight belongs to the old project and must not
      // be applied; resultProjectId below blocks feedback on a result of the old project.
      this.negativeIds.clear();this.ticket++;this.pending=null
      // The invalidated request will never answer, so do not leave the panel spinning.
      if(this.state.status==='loading')this.set({...this.state,status:'idle'})
    }
    this.options={...this.options,...options}
  }
  private merge: (images: SearchHit[]) => void
  constructor(api: RecommendationApi, merge: (images: SearchHit[]) => void, stream?:StreamApi) { this.api = api; this.merge = merge;this.stream=stream }
  snapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private set(state: RecommendationState) { this.state = state; this.listeners.forEach(fn => fn()) }
  recommend = async (brief: string, previousBrief?: string, includeNonvisual = false) => {
    if(brief!==this.lastBrief)this.negativeIds.clear()
    this.lastBrief=brief;this.previous=previousBrief;this.includeNonvisual=includeNonvisual
    const previous = previousBrief?.trim() ? previousBrief : undefined
    // Requests differ by project and color conditions too: the same brief with a
    // different target project is a new request, never a duplicate in-flight one.
    const key = JSON.stringify([brief, previous ?? null, includeNonvisual,
      this.options.project_id ?? null, this.options.color_need ?? null])
    if (!brief.trim() || this.state.status === 'loading' && this.pending === key) return
    const ticket = ++this.ticket; this.pending = key
    this.set({ status: 'loading', columns: [], notices: [], effectiveQuery: '' })
    try {
      const result = this.stream ? await this.stream(brief,previous,includeNonvisual,partial=>{
        if(ticket!==this.ticket)return
        this.merge(partial.images)
        const incoming=new Map(this.state.columns.map(c=>[c.purpose,c]))
        for(const column of partial.columns)incoming.set(column.purpose,column)
        this.set({...this.state,status:'loading',effectiveQuery:partial.effective_query,columns:[...incoming.values()].sort((a,b)=>['layout','palette','style'].indexOf(a.purpose)-['layout','palette','style'].indexOf(b.purpose))})
      },{...this.options,negative_ids:[...this.negativeIds]}) : await (includeNonvisual ? this.api(brief, previous, true) : this.api(brief, previous))
      if (ticket !== this.ticket) return // a newer brief owns the surface
      this.merge(result.images)
      this.resultProjectId=this.options.project_id??null
      this.set({ status: result.status, columns: result.columns, notices: result.notices, effectiveQuery: result.effective_query, ...(result.usage ? {usage:result.usage} : {}) })
    } catch (error) {
      if (ticket !== this.ticket) return
      const message = error instanceof RecommendationError ? error.message : '推荐服务暂不可用，请重试'
      this.set({ status: 'error', error: message, columns: this.state.columns, notices: [], effectiveQuery: '' })
    }
  }
  exclude=(imageId:number)=>{
    this.negativeIds.add(imageId)
    this.set({...this.state,columns:this.state.columns.map(c=>({...c,items:c.items.filter(i=>i.image_id!==imageId)})),feedbackMessage:'本次已排除；后续召回会给相似图片轻微降分。'})

  }
  moreLike=async(imageId:number,purpose:import('./contract').Purpose)=>{
    if(this.state.status==='loading'||this.state.busyColumn)return
    const ticket=this.ticket
    this.set({...this.state,busyColumn:purpose,actionError:undefined})
    try{
      const result=await moreLike(this.lastBrief,purpose,imageId,{...this.options,negative_ids:[...this.negativeIds]},this.previous,this.includeNonvisual)
      if(ticket!==this.ticket)return
      // The request carried the exclusions known when it was sent. "不要这种" can be
      // clicked while it is in flight, so drop those ids again before showing the column.
      const column=result.columns[0]
      const kept=column.items.filter(item=>!this.negativeIds.has(item.image_id))
      const applied=kept.length===column.items.length?column:{...column,items:kept}
      this.merge(result.images.filter(hit=>!this.negativeIds.has(hit.image_id)))
      this.resultProjectId=this.options.project_id??null
      this.set({...this.state,busyColumn:undefined,columns:this.state.columns.map(c=>c.purpose===purpose?applied:c),usage:result.usage})
    }catch{if(ticket===this.ticket)this.set({...this.state,busyColumn:undefined,actionError:'该栏相似推荐暂不可用，请重试'})}
  }
  rate=async(imageId:number,purpose:import('./contract').Purpose,usable:boolean)=>{
    const project=this.options.project_id
    if(!project)return
    // Feedback is written into the current project: a column left over from the previous
    // project must not record it against this one.
    if(this.resultProjectId!==null&&this.resultProjectId!==project){
      this.set({...this.state,actionError:'这栏结果属于上一个项目，请先在当前项目重新获取推荐，再记录反馈。'})
      return
    }
    try{await saveFeedback(project,imageId,purpose,usable);this.set({...this.state,feedbackMessage:'已保存到当前项目，下次推荐会轻微参考此反馈。'})}
    catch{this.set({...this.state,actionError:'反馈保存失败，请重试'})}
  }

}

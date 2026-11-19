import {RecommendationError,type RecommendationResponse,type RecommendationColumn,type Purpose} from './contract'
import type {SearchHit} from '../search/contract'
export type ColorNeed={hex:string;role:'为主'|'点缀'}
export type RecommendationOptions={project_id?:number;color_need?:ColorNeed[];negative_ids?:number[]}
export type PartialRecommendation={columns:RecommendationColumn[];images:SearchHit[];effective_query:string;first_column_ms?:number}
export type StreamApi=(brief:string,previous:string|undefined,includeNonvisual:boolean,onPartial:(result:PartialRecommendation)=>void,options:RecommendationOptions)=>Promise<RecommendationResponse>
export const streamRecommendations:StreamApi=async(brief,previous,includeNonvisual,onPartial,options)=>{
  const response=await fetch('/api/recommendations/stream',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({brief,...(previous?{previous_brief:previous}:{}),include_nonvisual:includeNonvisual,...options})})
  if(!response.ok||!response.body)throw new RecommendationError()
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer=''
  try{
    for(;;){const {value,done}=await reader.read();buffer+=decoder.decode(value,{stream:!done});const lines=buffer.split('\n');buffer=lines.pop()??''
      for(const line of lines){if(!line.trim())continue;const event=JSON.parse(line)
        if(event.event==='column')onPartial(event.result)
        else if(event.event==='done')return event.result
        else if(event.event==='error')throw new RecommendationError()
      }
      if(done)throw new RecommendationError('推荐连接已中断，请重试')
    }
  }finally{reader.releaseLock()}
}
export async function moreLike(brief:string,purpose:Purpose,imageId:number,options:RecommendationOptions,previous?:string,includeNonvisual=false):Promise<RecommendationResponse>{
  const response=await fetch('/api/recommendations/more-like',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({brief,purpose,image_id:imageId,...options,previous_brief:previous,include_nonvisual:includeNonvisual})})
  if(!response.ok)throw new RecommendationError('这栏的相似推荐暂不可用，请重试')
  return response.json()
}
export async function saveFeedback(projectId:number,imageId:number,purpose:Purpose,usable:boolean){
  const response=await fetch(`/api/projects/${projectId}/recommendation-feedback`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_id:imageId,purpose,usable})})
  if(!response.ok)throw new RecommendationError('反馈保存失败，请重试')
}

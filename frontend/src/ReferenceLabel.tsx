import { useEffect, useState } from 'react'

export type ReferenceCategory = 'visual_ref' | 'text_card' | 'life' | 'uncertain'
export type ReferenceRow = { id:number; category:ReferenceCategory; auto_category:ReferenceCategory|null; manual_category:ReferenceCategory|null; confirmed:number; reason:string|null; manual_reference?:number|null }
export const referenceNames = { visual_ref:'视觉参考', text_card:'文字卡片', life:'生活类', uncertain:'不确定' }
export const isVisual = (row?:ReferenceRow) => !row || row.manual_reference!==0 && (row.category === 'visual_ref' || row.category === 'uncertain')
export async function loadReferences():Promise<Record<number,ReferenceRow>> {
  const response = await fetch('/api/reference-labels')
  if (!response.ok) throw new Error('参考标签读取失败')
  const data = await response.json()
  return Object.fromEntries(data.items.map((r:ReferenceRow)=>[r.id,r]))
}
export default function ReferenceLabel({imageId,onChanged}:{imageId:number;onChanged?:()=>void}) {
  const [row,setRow] = useState<ReferenceRow>()
  const [error,setError] = useState('')
  const [confirmed,setConfirmed] = useState(false)
  useEffect(()=>{loadReferences().then(rows=>{setRow(rows[imageId]);setConfirmed(Boolean(rows[imageId]?.confirmed))}).catch(()=>setError('标签尚未就绪'))},[imageId])
  const save=async(category:ReferenceCategory)=>{
    const response=await fetch(`/api/images/${imageId}/reference`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({category})})
    if(!response.ok){setError('保存失败，请重试');return}
    setRow(previous=>({...previous,id:imageId,category,manual_category:category,auto_category:previous?.auto_category??null,confirmed:previous?.confirmed??0,reason:previous?.reason??null}));onChanged?.()
  }
  return <section aria-label="参考价值"><h3>参考价值</h3>
    {row?.manual_reference===0&&<p>人工：不是参考，已从推荐候选排除。</p>}
    {row?.manual_reference===1&&<p>人工：是参考。</p>}
    <select aria-label="参考价值标签" value={row?.category??'uncertain'} onChange={e=>void save(e.target.value as ReferenceCategory)}>
      {Object.entries(referenceNames).map(([value,label])=><option key={value} value={value}>{label}</option>)}
    </select>
    <p className="muted">{row?.manual_category?'人工标签优先':row?.reason??'等待本地预判'}。不确定类保留在推荐中。</p>
    {(!row?.auto_category || !isVisual(row)) && !confirmed && <><p>待确认：暂不送付费视觉分析</p><button onClick={async()=>{
      const response=await fetch(`/api/images/${imageId}/confirm-analysis`,{method:'POST'});if(response.ok)setConfirmed(true);else setError('确认失败')
    }}>确认允许分析</button></>}
    {confirmed&&<p>已确认，等待你手动启动分析</p>}{error&&<p role="alert">{error}</p>}
  </section>
}

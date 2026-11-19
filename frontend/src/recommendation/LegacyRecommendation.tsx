import { useState } from 'react'
import type { SearchHit } from '../search/contract'
import { thumbnailSrc } from '../imageThumbnail'

/** Explicit comparison switch; this path never replaces the default three columns. */
export default function LegacyRecommendation({onOpen}:{onOpen:(id:number)=>void}) {
  const [brief,setBrief]=useState('')
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [result,setResult]=useState<{images:SearchHit[];items:{image_id:number;purposes:{purpose:string;reason:string}[]}[]}>()
  return <section aria-label="旧规则版对照"><p>旧规则版：基于分析字段给理由，未经三栏视觉复看。</p>
    <form onSubmit={async e=>{e.preventDefault();setBusy(true);setError('');try{
      const response=await fetch('/api/recommendations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({brief})})
      if(!response.ok)throw new Error();setResult(await response.json())
    }catch{setError('旧规则版暂不可用')}finally{setBusy(false)}}}>
      <label>旧规则版任务<input value={brief} onChange={e=>setBrief(e.target.value)}/></label>
      <button disabled={busy||!brief.trim()}>运行旧规则版</button>
    </form>{error&&<p role="alert">{error}</p>}
    <div className="workspace-cards">{result?.items.map(item=>{
      const image=result.images.find(i=>i.image_id===item.image_id)
      return <article key={item.image_id}><button onClick={()=>onOpen(item.image_id)}>{image&&<img src={thumbnailSrc(image.url)} width="160" alt={`参考 #${item.image_id}`} loading="lazy"/>}</button>{item.purposes.map(p=><p key={p.purpose}>{p.reason}</p>)}</article>
    })}</div>
  </section>
}

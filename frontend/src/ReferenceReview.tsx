import {useCallback,useEffect,useRef,useState} from 'react'
import type {ImageItem} from './api'
import {referenceNames,type ReferenceRow} from './ReferenceLabel'
type Row=ImageItem&ReferenceRow&{needs_confirmation:boolean}
type Group='all'|'pending'|'visual_ref'|'text_card'|'life'
const groups:Record<Group,string>={all:'全部',visual_ref:'视觉参考',text_card:'文字卡片',life:'生活',pending:'待确认'}
export default function ReferenceReview(){
  const [rows,setRows]=useState<Row[]>([]),[group,setGroup]=useState<Group>(()=>{const value=new URLSearchParams(location.search).get('group');return value&&value in groups?value as Group:'pending'})
  const [selected,setSelected]=useState<Set<number>>(new Set()),[busy,setBusy]=useState(false)
  const [error,setError]=useState(''),[operation,setOperation]=useState<string|null>(null)
  const [box,setBox]=useState<{x:number;y:number;width:number;height:number}|null>(null)
  const drag=useRef<{x:number;y:number;initial:Set<number>}|null>(null)
  const reload=useCallback(async()=>{const response=await fetch('/api/reference/review');if(!response.ok)throw new Error('读取失败');setRows((await response.json()).items)},[])
  useEffect(()=>{void reload().catch(()=>setError('读取待确认图片失败，请重试'))},[reload])
  const visible=rows.filter(r=>group==='all'||group==='pending'?group==='all'||r.needs_confirmation:r.category===group&&(group!=='visual_ref'||r.manual_reference!==0))
  const submit=async(is_reference:boolean)=>{
    setBusy(true);setError('')
    try{const response=await fetch('/api/reference/decisions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:[...selected],is_reference})});if(!response.ok)throw new Error();const result=await response.json();setOperation(result.operation);setSelected(new Set());await reload()}
    catch{setError('保存失败，请重试；本地图片仍保留')}finally{setBusy(false)}
  }
  const undo=async()=>{
    if(!operation)return;setBusy(true);setError('')
    try{const response=await fetch(`/api/reference/decisions/${operation}/undo`,{method:'POST'});if(!response.ok)throw new Error();const result=await response.json();setError(result.conflicts.length?`${result.conflicts.length}张有后续修改，已保留后续结果`:'已撤销');setOperation(null);await reload()}
    catch{setError('撤销失败，请重试')}finally{setBusy(false)}
  }
  return <main style={{maxWidth:'none'}}><h1>导入筛选与待确认</h1><p>只改参考判断，不删除图片，不启动付费分析。人工结果优先。</p><a href="/">返回工作台</a>
    <div style={{display:'flex',gap:20,marginTop:20}}><aside style={{width:130,flexShrink:0}} aria-label="智能分组">{Object.entries(groups).map(([key,name])=><button key={key} aria-pressed={group===key} style={{display:'block',marginBottom:10}} onClick={()=>{setGroup(key as Group);setSelected(new Set())}}>{name}</button>)}</aside>
      <section style={{flex:1,minWidth:0}}><p>显示 {visible.length} 张 · 已选 {selected.size} 张</p>
        <div style={{display:'flex',gap:8,flexWrap:'wrap'}}><button disabled={busy} onClick={()=>setSelected(new Set(visible.map(r=>r.id)))}>全选当前分组</button><button onClick={()=>setSelected(new Set())}>取消选择</button><button disabled={busy||!selected.size} onClick={()=>void submit(true)}>是参考</button><button disabled={busy||!selected.size} onClick={()=>void submit(false)}>不是参考</button><button disabled={busy||!operation} onClick={()=>void undo()}>撤销上次确认</button></div>
        <p>拖动可框选，按 Shift 可追加选择。文字卡片也可能对特定任务有用，可人工标“是参考”。</p>{error&&<p role="status">{error}</p>}
        <div style={{position:'relative',userSelect:'none',touchAction:'none'}} aria-label="待确认网格"
          onPointerDown={e=>{if(e.button!==0||(e.target as HTMLElement).closest('button,input,a'))return;drag.current={x:e.clientX,y:e.clientY,initial:e.shiftKey?new Set(selected):new Set()};e.currentTarget.setPointerCapture(e.pointerId)}}
          onPointerMove={e=>{if(!drag.current)return;const start=drag.current,x=Math.min(start.x,e.clientX),y=Math.min(start.y,e.clientY),right=Math.max(start.x,e.clientX),bottom=Math.max(start.y,e.clientY);if(right-x+bottom-y<6)return;const parent=e.currentTarget.getBoundingClientRect();setBox({x:x-parent.x,y:y-parent.y,width:right-x,height:bottom-y});const next=new Set(start.initial);for(const card of e.currentTarget.querySelectorAll<HTMLElement>('[data-review-id]')){const r=card.getBoundingClientRect();if(r.left<right&&r.right>x&&r.top<bottom&&r.bottom>y)next.add(Number(card.dataset.reviewId))}setSelected(next)}}
          onPointerUp={()=>{drag.current=null;setBox(null)}} onPointerCancel={()=>{drag.current=null;setBox(null)}}>
          {(['visual_ref','text_card','life','uncertain'] as const).map(category=>{const items=visible.filter(r=>r.category===category);return items.length>0&&<section key={category}><h2>{referenceNames[category]} · {items.length}</h2><div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(150px,1fr))',gap:12}}>{items.map(row=><label data-review-id={row.id} key={row.id} style={{border:selected.has(row.id)?'2px solid #3568bd':'2px solid #ddd',padding:8,background:'#fff'}}><img src={row.url} alt={`图片 #${row.id}`} draggable={false} style={{width:'100%',height:160,objectFit:'contain'}} loading="lazy"/><div><input type="checkbox" aria-label={`选择图片 #${row.id}`} checked={selected.has(row.id)} onChange={e=>setSelected(old=>{const next=new Set(old);if(e.target.checked)next.add(row.id);else next.delete(row.id);return next})}/>#{row.id} {row.manual_reference===1?'人工：是参考':row.manual_reference===0?'人工：不是参考':row.manual_category?'人工分类':row.needs_confirmation?'待确认':''}</div></label>)}</div></section>})}
          {box&&<div aria-hidden style={{pointerEvents:'none',position:'absolute',left:box.x,top:box.y,width:box.width,height:box.height,border:'1px solid #3568bd',background:'#3568bd22'}}/>}
        </div>
      </section>
    </div>
  </main>
}

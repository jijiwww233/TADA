import {useState} from 'react'
import type {ColorNeed} from './interactive'
export default function ColorTargets({onChange}:{onChange:(values:ColorNeed[])=>void}){
  const [values,setValues]=useState<ColorNeed[]>([]),[hex,setHex]=useState('#f5f0e6'),[main,setMain]=useState(false),[error,setError]=useState('')
  const update=(next:ColorNeed[])=>{setValues(next);onChange(next)}
  const add=()=>{if(!/^#[\da-f]{6}$/i.test(hex)){setError('请输入 #RRGGBB');return}if(values.length>=8){setError('最多指定8种颜色');return}setError('');update([...values.filter(v=>v.hex!==hex.toLowerCase()),{hex:hex.toLowerCase(),role:main?'为主':'点缀'}])}
  const extract=async(file:File)=>{setError('');try{const data=new FormData();data.append('file',file);const response=await fetch('/api/palette/extract',{method:'POST',body:data});if(!response.ok)throw new Error();const result=await response.json();update(result.colors.map((c:{hex:string;ratio:number})=>({hex:c.hex,role:c.ratio>.4?'为主':'点缀'})))}catch{setError('无法读取这张图的色板')}}
  return <details><summary>指定配色（只影响配色栏）</summary><div style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
    <label>Hex<input aria-label="配色Hex" value={hex} onChange={e=>setHex(e.target.value)}/></label><label>取色<input aria-label="配色取色器" type="color" value={/^#[\da-f]{6}$/i.test(hex)?hex:'#f5f0e6'} onChange={e=>setHex(e.target.value)}/></label><label><input type="checkbox" checked={main} onChange={e=>setMain(e.target.checked)}/>为主</label><button type="button" onClick={add}>添加颜色</button><button type="button" onClick={()=>update([])}>清除指定配色</button>
    <label onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();const file=e.dataTransfer.files[0];if(file)void extract(file)}}>拖图提取主色<input aria-label="配色参考图" type="file" accept="image/*" onChange={e=>{const file=e.target.files?.[0];if(file)void extract(file)}}/></label>
    </div>{values.length>0&&<ul aria-label="指定颜色">{values.map(v=><li key={v.hex}><span style={{display:'inline-block',width:16,height:16,background:v.hex,border:'1px solid #999'}}/>{v.hex} {v.role}<button type="button" aria-label={`移除颜色 ${v.hex}`} onClick={()=>update(values.filter(x=>x.hex!==v.hex))}>移除</button></li>)}</ul>}{error&&<p role="alert">{error}</p>}
  </details>
}

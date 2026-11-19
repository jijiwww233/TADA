import {it,expect,vi,afterEach} from 'vitest'
import {render,screen} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {streamRecommendations,type StreamApi} from './interactive'
import {RecommendationStore} from './store'
import type {RecommendationResponse} from './contract'
import ColorTargets from './ColorTargets'
afterEach(()=>vi.unstubAllGlobals())
const response:RecommendationResponse={status:'ok',effective_query:'建筑',columns:[],images:[],notices:[]}
it('decodes split UTF8 NDJSON and publishes a column before final response',async()=>{
  const events=[{event:'column',result:{columns:[],images:[],effective_query:'建筑'}},{event:'done',result:response}]
  const bytes=new TextEncoder().encode(events.map(e=>JSON.stringify(e)).join('\n')+'\n')
  const body=new ReadableStream({start(controller){for(const byte of bytes)controller.enqueue(new Uint8Array([byte]));controller.close()}})
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(body)))
  const partial=vi.fn();expect(await streamRecommendations('建筑',undefined,false,partial,{})).toEqual(response)
  expect(partial).toHaveBeenCalledOnce();expect(partial.mock.calls[0][0].effective_query).toBe('建筑')
})
it('streaming state exposes first results while the remaining columns are still pending',async()=>{
  let finish!:(value:RecommendationResponse)=>void
  const stream:StreamApi=async(_brief,_previous,_include,onPartial)=>{
    onPartial({columns:[],images:[],effective_query:'已经有一栏'})
    return new Promise(resolve=>{finish=resolve})
  }
  const store=new RecommendationStore(vi.fn(),vi.fn(),stream)
  const pending=store.recommend('建筑')
  expect(store.snapshot().status).toBe('loading');expect(store.snapshot().effectiveQuery).toBe('已经有一栏')
  finish(response);await pending;expect(store.snapshot().status).toBe('ok')
})
it('color extraction uses a local upload and becomes explicit multi-color targets',async()=>{
  const change=vi.fn();vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({colors:[{hex:'#112233',ratio:.7},{hex:'#ffeedd',ratio:.3}]})})))
  render(<ColorTargets onChange={change}/>);const user=userEvent.setup();await user.click(screen.getByText('指定配色（只影响配色栏）'))
  await user.upload(screen.getByLabelText('配色参考图'),new File(['fixture'],'palette.png',{type:'image/png'}))
  expect(change).toHaveBeenCalledWith([{hex:'#112233',role:'为主'},{hex:'#ffeedd',role:'点缀'}])
  expect(await screen.findByRole('list',{name:'指定颜色'})).toBeInTheDocument()
})

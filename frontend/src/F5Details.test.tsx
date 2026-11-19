import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import ImageDetail from './ImageDetail'
import SyncBar from './SyncBar'
const api=vi.hoisted(()=>({getSyncStatus:vi.fn(),startSync:vi.fn(),startZcoolBackfill:vi.fn(),verificationAction:vi.fn(async()=>({busy:true,action:'verify',account_known:true,last_run:null,failures:[]}))}))
vi.mock('./api',async importOriginal=>({...await importOriginal<typeof import('./api')>(),...api}))
it('gallery keeps native positions and preserves deleted/unfavored/login states',async()=>{
 const open=vi.fn();render(<ImageDetail imageId={1} onOpen={open} onClose={()=>{}} loadImage={async()=>({id:1,url:'/1.png',width:1,height:1,original_name:'a',created_at:'',colors:[],analysis:null,posts:[{id:1,platform:'xhs',url:'https://example.org',title:'fixture',position:0,is_video:true,image_count:3,source_state:'deleted',fav_state:'removed',connection_state:'needs_login',images:[{position:0,image_id:1,url:'/1.png',download_state:'ok'},{position:1,image_id:null,url:null,download_state:'failed'},{position:2,image_id:3,url:'/3.png',download_state:'ok'}]}]})}/> )
 const third=await screen.findByRole('button',{name:'第3张，共3张'});fireEvent.click(third);expect(open).toHaveBeenCalledWith(3)
 expect(screen.getByRole('button',{name:'第2张，共3张'})).toBeDisabled()
 expect(screen.getByText(/原帖已删除/)).toHaveTextContent('已取消收藏')
 expect(screen.getByText(/登录已失效/)).toBeTruthy()
 expect(screen.getByText('仅分析封面与简介')).toBeTruthy()
 expect(screen.getByRole('link',{name:'原帖链接（网页端可能受限）'}).getAttribute('href')).toBe('/api/posts/1/source')
})
it('blocked platform opens a visible verification flow only on the user click',async()=>{
 api.getSyncStatus.mockResolvedValue({busy:false,action:null,account_known:true,last_run:null,failures:[],connection_state:'needs_verification',connection_label:'需要验证'})
 render(<SyncBar platform="zcool" onSynced={()=>{}}/> )
 const button=await screen.findByRole('button',{name:'打开站酷验证窗口'});expect(api.verificationAction).not.toHaveBeenCalled();fireEvent.click(button)
 const confirmed=await screen.findByRole('button',{name:'我已完成验证，继续同步'});expect(api.verificationAction).toHaveBeenCalledWith('zcool');fireEvent.click(confirmed);expect(api.verificationAction).toHaveBeenCalledWith('zcool',true)
})

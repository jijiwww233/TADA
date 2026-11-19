import { render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import SyncBar from './SyncBar'
vi.mock('./api',()=>({getSyncStatus:vi.fn(async()=>({busy:false,action:null,account_known:true,last_run:null,failures:[],cooldown:{blocked:true,manual_hold:true}})),startSync:vi.fn(),startZcoolBackfill:vi.fn()}))
it('cooldown disables XHS sync and exposes the platform risk message',async()=>{
  render(<SyncBar onSynced={()=>{}}/> )
  expect(await screen.findByText(/平台风控中，请稍后手动同步/)).toBeTruthy()
  expect(screen.getByRole('button',{name:'同步小红书收藏'})).toBeDisabled()
  expect(screen.queryByText('登录小红书')).toBeNull()
})

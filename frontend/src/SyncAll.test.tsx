import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import SyncAll from './SyncAll'
afterEach(() => vi.unstubAllGlobals())
it('manual all-platform sync dispatches existing status polling, never login', async () => {
  const fetch=vi.fn(async()=>({ok:true})), listener=vi.fn()
  vi.stubGlobal('fetch',fetch); window.addEventListener('sync-all-started',listener)
  render(<SyncAll/>); fireEvent.click(screen.getByRole('button',{name:'同步全部平台'}))
  await waitFor(()=>expect(listener).toHaveBeenCalledTimes(1))
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/sync/all',{method:'POST'})
  window.removeEventListener('sync-all-started',listener)
})

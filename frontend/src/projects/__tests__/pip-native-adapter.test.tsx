import { createRef } from 'react'
import { act, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import type { ImageItem } from '../../api'

const native = vi.hoisted(() => {
  const api = { window: null as Window | null, requestWindow: vi.fn() }
  Object.defineProperty(window,'documentPictureInPicture',{configurable:true,value:api})
  return api
})
vi.mock('../../pip/PipBoard', () => ({ default: ({ images }: { images: ImageItem[] }) =>
  <div data-testid="existing-pip-board">{images.map(i=>i.id).join(',')}</div> }))
import PipButton, { type PipHandle } from '../../PipButton'

beforeEach(() => { native.window = null; native.requestWindow.mockReset() })
const image = (id:number):ImageItem => ({id,post_id:5,position:129,url:`/images/${id}.png`,width:10,height:10,created_at:'',original_name:null,platform:'xhs'})

it('project action uses the existing window/portal and reacts to pin removal, without another PiP instance', async () => {
  const document = window.document.implementation.createHTMLDocument('test PiP')
  const close = vi.fn(); const focus = vi.fn()
  const child = {document,close,focus,addEventListener:vi.fn()} as unknown as Window
  native.requestWindow.mockImplementation(async () => { native.window=child; return child })
  const ref = createRef<PipHandle>()
  const view = render(<PipButton ref={ref} images={[image(42)]} />)
  expect(ref.current?.supported).toBe(true)
  await act(async () => { await ref.current!.open() })
  expect(native.requestWindow).toHaveBeenCalledExactlyOnceWith({width:720,height:620})
  expect(document.body.textContent).toContain('42')
  await act(async () => { await ref.current!.open() })
  expect(native.requestWindow).toHaveBeenCalledTimes(1); expect(focus).toHaveBeenCalledTimes(1)
  view.rerender(<PipButton ref={ref} images={[]} />)
  expect(document.body.textContent).not.toContain('42')
  expect(screen.getAllByRole('button',{name:'弹出小窗'})).toHaveLength(1)
  view.unmount(); expect(close).toHaveBeenCalled()
})

it('keeps the mature six-image limit for project sources', async () => {
  const document = window.document.implementation.createHTMLDocument('test PiP')
  const child = {document,close:vi.fn(),focus:vi.fn(),addEventListener:vi.fn()} as unknown as Window
  native.requestWindow.mockImplementation(async () => { native.window=child; return child })
  const ref = createRef<PipHandle>()
  render(<PipButton ref={ref} images={Array.from({length:8},(_,i)=>image(i+1))} />)
  await act(async () => { await ref.current!.open() })
  expect(document.body.textContent).toBe('1,2,3,4,5,6')
})
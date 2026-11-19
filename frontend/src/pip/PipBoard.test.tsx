import { act, createEvent, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ImageItem } from '../api'
import type { ImageEntity } from '../search/contract'
import { PipSceneConflict, type PipSceneApi } from './persistence'
import { itemAABB } from './geometry'
import { emptyScene, type PipScene } from './scene'
import PipBoard from './PipBoard'

vi.mock('../PipCanvas', () => ({
  default: (props: {
    scene: PipScene
    onSelect: (value: { kind: 'image'; image_id: number } | { kind: 'note'; id: string } | { kind: 'images'; image_ids: number[] }) => void
    onContextMenu: (event: MouseEvent, imageId: number | null) => void
    onCrop: (imageId: number, rect: { x: number; y: number; width: number; height: number }) => void
    onAddNote: (x: number, y: number) => void
    onOpenOriginal: (imageId: number) => void
    onOpenPost: (imageId: number, shift: boolean) => void
    onEditNote?: (noteId: string) => void
  }) => (
    <div data-testid="canvas" className="pip-canvas-host" onContextMenu={event => props.onContextMenu(event.nativeEvent, null)}>
      <span data-testid="canvas-scene">{JSON.stringify(props.scene)}</span>
      <button type="button" onClick={() => props.onSelect({ kind: 'image', image_id: 1 })}>canvas-select-1</button>
      <button type="button" onClick={() => props.onSelect({ kind: 'note', id: 'note-1' })}>canvas-select-note</button>
      <button type="button" onClick={() => props.onSelect({ kind: 'images', image_ids: [2, 3] })}>canvas-select-multi</button>
      <button type="button" onClick={() => props.onCrop(1, { x: 0, y: 0, width: 0.5, height: 0.5 })}>canvas-crop</button>
      <button type="button" onClick={() => props.onAddNote(30, 40)}>canvas-add-note</button>
      <button type="button" onClick={() => props.onOpenOriginal(1)}>canvas-open-original</button>
      <button type="button" onClick={() => props.onOpenPost(1, true)}>canvas-open-post</button>
      <button type="button" onClick={() => props.onEditNote?.('note-1')}>canvas-edit-note</button>
    </div>
  ),
}))

const image = (id: number, patch: Partial<ImageEntity> = {}): ImageEntity => ({
  id, post_id: 5, position: 0, url: '/images/' + id + '.png', width: 400, height: 300,
  original_name: '图 ' + id, created_at: '2026-11-05', platform: 'local', ...patch,
})

function fakeApi(snapshot: { revision: number; scene: PipScene | null } = { revision: 0, scene: null }) {
  let revision = snapshot.revision
  const save = vi.fn(async (_id: number, expected: number, scene: PipScene) => {
    if (expected !== revision) throw new PipSceneConflict(revision, snapshot.scene)
    revision += 1
    return { revision, scene }
  })
  return { get: vi.fn(async () => snapshot), save } as unknown as PipSceneApi & { get: ReturnType<typeof vi.fn>; save: ReturnType<typeof vi.fn> }
}

type PendingSave = {
  id: number
  expected: number
  scene: PipScene
  resolve: (value: { revision: number; scene: PipScene }) => void
  reject: (error: unknown) => void
}

function deferredApi(initial: Record<number, { revision: number; scene: PipScene | null }>) {
  const state = new Map<number, { revision: number; scene: PipScene | null }>(
    Object.entries(initial).map(([id, value]) => [Number(id), { ...value }]),
  )
  const pending: PendingSave[] = []
  const get = vi.fn(async (id: number) => {
    const entry = state.get(id) ?? { revision: 0, scene: null }
    return { revision: entry.revision, scene: entry.scene }
  })
  const save = vi.fn((id: number, expected: number, scene: PipScene) =>
    new Promise<{ revision: number; scene: PipScene }>((resolve, reject) => {
      pending.push({ id, expected, scene, resolve, reject })
    }))
  return {
    api: { get, save } as unknown as PipSceneApi & { get: ReturnType<typeof vi.fn>; save: ReturnType<typeof vi.fn> },
    pending,
    state,
  }
}

const sceneData = () => JSON.parse(screen.getByTestId('canvas-scene').textContent || '{}') as PipScene
const openImageMenu = () => fireEvent.click(screen.getByRole('button', { name: '图片操作' }))
const openProjectMenu = () => fireEvent.click(screen.getByTestId('pip-project-name'))
const openCanvasMenu = () => fireEvent.contextMenu(screen.getByTestId('canvas'))
const itemById = (imageId: number) => sceneData().items.find(entry => entry.image_id === imageId)!

async function renderBoard(props: {
  projectId?: number | null
  api?: PipSceneApi
  images?: ImageEntity[]
  imagesById?: Record<number, ImageItem | undefined>
  openUrl?: (url: string, kind: 'image' | 'post') => void
}) {
  const api = props.api ?? fakeApi()
  const openUrl = props.openUrl ?? vi.fn()
  const view = render(
    <PipBoard
      images={props.images ?? [image(1), image(2)]}
      imagesById={props.imagesById}
      projectId={props.projectId === undefined ? 7 : props.projectId}
      win={window}
      sceneApi={api}
      openUrl={openUrl}
    />,
  )
  // The board defers loading by one macrotask so the effect does not setState synchronously.
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  return { view, api, openUrl }
}

describe('PipBoard loading and saving', () => {
  it('restores a saved scene and does not save untouched boards', async () => {
    const saved = { ...emptyScene(), items: [{
      image_id: 5, x: 0, y: 0, width: 100, height: 50, rotation: 0, flip_x: false, grayscale: false, opacity: 1, crop: null,
    }] }
    const api = fakeApi({ revision: 3, scene: saved })
    await renderBoard({ api, images: [] })
    expect(api.get).toHaveBeenCalledWith(7)
    expect(sceneData().items.map(entry => entry.image_id)).toEqual([5])
    expect(screen.getByTestId('pip-status').textContent).toBe('')
    await new Promise(resolve => setTimeout(resolve, 800))
    expect(api.save).not.toHaveBeenCalled()
  })

  it('saves a brand new project scene with revision zero and shows 已保存', async () => {
    const api = fakeApi({ revision: 0, scene: null })
    await renderBoard({ api })
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1), { timeout: 3000 })
    const call = api.save.mock.calls[0] as [number, number, PipScene]
    expect(call[0]).toBe(7)
    expect(call[1]).toBe(0)
    expect(call[2].items.map(entry => entry.image_id)).toEqual([1, 2])
    await waitFor(() => expect(screen.getByTestId('pip-status')).toHaveAttribute('data-state', 'saved'), { timeout: 3000 })
  })

  it('debounces user edits into one save with the next revision', async () => {
    const api = fakeApi({ revision: 0, scene: null })
    await renderBoard({ api })
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1), { timeout: 3000 })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    fireEvent.click(screen.getByRole('button', { name: '水平翻转' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(2), { timeout: 3000 })
    const call = api.save.mock.calls[1] as [number, number, PipScene]
    expect(call[1]).toBe(1)
    expect(call[2].items.find(entry => entry.image_id === 1)).toMatchObject({ grayscale: true, flip_x: true })
  })

  it('keeps unsaved edits on conflict and offers both recovery paths', async () => {
    const server = { ...emptyScene(), items: [{
      image_id: 1, x: 0, y: 0, width: 100, height: 50, rotation: 0, flip_x: false, grayscale: false, opacity: 0.3, crop: null,
    }] }
    const api = fakeApi({ revision: 1, scene: server })
    api.save.mockRejectedValueOnce(new PipSceneConflict(4, server))
    await renderBoard({ api, images: [] })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    await waitFor(() => expect(screen.getByText('布局已在另一个窗口更新，本次修改尚未保存。')).toBeInTheDocument(), { timeout: 3000 })
    fireEvent.click(screen.getByText('加载服务器布局'))
    expect(sceneData().items).toHaveLength(1)
    expect(sceneData().items[0]).toMatchObject({ image_id: 1, opacity: 0.3 })
    expect(screen.queryByText('布局已在另一个窗口更新，本次修改尚未保存。')).not.toBeInTheDocument()
    expect(screen.getByTestId('pip-status').textContent).toBe('')
  })

  it('can overwrite the server scene with the local one after a conflict', async () => {
    const server = { ...emptyScene(), items: [{
      image_id: 1, x: 0, y: 0, width: 100, height: 50, rotation: 0, flip_x: false, grayscale: false, opacity: 1, crop: null,
    }] }
    const api = fakeApi({ revision: 1, scene: server })
    api.save.mockRejectedValueOnce(new PipSceneConflict(9, server))
    await renderBoard({ api, images: [] })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    await waitFor(() => expect(screen.getByText('保留本地并覆盖')).toBeInTheDocument(), { timeout: 3000 })
    api.save.mockImplementationOnce(async (_id: number, rev: number, next: PipScene) => ({ revision: rev + 1, scene: next }))
    fireEvent.click(screen.getByText('保留本地并覆盖'))
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(2), { timeout: 3000 })
    const call = api.save.mock.calls[1] as [number, number, PipScene]
    expect(call[1]).toBe(9)
    expect(call[2].items[0].grayscale).toBe(true)
  })

  it('does not save when no project is selected', async () => {
    const api = fakeApi()
    await renderBoard({ api, projectId: null })
    expect(api.get).not.toHaveBeenCalled()
    expect(screen.getByTestId('pip-project-name').textContent).toContain('全部收藏')
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    await new Promise(resolve => setTimeout(resolve, 800))
    expect(api.save).not.toHaveBeenCalled()
  })
})

function dropAt(board: Element, id: string, clientX = 0, clientY = 0) {
  const dataTransfer = {
    types: ['application/x-wominmin-image'],
    getData: () => id,
    dropEffect: 'none',
    effectAllowed: 'all',
    setData: () => {},
  }
  const over = createEvent.dragOver(board, { dataTransfer })
  Object.defineProperty(over, 'dataTransfer', { value: dataTransfer })
  fireEvent(board, over)
  expect(dataTransfer.dropEffect).toBe('copy')
  const drop = createEvent.drop(board)
  Object.defineProperties(drop, {
    dataTransfer: { value: dataTransfer },
    clientX: { value: clientX },
    clientY: { value: clientY },
  })
  fireEvent(board, drop)
}

describe('drag and drop from the library', () => {
  it('adds a local image id at the drop point', async () => {
    const api = fakeApi()
    const board = (await renderBoard({ api, images: [], imagesById: { 9: image(9) } })).view.container.querySelector('.pip-board')!
    dropAt(board, '9', 120, 60)
    const placed = sceneData().items.find(entry => entry.image_id === 9)!
    expect(placed.x).toBeCloseTo(120)
    expect(placed.y).toBeCloseTo(60)
  })

  function stubHostRect(container: HTMLElement, left: number, top: number) {
    const host = container.querySelector('.pip-canvas-host') as HTMLElement
    expect(host).not.toBeNull()
    host.getBoundingClientRect = () => ({
      left, top, x: left, y: top, width: 721, height: 560,
      right: left + 721, bottom: top + 560, toJSON: () => ({}),
    }) as DOMRect
  }

  const dropWithView = async (view: { x: number; y: number; scale: number }, clientX: number, clientY: number,
    host: { left: number; top: number }) => {
    const api = fakeApi({ revision: 1, scene: { ...emptyScene(), view } })
    const rendered = await renderBoard({ api, images: [], imagesById: { 9: image(9) } })
    const board = rendered.view.container.querySelector('.pip-board')!
    stubHostRect(rendered.view.container as HTMLElement, host.left, host.top)
    dropAt(board, '9', clientX, clientY)
    return sceneData().items.find(entry => entry.image_id === 9)!
  }

  it('subtracts the canvas host offset from the drop point at scale 1', async () => {
    const placed = await dropWithView({ x: 0, y: 0, scale: 1 }, 120, 60, { left: 0, top: 48 })
    expect(placed.x).toBeCloseTo(120)
    expect(placed.y).toBeCloseTo(12)
  })

  it('subtracts the canvas host offset before the 1/scale inverse at 50% zoom', async () => {
    const placed = await dropWithView({ x: 0, y: 0, scale: 0.5 }, 120, 60, { left: 0, top: 48 })
    expect(placed.x).toBeCloseTo(240)
    expect(placed.y).toBeCloseTo(24)
  })

  it('accounts for both the host offset and a panned view', async () => {
    const placed = await dropWithView({ x: -40, y: -20, scale: 1 }, 120, 60, { left: 0, top: 48 })
    expect(placed.x).toBeCloseTo(160)
    expect(placed.y).toBeCloseTo(32)
  })

  it('keeps the drop point when toolbar/message shift the canvas host down', async () => {
    const placed = await dropWithView({ x: 0, y: 0, scale: 1 }, 120, 140, { left: 10, top: 80 })
    expect(placed.x).toBeCloseTo(110)
    expect(placed.y).toBeCloseTo(60)
  })

  it('rejects unknown ids and external URLs', async () => {
    const api = fakeApi()
    const board = (await renderBoard({ api, images: [] })).view.container.querySelector('.pip-board')!
    dropAt(board, '999', 10, 10)
    expect(screen.getByText('只能拖入图库里的图片')).toBeInTheDocument()
    const drop = createEvent.drop(board)
    Object.defineProperty(drop, 'dataTransfer', { value: { types: ['text/uri-list'], getData: () => 'https://xhscdn.example/a.jpg', dropEffect: 'none' } })
    fireEvent(board, drop)
    expect(sceneData().items).toHaveLength(0)
  })
})

describe('opening originals and posts', () => {
  it('opens the local image file on double click', async () => {
    const openUrl = vi.fn()
    await renderBoard({ openUrl })
    fireEvent.click(screen.getByText('canvas-open-original'))
    expect(openUrl).toHaveBeenCalledTimes(1)
    const [url, kind] = openUrl.mock.calls[0] as [string, string]
    expect(kind).toBe('image')
    expect(url).toMatch(/\/images\/1\.png$/)
    expect(url.startsWith('http')).toBe(true)
  })

  it('routes XHS posts through the cooldown-aware source endpoint', async () => {
    const openUrl = vi.fn()
    const source = {
      post_id: 5, platform: 'xhs', source_id: 'note-1', canonical_url: 'https://www.xiaohongshu.com/explore/note-1',
      title: '原帖', position: 0, is_video: false,
    }
    await renderBoard({ openUrl, images: [image(1, { sources: [source] })] })
    fireEvent.click(screen.getByText('canvas-open-post'))
    expect(openUrl).toHaveBeenCalledWith('/api/posts/5/source', 'post')
    expect(openUrl).not.toHaveBeenCalledWith(source.canonical_url, 'post')
  })

  it('opens non-XHS canonical post links directly', async () => {
    const openUrl = vi.fn()
    const source = {
      post_id: 7, platform: 'huaban', source_id: 'pin-7', canonical_url: 'https://huaban.com/pins/7',
      title: '花瓣', position: 0, is_video: false,
    }
    await renderBoard({ openUrl, images: [image(1, { sources: [source] })] })
    fireEvent.click(screen.getByText('canvas-open-post'))
    expect(openUrl).toHaveBeenCalledWith('https://huaban.com/pins/7', 'post')
  })
})

describe('toolbar interactions', () => {
  it('toggles grayscale, flip, crop and restores the crop', async () => {
    const api = fakeApi()
    await renderBoard({ api })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    expect(itemById(1).grayscale).toBe(true)
    expect(screen.getByRole('button', { name: '灰度' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: '水平翻转' }))
    expect(itemById(1).flip_x).toBe(true)
    openImageMenu()
    fireEvent.change(screen.getByLabelText('不透明度'), { target: { value: '40' } })
    expect(itemById(1).opacity).toBeCloseTo(0.4)
    fireEvent.click(screen.getByRole('button', { name: '裁剪' }))
    fireEvent.click(screen.getByText('canvas-crop'))
    const cropped = itemById(1)
    // The item was flipped first, so the visible left half is the source right half.
    expect(cropped.crop).toEqual({ x: 0.5, y: 0, width: 0.5, height: 0.5 })
    expect(cropped.width).toBeCloseTo(150)
    expect(screen.getByText('恢复原图')).toBeEnabled()
    fireEvent.click(screen.getByText('恢复原图'))
    expect(itemById(1).crop).toBeNull()
  })

  it('crops the visible left half into the source right half after a horizontal flip', async () => {
    const api = fakeApi()
    await renderBoard({ api })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '水平翻转' }))
    fireEvent.click(screen.getByRole('button', { name: '裁剪' }))
    fireEvent.click(screen.getByText('canvas-crop'))
    const cropped = itemById(1)
    expect(cropped.crop).toMatchObject({ x: 0.5, y: 0, width: 0.5, height: 0.5 })
    expect(cropped.width).toBeCloseTo(150)
    expect(cropped.y).toBeCloseTo(cropped.y)
  })

  it('applies opacity and crop recovery to the selected group in one undo step', async () => {
    const items = [1, 2, 3].map(image_id => ({
      image_id, x: image_id * 100, y: 30, width: 100, height: 80, rotation: 0,
      flip_x: true, grayscale: true, opacity: 1,
      crop: { x: 0.5, y: 0, width: 0.5, height: 1 },
    }))
    const api = fakeApi({ revision: 1, scene: { ...emptyScene(), items } })
    await renderBoard({ api, images: [image(1), image(2), image(3)] })
    fireEvent.click(screen.getByText('canvas-select-multi'))
    openImageMenu()
    fireEvent.change(screen.getByLabelText('不透明度'), { target: { value: '40' } })
    expect([1, 2, 3].map(id => itemById(id).opacity)).toEqual([1, 0.4, 0.4])
    fireEvent.click(screen.getByText('恢复原图'))
    expect(itemById(1).crop).not.toBeNull()
    expect(itemById(2).crop).toBeNull()
    expect(itemById(3).crop).toBeNull()
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    expect(itemById(2).crop).not.toBeNull()
    expect(itemById(3).crop).not.toBeNull()
    openImageMenu()
    fireEvent.click(screen.getByText('重置图片'))
    expect(itemById(1)).toMatchObject({ grayscale: true, flip_x: true })
    for (const id of [2, 3]) {
      expect(itemById(id)).toMatchObject({ grayscale: false, flip_x: false, opacity: 1, crop: null })
    }
  })

  it('adds, edits and deletes a text note', async () => {
    const api = fakeApi()
    await renderBoard({ api })
    fireEvent.click(screen.getByRole('button', { name: '便签' }))
    expect(sceneData().notes).toHaveLength(1)
    expect(sceneData().notes[0].text).toBe('')
    fireEvent.click(screen.getByText('canvas-select-note'))
    const textarea = screen.getByLabelText('内容')
    expect(textarea).toHaveValue('')
    fireEvent.change(textarea, { target: { value: '主视觉留白' } })
    expect(sceneData().notes[0].text).toBe('主视觉留白')
    fireEvent.change(screen.getByLabelText('字号'), { target: { value: '24' } })
    expect(sceneData().notes[0].font_size).toBe(24)
    fireEvent.click(screen.getByText('删除便签'))
    expect(sceneData().notes).toHaveLength(0)
  })

  it('compact arrangement keeps every item and applies to all opacity', async () => {
    const api = fakeApi()
    await renderBoard({ api })
    openProjectMenu()
    fireEvent.change(screen.getByLabelText('全部不透明度'), { target: { value: '50' } })
    expect(sceneData().items.every(entry => entry.opacity === 0.5)).toBe(true)
    openCanvasMenu()
    fireEvent.click(screen.getByText('自动排列'))
    expect(sceneData().items).toHaveLength(2)
    // Auto arrange packs by aspect ratio with an 8px visual gutter.
    const [first, second] = [...sceneData().items].sort((a, b) => a.y - b.y || a.x - b.x)
    const gap = second.y > first.y + 0.5
      ? second.y - (first.y + first.height)
      : second.x - (first.x + first.width)
    expect(gap).toBeCloseTo(8)
  })

  it('requires two clicks to reset the board', async () => {
    const api = fakeApi()
    await renderBoard({ api })
    openProjectMenu()
    fireEvent.click(screen.getByText('重置布局'))
    expect(screen.getByText('再点一次“确认重置”会清空本项目保存的布局')).toBeInTheDocument()
    expect(screen.getByText('确认重置')).toBeInTheDocument()
    fireEvent.click(screen.getByText('确认重置'))
    expect(sceneData().items.map(entry => entry.image_id)).toEqual([1, 2])
  })
})

describe('exit flush', () => {
  const savedScene: PipScene = { ...emptyScene(), items: [{
    image_id: 1, x: 10, y: 20, width: 100, height: 80, rotation: 0,
    flip_x: false, grayscale: false, opacity: 1, crop: null,
  }] }

  afterEach(() => {
    vi.unstubAllGlobals()
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
  })

  function stubFetch(impl?: () => Promise<unknown>) {
    const fetcher = vi.fn(impl ?? (async () => ({ ok: true, status: 200, json: async () => ({}) })))
    vi.stubGlobal('fetch', fetcher)
    return fetcher
  }

  async function dirtyBoard() {
    const api = fakeApi({ revision: 3, scene: savedScene })
    const rendered = await renderBoard({ api, images: [image(1)] })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    expect(sceneData().items[0].grayscale).toBe(true)
    return { ...rendered, api }
  }

  it('sends a keepalive PUT on pagehide and cancels the debounce timer', async () => {
    const { api } = await dirtyBoard()
    const fetcher = stubFetch()
    fireEvent(window, new Event('pagehide'))
    expect(fetcher).toHaveBeenCalledTimes(1)
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/projects/7/pip-scene')
    expect(init.method).toBe('PUT')
    expect(init.keepalive).toBe(true)
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(JSON.parse(String(init.body))).toMatchObject({
      revision: 3,
      scene: { items: [{ image_id: 1, grayscale: true }] },
    })
    // The 700ms debounce must not also fire after the flush.
    await new Promise(resolve => setTimeout(resolve, 800))
    expect(api.save).not.toHaveBeenCalled()
  })

  it('sends the same keepalive PUT on beforeunload', async () => {
    await dirtyBoard()
    const fetcher = stubFetch()
    fireEvent(window, new Event('beforeunload'))
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].keepalive).toBe(true)
  })

  it('uses the tracked API save when the document becomes hidden, then keeps the new revision', async () => {
    const { api } = await dirtyBoard()
    const fetcher = stubFetch()
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
    fireEvent(document, new Event('visibilitychange'))
    // A hidden but still-alive document can observe the response, so this is a normal save
    // (keepalive fire-and-forget is only for pagehide/beforeunload).
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1), { timeout: 3000 })
    const first = api.save.mock.calls[0] as [number, number, PipScene]
    expect(first[0]).toBe(7)
    expect(first[1]).toBe(3)
    expect(first[2].items.find(entry => entry.image_id === 1)).toMatchObject({ grayscale: true })
    expect(fetcher).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByTestId('pip-status')).toHaveAttribute('data-state', 'saved'), { timeout: 3000 })
    // Back to visible: the next edit must save with the revision learned while hidden.
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
    fireEvent(document, new Event('visibilitychange'))
    fireEvent.click(screen.getByRole('button', { name: '水平翻转' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(2), { timeout: 3000 })
    const second = api.save.mock.calls[1] as [number, number, PipScene]
    expect(second[1]).toBe(4)
    expect(second[2].items.find(entry => entry.image_id === 1)).toMatchObject({ grayscale: true, flip_x: true })
    expect(screen.queryByText('布局已在另一个窗口更新，本次修改尚未保存。')).not.toBeInTheDocument()
  })

  it('does nothing when there are no unsaved changes', async () => {
    const api = fakeApi({ revision: 3, scene: savedScene })
    await renderBoard({ api, images: [image(1)] })
    const fetcher = stubFetch()
    fireEvent(window, new Event('pagehide'))
    fireEvent(window, new Event('beforeunload'))
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
    fireEvent(document, new Event('visibilitychange'))
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(fetcher).not.toHaveBeenCalled()
    expect(api.save).not.toHaveBeenCalled()
  })

  it('never leaks a rejected request as an uncaught error', async () => {
    await dirtyBoard()
    const fetcher = stubFetch(async () => { throw new Error('offline') })
    expect(() => fireEvent(window, new Event('pagehide'))).not.toThrow()
    expect(fetcher).toHaveBeenCalledTimes(1)
    await new Promise(resolve => setTimeout(resolve, 20))
  })

  it('flushes at most once per unload and not after unmounting', async () => {
    const { view } = await dirtyBoard()
    const fetcher = stubFetch()
    fireEvent(window, new Event('pagehide'))
    fireEvent(window, new Event('beforeunload'))
    expect(fetcher).toHaveBeenCalledTimes(1)
    view.unmount()
    fireEvent(window, new Event('pagehide'))
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})

describe('undo and redo', () => {
  const itemOne = {
    image_id: 1, x: 10, y: 10, width: 100, height: 80, rotation: 0,
    flip_x: false, grayscale: false, opacity: 1, crop: null,
  }
  const baseScene: PipScene = { ...emptyScene(), items: [itemOne] }
  const movedScene: PipScene = { ...emptyScene(), items: [{ ...itemOne, x: 500 }] }

  it('undoes and redoes a flag with Ctrl+Z, Ctrl+Shift+Z and Ctrl+Y', async () => {
    const api = fakeApi({ revision: 2, scene: baseScene })
    await renderBoard({ api, images: [image(1)] })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    expect(sceneData().items[0].grayscale).toBe(true)
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    expect(sceneData().items[0].grayscale).toBe(false)
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true, shiftKey: true })
    expect(sceneData().items[0].grayscale).toBe(true)
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    expect(sceneData().items[0].grayscale).toBe(false)
    fireEvent.keyDown(window, { key: 'y', ctrlKey: true })
    expect(sceneData().items[0].grayscale).toBe(true)
  })

  it('drops the redo branch when a new edit arrives', async () => {
    const api = fakeApi({ revision: 2, scene: baseScene })
    await renderBoard({ api, images: [image(1)] })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    expect(sceneData().items[0].grayscale).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: '水平翻转' }))
    openCanvasMenu()
    expect(screen.queryByRole('button', { name: '重做' })).not.toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true, shiftKey: true })
    expect(sceneData().items[0]).toMatchObject({ grayscale: false, flip_x: true })
  })

  it('keeps undo and redo available by keyboard with the menu open', async () => {
    const api = fakeApi({ revision: 2, scene: baseScene })
    await renderBoard({ api, images: [image(1)] })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    openCanvasMenu()
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    expect(sceneData().items[0].grayscale).toBe(false)
    openCanvasMenu()
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true, shiftKey: true })
    expect(sceneData().items[0].grayscale).toBe(true)
  })

  it('leaves Ctrl+Z inside a note textarea to the browser', async () => {
    const api = fakeApi({ revision: 2, scene: baseScene })
    await renderBoard({ api, images: [image(1)] })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    fireEvent.click(screen.getByRole('button', { name: '便签' }))
    expect(sceneData().notes).toHaveLength(1)
    const textarea = screen.getByLabelText('内容')
    textarea.focus()
    fireEvent.keyDown(textarea, { key: 'z', ctrlKey: true })
    // The board must not undo the "add note" edit while the caret is in the textarea.
    expect(sceneData().notes).toHaveLength(1)
    expect(sceneData().items[0].grayscale).toBe(true)
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    expect(sceneData().notes).toHaveLength(0)
  })

  it('clears the stack when switching projects', async () => {
    const api = {
      get: vi.fn(async (id: number) => ({ revision: 1, scene: id === 7 ? baseScene : movedScene })),
      save: vi.fn(async (_id: number, rev: number, next: PipScene) => ({ revision: rev + 1, scene: next })),
    } as unknown as PipSceneApi
    const openUrl = vi.fn()
    const view = render(<PipBoard images={[image(1), image(2)]} projectId={7} win={window} sceneApi={api} openUrl={openUrl} />)
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    expect(itemById(1).grayscale).toBe(true)
    view.rerender(<PipBoard images={[image(1), image(2)]} projectId={8} win={window} sceneApi={api} openUrl={openUrl} />)
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
    expect(sceneData().items[0].x).toBe(500)
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    // A stale project-7 snapshot would move the item back to x=10.
    expect(sceneData().items[0].x).toBe(500)
  })

  it('clears the stack when the layout is reset', async () => {
    const api = fakeApi({ revision: 2, scene: baseScene })
    await renderBoard({ api, images: [image(1), image(2)] })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    openProjectMenu()
    fireEvent.click(screen.getByText('重置布局'))
    fireEvent.click(screen.getByText('确认重置'))
    expect(sceneData().items).toHaveLength(2)
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    expect(sceneData().items).toHaveLength(2)
  })
})

describe('board shortcuts', () => {
  const savedScene: PipScene = {
    ...emptyScene(),
    items: [
      { image_id: 1, x: 10, y: 10, width: 100, height: 80, rotation: 0, flip_x: false, grayscale: false, opacity: 1, crop: null },
      { image_id: 2, x: 300, y: 10, width: 100, height: 80, rotation: 0, flip_x: false, grayscale: false, opacity: 1, crop: null },
    ],
  }

  async function boardWithTwo() {
    const api = fakeApi({ revision: 2, scene: savedScene })
    const rendered = await renderBoard({ api, images: [image(1), image(2)] })
    return rendered
  }

  it('deletes the selected image with Delete and Backspace', async () => {
    await boardWithTwo()
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.keyDown(window, { key: 'Delete' })
    expect(sceneData().items.map(entry => entry.image_id)).toEqual([2])
    fireEvent.keyDown(window, { key: 'a', ctrlKey: true })
    fireEvent.keyDown(window, { key: 'Backspace' })
    expect(sceneData().items).toHaveLength(0)
  })

  it('selects every image with Ctrl+A and applies flags to the selection', async () => {
    await boardWithTwo()
    const prevented = fireEvent.keyDown(window, { key: 'a', ctrlKey: true })
    expect(prevented).toBe(false)
    expect(screen.getByTestId('pip-multi-tools')).toBeInTheDocument()
    expect(screen.getByText('已选 2 张')).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'g' })
    expect(sceneData().items.every(entry => entry.grayscale)).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '水平翻转' }))
    expect(sceneData().items.every(entry => entry.flip_x)).toBe(true)
    fireEvent.keyDown(window, { key: 'Delete' })
    expect(sceneData().items).toHaveLength(0)
  })

  it('toggles grayscale with G and switches between hand and selection with H and V', async () => {
    await boardWithTwo()
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.keyDown(window, { key: 'g' })
    expect(itemById(1).grayscale).toBe(true)
    expect(itemById(2).grayscale).toBe(false)
    fireEvent.keyDown(window, { key: 'g' })
    expect(itemById(1).grayscale).toBe(false)
    fireEvent.keyDown(window, { key: 'h' })
    expect(screen.getByRole('button', { name: '抓手' })).toHaveAttribute('aria-pressed', 'true')
    expect(itemById(1).flip_x).toBe(false)
    fireEvent.keyDown(window, { key: 'v' })
    expect(screen.getByRole('button', { name: '选择' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('fits the whole board with Shift+1 without touching the undo history', async () => {
    await boardWithTwo()
    const before = sceneData().view
    fireEvent.keyDown(window, { key: '!', code: 'Digit1', shiftKey: true })
    const fitted = sceneData().view
    expect(fitted).not.toEqual(before)
    expect(sceneData().items.map(entry => entry.image_id)).toEqual([1, 2])
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    expect(sceneData().view).toEqual(fitted)
  })

  it('leaves shortcuts to the browser while a note textarea is focused', async () => {
    await boardWithTwo()
    fireEvent.click(screen.getByRole('button', { name: '便签' }))
    const textarea = screen.getByLabelText('内容')
    textarea.focus()
    fireEvent.keyDown(textarea, { key: 'Delete' })
    fireEvent.keyDown(textarea, { key: 'g' })
    fireEvent.keyDown(textarea, { key: 'a', ctrlKey: true })
    expect(sceneData().notes).toHaveLength(1)
    expect(sceneData().items.every(entry => !entry.grayscale)).toBe(true)
  })
})

describe('dynamic z-order', () => {
  const item = (imageId: number, x = imageId * 100) => ({
    image_id: imageId, x, y: 10, width: 80, height: 60, rotation: 0,
    flip_x: false, grayscale: false, opacity: 1, crop: null,
  })
  const sceneOf = (...ids: number[]): PipScene => ({ ...emptyScene(), items: ids.map(id => item(id)) })

  function statefulApi(initial: PipScene) {
    let revision = 1
    let stored = initial
    const get = vi.fn(async () => ({ revision, scene: stored }))
    const save = vi.fn(async (_id: number, expected: number, next: PipScene) => {
      if (expected !== revision) throw new PipSceneConflict(revision, stored)
      revision += 1
      stored = next
      return { revision, scene: next }
    })
    return { get, save } as unknown as PipSceneApi & { get: ReturnType<typeof vi.fn>; save: ReturnType<typeof vi.fn> }
  }

  it('raises the clicked image to the top', async () => {
    const api = fakeApi({ revision: 1, scene: sceneOf(1, 2) })
    await renderBoard({ api, images: [image(1), image(2)] })
    expect(sceneData().items.map(entry => entry.image_id)).toEqual([1, 2])
    fireEvent.click(screen.getByText('canvas-select-1'))
    expect(sceneData().items.map(entry => entry.image_id)).toEqual([2, 1])
  })

  it('raises a multi-selection as one group, preserving its internal order', async () => {
    const api = fakeApi({ revision: 1, scene: sceneOf(2, 3, 1) })
    await renderBoard({ api, images: [image(1), image(2), image(3)] })
    fireEvent.click(screen.getByText('canvas-select-multi'))
    expect(sceneData().items.map(entry => entry.image_id)).toEqual([1, 2, 3])
    expect(screen.getByTestId('pip-multi-tools')).toBeInTheDocument()
  })

  it('does not put the z-order change into the undo stack', async () => {
    const api = fakeApi({ revision: 1, scene: sceneOf(1, 2) })
    await renderBoard({ api, images: [image(1), image(2)] })
    fireEvent.click(screen.getByText('canvas-select-1'))
    openCanvasMenu()
    expect(screen.queryByRole('button', { name: '撤销' })).not.toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    expect(sceneData().items.map(entry => entry.image_id)).toEqual([2, 1])
  })

  it('persists the new order and restores it after reopening', async () => {
    const api = statefulApi(sceneOf(1, 2))
    const first = await renderBoard({ api, images: [image(1), image(2)] })
    fireEvent.click(screen.getByText('canvas-select-1'))
    expect(sceneData().items.map(entry => entry.image_id)).toEqual([2, 1])
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1), { timeout: 3000 })
    const saved = api.save.mock.calls[0] as [number, number, PipScene]
    expect(saved[2].items.map(entry => entry.image_id)).toEqual([2, 1])
    first.view.unmount()
    await renderBoard({ api, images: [image(1), image(2)] })
    expect(sceneData().items.map(entry => entry.image_id)).toEqual([2, 1])
  })
})
describe('reset image', () => {
  const transformed = {
    image_id: 1, x: 120, y: 80, width: 450, height: 120, rotation: 30,
    flip_x: true, grayscale: true, opacity: 0.4, crop: { x: 0.2, y: 0.1, width: 0.5, height: 0.6 },
  }

  it('restores the freshly dropped state around the same center and can be undone', async () => {
    const api = fakeApi({ revision: 2, scene: { ...emptyScene(), items: [transformed] } })
    await renderBoard({ api, images: [image(1, { width: 400, height: 300 })] })
    fireEvent.click(screen.getByText('canvas-select-1'))
    const before = sceneData().items.find(entry => entry.image_id === 1)!
    const beforeBox = itemAABB(before)
    openImageMenu()
    fireEvent.click(screen.getByText('重置图片'))
    const reset = itemById(1)
    expect(reset).toMatchObject({
      width: 300, height: 225, rotation: 0, flip_x: false, grayscale: false, opacity: 1, crop: null,
    })
    const afterBox = itemAABB(reset)
    expect(afterBox.x + afterBox.width / 2).toBeCloseTo(beforeBox.x + beforeBox.width / 2, 6)
    expect(afterBox.y + afterBox.height / 2).toBeCloseTo(beforeBox.y + beforeBox.height / 2, 6)
    // Reset is a normal edit: undo returns the scaled/cropped/flipped state.
    openCanvasMenu()
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    expect(itemById(1)).toMatchObject({
      width: 450, height: 120, rotation: 30, flip_x: true, grayscale: true, opacity: 0.4,
      crop: { x: 0.2, y: 0.1, width: 0.5, height: 0.6 },
    })
  })

  it('falls back to the displayed aspect ratio when the library size is unknown', async () => {
    const cropped = {
      image_id: 1, x: 30, y: 40, width: 150, height: 100, rotation: 0,
      flip_x: false, grayscale: false, opacity: 1, crop: { x: 0.5, y: 0, width: 0.5, height: 1 },
    }
    const api = fakeApi({ revision: 2, scene: { ...emptyScene(), items: [cropped] } })
    await renderBoard({ api, images: [], imagesById: { 1: image(1, { width: null, height: null }) } })
    fireEvent.click(screen.getByText('canvas-select-1'))
    openImageMenu()
    fireEvent.click(screen.getByText('重置图片'))
    expect(itemById(1)).toMatchObject({ width: 300, height: 100, crop: null })
  })
})

describe('note editing and placeholders', () => {
  it('focuses the note editor on double-click without storing placeholder text', async () => {
    const api = fakeApi({ revision: 2, scene: { ...emptyScene(), items: [{
      image_id: 1, x: 10, y: 10, width: 100, height: 80, rotation: 0,
      flip_x: false, grayscale: false, opacity: 1, crop: null,
    }] } })
    await renderBoard({ api, images: [image(1)] })
    fireEvent.click(screen.getByRole('button', { name: '便签' }))
    const textarea = screen.getByLabelText('内容')
    expect(textarea).toHaveValue('')
    textarea.blur()
    fireEvent.click(screen.getByText('canvas-edit-note'))
    expect(document.activeElement).toBe(textarea)
    // The canvas placeholder is display-only; the scene stores an empty string.
    expect(sceneData().notes[0].text).toBe('')
    fireEvent.change(textarea, { target: { value: '留白' } })
    expect(sceneData().notes[0].text).toBe('留白')
  })

  it('normalizes the legacy placeholder saved by an older version and persists the cleanup', async () => {
    const legacy = { ...emptyScene(), notes: [{
      id: 'note-1', x: 5, y: 6, text: '双击编辑文字', color: '#f5d76e', font_size: 16,
    }] }
    const api = fakeApi({ revision: 3, scene: legacy })
    await renderBoard({ api })
    expect(sceneData().notes[0].text).toBe('')
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1), { timeout: 3000 })
    const saved = api.save.mock.calls[0] as [number, number, PipScene]
    expect(saved[2].notes[0]).toMatchObject({ id: 'note-1', text: '' })
  })
})
describe('project switching while a save is pending', () => {
  const item = (imageId: number, x: number) => ({
    image_id: imageId, x, y: 10, width: 100, height: 80, rotation: 0,
    flip_x: false, grayscale: false, opacity: 1, crop: null,
  })
  const sceneA: PipScene = { ...emptyScene(), items: [item(1, 10)] }
  const sceneB: PipScene = { ...emptyScene(), items: [item(1, 500), item(2, 30)] }

  const board = (api: PipSceneApi, projectId: number) => (
    <PipBoard images={[image(1), image(2)]} projectId={projectId} win={window} sceneApi={api} openUrl={vi.fn()} />
  )

  it('never writes the new project scene with the old project id', async () => {
    const { api, pending } = deferredApi({
      1: { revision: 5, scene: sceneA },
      2: { revision: 7, scene: sceneB },
    })
    const view = render(board(api, 1))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1), { timeout: 3000 })
    const first = pending[0]
    expect(first.id).toBe(1)
    expect(first.expected).toBe(5)

    view.rerender(board(api, 2))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(sceneData().items.find(entry => entry.image_id === 1)!.x).toBe(500)
    // Project 2 becomes dirty while project 1's save is still in flight.
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '水平翻转' }))
    await act(async () => { first.resolve({ revision: 6, scene: first.scene }) })

    // The next save must belong to project 2 and use its revision, never id=1 with B's scene.
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(2), { timeout: 3000 })
    const second = pending[1]
    expect(second.id).toBe(2)
    expect(second.expected).toBe(7)
    expect(second.scene.items.find(entry => entry.image_id === 1)).toMatchObject({ flip_x: true })
    expect(api.save.mock.calls.filter(call => call[0] === 1)).toHaveLength(1)
    expect(screen.getByTestId('pip-status')).toHaveAttribute('data-state', 'saving')
    await act(async () => { second.resolve({ revision: 8, scene: second.scene }) })
    await waitFor(() => expect(screen.getByTestId('pip-status')).toHaveAttribute('data-state', 'saved'), { timeout: 3000 })
  })

  it('keeps revisions isolated across A -> B -> A', async () => {
    const { api, pending, state } = deferredApi({
      1: { revision: 5, scene: sceneA },
      2: { revision: 7, scene: sceneB },
    })
    const view = render(board(api, 1))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1), { timeout: 3000 })

    view.rerender(board(api, 2))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '水平翻转' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(2), { timeout: 3000 })
    const projectTwoSave = pending[1]
    expect(projectTwoSave.id).toBe(2)

    // Switch back to project 1 while project 2's save is still unresolved, then edit project 1.
    view.rerender(board(api, 1))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(sceneData().items.find(entry => entry.image_id === 1)!.x).toBe(10)
    fireEvent.click(screen.getByText('canvas-select-1'))
    fireEvent.click(screen.getByRole('button', { name: '灰度' }))
    state.set(2, { revision: 8, scene: sceneB })
    await act(async () => { projectTwoSave.resolve({ revision: 8, scene: projectTwoSave.scene }) })

    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(3), { timeout: 3000 })
    const projectOneSave = pending[2]
    expect(projectOneSave.id).toBe(1)
    expect(projectOneSave.expected).toBe(5)
    expect(projectOneSave.scene.items.find(entry => entry.image_id === 1)).toMatchObject({ grayscale: true })
    // Project 2's scene must never be written under project 1's id, and vice versa.
    expect(pending[1].scene.items.find(entry => entry.image_id === 1)).toMatchObject({ x: 500, flip_x: true })
    await act(async () => { projectOneSave.resolve({ revision: 6, scene: projectOneSave.scene }) })
    await waitFor(() => expect(screen.getByTestId('pip-status')).toHaveAttribute('data-state', 'saved'), { timeout: 3000 })
  })
})
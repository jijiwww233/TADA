import { useImperativeHandle, type Ref } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import App from '../../App'
import type { ImageItem, ImageDetail as Detail } from '../../api'
import type { PipHandle } from '../../PipButton'
import type { RecommendationResponse } from '../../recommendation/contract'
import { createProjectApi, ProjectApiError } from '../api'
import type { ProjectApi } from '../contract'
import { detail, fakeApi, image, pin } from './fixtures'

vi.mock('../../SyncBar', () => ({ default: () => null }))
vi.mock('../../AutoSyncControl', () => ({ default: () => null }))
vi.mock('../../AnalysisBar', () => ({ default: () => null }))
vi.mock('../../DropZone', () => ({ default: () => null }))
const pipProbe = vi.hoisted(() => ({ images: [] as ImageItem[], opened: [] as number[][] }))
vi.mock('../../PipButton', () => ({ default: function MockPipButton({ images, ref }: { images: ImageItem[]; ref?: Ref<PipHandle> }) {
  pipProbe.images = images
  useImperativeHandle(ref, () => ({ supported: true, open: async () => { pipProbe.opened.push(images.map(i => i.id)) } }), [images])
  return <output data-testid="pip-input">{images.map(i => i.id).join(',')}</output>
} }))
beforeEach(() => { pipProbe.images = []; pipProbe.opened = [] })

/** Fixture transport around scripted server outputs; production adapter serializes all calls. */
function transport(server: ProjectApi) {
  return vi.fn<typeof fetch>(async (input, options) => {
    const path = String(input).replace('/api/projects',''); const method = options?.method ?? 'GET'
    const data = options?.body ? JSON.parse(String(options.body)) : undefined
    try {
      let result: unknown
      const parts = path.split('/').filter(Boolean)
      if (!parts.length) result = method === 'POST' ? await server.createProject(data) : await server.listProjects()
      else if (parts.length === 1) result = method === 'PATCH' ? await server.updateProject(Number(parts[0]),data) : await server.getProject(Number(parts[0]))
      else if (method === 'DELETE') result = { pin: await server.removePurpose(Number(parts[0]),Number(parts[2]),parts[4]) }
      else result = method === 'POST' ? await server.pinImage(Number(parts[0]),data.image_id,data.purposes,data.note) : await server.listPins(Number(parts[0]))
      return new Response(JSON.stringify(result),{status:200})
    } catch { return new Response(JSON.stringify({detail:{code:'project_store_unavailable'}}),{status:503}) }
  })
}
const recommended = (): RecommendationResponse => ({ status: 'ok', effective_query: '建筑展板', notices: [],
  images: [{ ...image(42), score: .7 }, { ...image(43), score: .6 }, { ...image(44), score: .5 }],
  columns: [
    { purpose: 'layout', label: '排版', status: 'ok', recall_source: 'layout_v1', reviewed_ids: [42],
      usage: { requests: 1, prompt_tokens: 10, completion_tokens: 5, latency_ms: 2 },
      items: [{ image_id: 42, rank: 1, recall_rank: 1, post_ids: [5], media_scope: 'image',
        reference_point: '多栏信息层级', purpose: 'layout' }] },
    { purpose: 'palette', label: '配色', status: 'ok', recall_source: 'description_v1', reviewed_ids: [43],
      usage: { requests: 1, prompt_tokens: 10, completion_tokens: 5, latency_ms: 2 },
      items: [{ image_id: 43, rank: 1, recall_rank: 1, post_ids: [5], media_scope: 'image',
        reference_point: '配色关系', purpose: 'palette' }] },
    { purpose: 'style', label: '风格', status: 'ok', recall_source: 'style_v1', reviewed_ids: [44],
      usage: { requests: 1, prompt_tokens: 10, completion_tokens: 5, latency_ms: 2 },
      items: [{ image_id: 44, rank: 1, recall_rank: 1, post_ids: [5], media_scope: 'image',
        reference_point: '极简气质', purpose: 'style' }] },
  ] })
async function setup(server = fakeApi(), analysis: Detail['analysis'] = null) {
  const fetcher = transport(server); const user = userEvent.setup(); const rec = vi.fn(async () => recommended())
  const library = { id:70,post_id:5,position:129,url:'/images/70.png',width:10,height:10,original_name:'库中图',created_at:'',platform:'xhs' as const }
  const loader = vi.fn(async (id: number) => ({ id, url:`/images/${id}.png`,width:1080,height:1440,original_name:`详情 ${id}`,created_at:'',
    posts:[{id:5,platform:'xhs',url:'https://example.org/work',title:'来源',position:129}],analysis,colors:[] }))
  const view = render(<App projectApi={createProjectApi(fetcher)} libraryApi={async () => [library]} detailLoader={loader}
    searchApi={async () => ({status:'index_empty',items:[]})} recommendationApi={rec} />)
  await screen.findByRole('option',{name:'项目 1'})
  return {server,user,fetcher,rec,loader,view,library}
}
async function select(user: ReturnType<typeof userEvent.setup>, id='1') {
  await user.selectOptions(screen.getByRole('combobox',{name:'当前项目'}),id)
  await screen.findByRole('list',{name:'项目钉选'})
}
async function recommend(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole('textbox',{name:'当前创作要求'}),'现代建筑展板')
  await user.click(screen.getByRole('button',{name:'获取推荐'}))
  return screen.getByRole('list',{name:'排版推荐'})
}

it('mounted create/select/edit project uses exact real adapter routes without automatic pins', async () => {
  const h=await setup()
  await h.user.type(screen.getByRole('textbox',{name:'新项目名称'}),'期末项目')
  await h.user.click(screen.getByRole('button',{name:'创建项目'}))
  expect(screen.getByRole('combobox',{name:'当前项目'})).toHaveValue('3')
  const box=screen.getByRole('textbox',{name:'项目 brief'}); await h.user.clear(box); await h.user.type(box,'现代建筑')
  await h.user.click(screen.getByRole('button',{name:'保存项目 brief'}))
  expect(h.server.updateProject).toHaveBeenCalledWith(3,{brief:'现代建筑'})
  expect(h.server.pinImage).not.toHaveBeenCalled()
})
it('recommendation → explicit pin → detail adds purpose → project removes last purpose → existing PiP', async () => {
  const h=await setup(); await select(h.user)
  const cards=await recommend(h.user)
  expect(h.server.pinImage).not.toHaveBeenCalled(); expect(pipProbe.images).toEqual([])
  const cardGroup=within(cards).getByRole('group',{name:'钉选图片 42：确认用途'})
  vi.mocked(h.server.pinImage).mockResolvedValueOnce(pin(1,42))
  await h.user.click(within(cardGroup).getByRole('button',{name:'确认钉选'}))
  expect(h.server.pinImage).toHaveBeenLastCalledWith(1,42,['layout'],undefined)
  const sharedEntity=pipProbe.images[0]
  expect(sharedEntity.id).toBe(42); expect(sharedEntity.position).toBe(129)
  expect(sharedEntity).not.toHaveProperty('purposes')
  await h.user.click(screen.getByRole('button',{name:'使用项目钉选作为小窗来源'}))
  expect(pipProbe.opened).toEqual([[42]])
  await h.user.click(within(cards).getByRole('button',{name:'查看图片 42'}))
  await screen.findByRole('heading',{name:'详情 42'})
  const detailUi=screen.getByRole('region',{name:'详情项目钉选'})
  const detailGroup=within(detailUi).getByRole('group',{name:'钉选图片 42：确认用途'})
  await h.user.click(within(detailGroup).getByRole('checkbox',{name:'风格'}))
  vi.mocked(h.server.pinImage).mockResolvedValueOnce(pin(1,42,['layout','style']))
  await h.user.click(within(detailGroup).getByRole('button',{name:'确认钉选'}))
  expect(h.server.pinImage).toHaveBeenLastCalledWith(1,42,['layout','style'],undefined)
  expect(pipProbe.images[0]).toBe(sharedEntity) // pin does not clone/replace the image
  vi.mocked(h.server.removePurpose).mockResolvedValueOnce(pin(1,42,['style']))
  await h.user.click(within(detailUi).getByRole('button',{name:'移除详情用途 layout #42'}))
  expect(within(detailUi).queryByRole('button',{name:'移除详情用途 layout #42'})).not.toBeInTheDocument()
  await h.user.click(screen.getByRole('button',{name:'关闭'}))
  vi.mocked(h.server.removePurpose).mockResolvedValueOnce(null)
  await h.user.click(screen.getByRole('button',{name:'移除 style #42'}))
  expect(pipProbe.images).toEqual([])
  expect(within(cards).getByRole('button',{name:'查看图片 42'})).toBeInTheDocument()
  expect(screen.getByText('已收录 1 张')).toBeInTheDocument()
  await h.user.selectOptions(screen.getByRole('combobox',{name:'当前项目'}),'')
  expect(pipProbe.images.map(i=>i.id)).toEqual([70])
  expect(h.rec).toHaveBeenCalledTimes(1)
})
it('all three column cards pin with their own purpose', async () => {
  const h=await setup(); await select(h.user)
  await recommend(h.user)
  const purposeFor = { '排版推荐': 'layout', '配色推荐': 'palette', '风格推荐': 'style' } as const
  for (const [name, purpose] of Object.entries(purposeFor)) {
    const list=screen.getByRole('list',{name})
    const group=within(list).getByRole('group',{name:/钉选图片/})
    vi.mocked(h.server.pinImage).mockResolvedValueOnce(pin(1, Number(within(list).getAllByRole('listitem')[0].getAttribute('data-image-id')), [purpose]))
    await h.user.click(within(group).getByRole('button',{name:'确认钉选'}))
    expect(h.server.pinImage).toHaveBeenLastCalledWith(1, expect.any(Number), [purpose], undefined)
  }
  expect(h.server.pinImage).toHaveBeenCalledTimes(3)
})

it('pinned recommendation survives page reload and feeds PiP', async () => {
  const h=await setup(); await select(h.user); const cards=await recommend(h.user)
  vi.mocked(h.server.pinImage).mockResolvedValueOnce(pin(1,42))
  const group=within(cards).getByRole('group',{name:'钉选图片 42：确认用途'})
  await h.user.click(within(group).getByRole('button',{name:'确认钉选'}))
  expect(pipProbe.images.map(i=>i.id)).toEqual([42])
  vi.mocked(h.server.getProject).mockResolvedValue(detail(1,[pin(1,42,['layout'])]))
  h.view.unmount()
  render(<App projectApi={createProjectApi(transport(h.server))} libraryApi={async () => [h.library]}
    detailLoader={h.loader} searchApi={async () => ({status:'index_empty',items:[]})} recommendationApi={h.rec} />)
  await screen.findByRole('option',{name:'项目 1'})
  await select(h.user)
  expect(screen.getByRole('button',{name:'移除 layout #42'})).toBeInTheDocument()
  expect(pipProbe.images.map(i=>i.id)).toEqual([42])
})

it('same image in two projects keeps purposes separate and switches PiP source', async () => {
  const h=await setup(); await select(h.user); const cards=await recommend(h.user)
  vi.mocked(h.server.pinImage).mockResolvedValueOnce(pin(1,42,['layout','style']))
  await h.user.click(within(cards).getByRole('button',{name:'确认钉选'}))
  await select(h.user,'2')
  expect(pipProbe.images).toEqual([])
  const group=within(cards).getByRole('group',{name:'钉选图片 42：确认用途'})
  await h.user.click(within(group).getByRole('checkbox',{name:'排版'}))
  await h.user.click(within(group).getByRole('checkbox',{name:'配色'}))
  vi.mocked(h.server.pinImage).mockResolvedValueOnce(pin(2,42,['palette']))
  await h.user.click(within(group).getByRole('button',{name:'确认钉选'}))
  expect(h.server.pinImage).toHaveBeenLastCalledWith(2,42,['palette'],undefined)
  vi.mocked(h.server.getProject).mockResolvedValueOnce(detail(1,[pin(1,42,['layout','style'])]))
  await select(h.user,'1')
  expect(screen.getByRole('button',{name:'移除 layout #42'})).toBeInTheDocument()
  expect(screen.queryByRole('button',{name:'移除 palette #42'})).not.toBeInTheDocument()
})
it('failed explicit pin leaves confirmed UI and image entity intact, without hidden retry', async () => {
  const h=await setup(); await select(h.user); const cards=await recommend(h.user)
  vi.mocked(h.server.pinImage).mockRejectedValueOnce(new ProjectApiError('project_store_unavailable',503))
  await h.user.click(within(cards).getByRole('button',{name:'确认钉选'}))
  expect(screen.getByText('项目还没有钉选图片。')).toBeInTheDocument()
  expect(h.server.pinImage).toHaveBeenCalledTimes(1)
  expect(within(cards).getByRole('button',{name:'查看图片 42'})).toBeInTheDocument()
  expect(pipProbe.images).toEqual([])
})

it('legacy partial layout still opens detail and allows manual pin instead of blanking App', async () => {
  const legacy = { status:'done', processing:false, model:'fixture', description:'建筑参考', subject:['建筑'],
    layout:{columns:['multi']}, style:{tags:['现代']}, visible_text:'', attempts:0,prompt_tokens:null,completion_tokens:null,error:null,updated_at:'' } as unknown as NonNullable<Detail['analysis']>
  const h=await setup(fakeApi(),legacy); await select(h.user); const cards=await recommend(h.user)
  await h.user.click(within(cards).getByRole('button',{name:'查看图片 42'}))
  await screen.findByRole('heading',{name:'详情 42'})
  expect(screen.getAllByText('未记录')).toHaveLength(4)
  const group=within(screen.getByRole('region',{name:'详情项目钉选'})).getByRole('group',{name:'钉选图片 42：确认用途'})
  await h.user.click(within(group).getByRole('checkbox',{name:'排版'}))
  vi.mocked(h.server.pinImage).mockResolvedValueOnce(pin(1,42))
  await h.user.click(within(group).getByRole('button',{name:'确认钉选'}))
  expect(h.server.pinImage).toHaveBeenCalledExactlyOnceWith(1,42,['layout'],undefined)
})

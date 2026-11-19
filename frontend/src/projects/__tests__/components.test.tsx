import { useSyncExternalStore } from 'react'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { ProjectBriefEditor, PinControl } from '../components'
import { ProjectPinAction, ProjectsPanel } from '../integration'
import { ProjectApiError } from '../api'
import type { Pin } from '../contract'
import { deferred, detail, fakeApi, harness, pin, project } from './fixtures'

async function setup(api = fakeApi()) {
  const h = harness(api); const user = userEvent.setup(); const onPip = vi.fn(); const onOpen = vi.fn()
  function Host() {
    const imagesById = useSyncExternalStore(h.subscribe, h.entities)
    return <>
      <ProjectsPanel store={h.store} imagesById={imagesById} libraryIds={[70]} onOpen={onOpen} onPip={onPip} />
      <article aria-label="推荐卡片"><ProjectPinAction store={h.store} imageId={42} suggestedPurposes={['layout','style']} /></article>
    </>
  }
  render(<Host />); await screen.findByRole('option', { name: '项目 1' })
  return { ...h, user, onPip, onOpen }
}
async function select(user: ReturnType<typeof userEvent.setup>, id = '1') {
  await user.selectOptions(screen.getByRole('combobox', { name: '当前项目' }), id)
  await screen.findByRole('list', { name: '项目钉选' })
}
function pinGroup() { return within(screen.getByRole('article', { name: '推荐卡片' })).getByRole('group', { name: '钉选图片 42：确认用途' }) }

it('create/select/view details/edit brief without a project note field', async () => {
  const h = await setup(); await select(h.user)
  expect(screen.getByRole('heading', { name: '项目 1' })).toBeInTheDocument()
  const brief = screen.getByRole('textbox', { name: '项目 brief' })
  await h.user.clear(brief); await h.user.type(brief, '现代建筑')
  await h.user.click(screen.getByRole('button', { name: '保存项目 brief' }))
  expect(h.api.updateProject).toHaveBeenCalledWith(1,{ brief: '现代建筑' })
  await h.user.type(screen.getByRole('textbox', { name: '新项目名称' }), '新项目')
  await h.user.type(screen.getByRole('textbox', { name: '新项目 brief' }), '海报')
  await h.user.click(screen.getByRole('button', { name: '创建项目' }))
  expect(h.api.createProject).toHaveBeenCalledWith({ name: '新项目', brief: '海报' })
  expect(screen.getByRole('combobox', { name: '当前项目' })).toHaveValue('3')
  expect(screen.queryByRole('textbox', { name: '项目 note' })).not.toBeInTheDocument()
})
it('recommendation suggestions do not auto pin; user can edit multiple purposes before confirming', async () => {
  const h = await setup(); await select(h.user)
  expect(h.api.pinImage).not.toHaveBeenCalled(); expect(h.onPip).not.toHaveBeenCalled()
  await h.user.click(within(pinGroup()).getByRole('checkbox', { name: '风格' }))
  await h.user.click(within(pinGroup()).getByRole('checkbox', { name: '配色' }))
  vi.mocked(h.api.pinImage).mockResolvedValue(pin(1,42,['layout','palette']))
  await h.user.click(within(pinGroup()).getByRole('button', { name: '确认钉选' }))
  expect(h.api.pinImage).toHaveBeenCalledExactlyOnceWith(1,42,['layout','palette'],undefined)
  expect(screen.getByRole('button', { name: '移除 layout #42' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '移除 palette #42' })).toBeInTheDocument()
})
it('untouched pin note is omitted, explicit clearing sends empty string', async () => {
  const api = fakeApi(); vi.mocked(api.getProject).mockResolvedValue(detail(1,[pin(1,42)]))
  vi.mocked(api.pinImage).mockResolvedValue(pin(1,42))
  const h = await setup(api); await select(h.user)
  const note = within(pinGroup()).getByRole('textbox', { name: '钉选备注' })
  expect(note).toHaveValue('旧备注')
  await h.user.click(within(pinGroup()).getByRole('button', { name: '确认钉选' }))
  expect(api.pinImage).toHaveBeenLastCalledWith(1,42,['layout'],undefined)
  await h.user.clear(within(pinGroup()).getByRole('textbox', { name: '钉选备注' })); await h.user.click(within(pinGroup()).getByRole('button', { name: '确认钉选' }))
  expect(api.pinImage).toHaveBeenLastCalledWith(1,42,['layout'],'')
})
it('remove one purpose keeps the image; last removal clears only the pin, PiP reads exact IDs', async () => {
  const api = fakeApi(); vi.mocked(api.getProject).mockResolvedValue(detail(1,[pin(1,42,['layout','style'])]))
  vi.mocked(api.removePurpose).mockResolvedValueOnce(pin(1,42,['style'])).mockResolvedValueOnce(null)
  const h = await setup(api); await select(h.user)
  await h.user.click(screen.getByRole('button', { name: '使用项目钉选作为小窗来源' }))
  expect(h.onPip.mock.calls[0][0].map((i: { id: number }) => i.id)).toEqual([42])
  await h.user.click(screen.getByRole('button', { name: '移除 layout #42' }))
  expect(screen.getByRole('button', { name: '移除 style #42' })).toBeInTheDocument()
  await h.user.click(screen.getByRole('button', { name: '移除 style #42' }))
  expect(within(screen.getByRole('list', { name: '项目钉选' })).queryByRole('button', { name: '查看图片 42' })).not.toBeInTheDocument()
  await h.user.click(screen.getByRole('button', { name: '使用项目钉选作为小窗来源' }))
  expect(h.onPip).toHaveBeenLastCalledWith([]); expect(h.entities()[42]).toBeDefined()
})
it('API error renders fixed feedback, refresh retries only the read', async () => {
  const api = fakeApi(); vi.mocked(api.getProject).mockRejectedValueOnce(new ProjectApiError('project_store_unavailable',503)).mockResolvedValueOnce(detail(1))
  const h = await setup(api); await h.user.selectOptions(screen.getByRole('combobox', { name: '当前项目' }), '1')
  expect(await screen.findByRole('alert')).toHaveTextContent('项目服务暂不可用')
  await h.user.click(screen.getByRole('button', { name: '重试' }))
  expect(await screen.findByRole('list', { name: '项目钉选' })).toBeInTheDocument()
  expect(api.pinImage).not.toHaveBeenCalled()
})
it('pin in flight disables repeated confirmation and failed write leaves no optimistic pin', async () => {
  const h = await setup(); await select(h.user); const gate = deferred<Pin>()
  vi.mocked(h.api.pinImage).mockReturnValue(gate.promise)
  await h.user.click(within(pinGroup()).getByRole('button', { name: '确认钉选' }))
  expect(within(pinGroup()).getByRole('button', { name: '保存中…' })).toBeDisabled()
  await act(async () => gate.reject(new ProjectApiError('project_store_unavailable',503)))
  expect(screen.getByText('项目还没有钉选图片。')).toBeInTheDocument()
  expect(h.api.pinImage).toHaveBeenCalledTimes(1)
})
it('standalone editor resets when switching project identity', () => {
  const view = render(<ProjectBriefEditor project={project(1)} busy={false} save={vi.fn()} />)
  view.rerender(<ProjectBriefEditor project={project(2)} busy={false} save={vi.fn()} />)
  expect(screen.getByRole('textbox', { name: '项目 brief' })).toHaveValue('brief 2')
})
it('standalone PinControl accepts only backend declared purposes and waits for confirmation', async () => {
  const save = vi.fn(); const user = userEvent.setup()
  render(<PinControl imageId={42} allowed={['layout','future']} suggested={['layout','not_allowed']} enabled busy={false} pin={save} />)
  expect(save).not.toHaveBeenCalled(); expect(screen.queryByRole('checkbox', { name: 'not_allowed' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('checkbox', { name: 'future' })); await user.click(screen.getByRole('button', { name: '确认钉选' }))
  expect(save).toHaveBeenCalledExactlyOnceWith(['layout','future'],undefined)
})

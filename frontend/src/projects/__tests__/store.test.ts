import { expect, it, vi } from 'vitest'
import type { Pin, PinsResponse, Project, ProjectDetail } from '../contract'
import { projectPipIds, sendProjectPinsToPip } from '../pip'
import { deferred, detail, fakeApi, harness, image, pin, project, purposes } from './fixtures'

it('creates/selects project with server detail and only merges images into the shared host map', async () => {
  const h = harness(); await h.store.loadProjects(); await h.store.createProject('新项目', '新 brief')
  expect(h.store.snapshot().activeProjectId).toBe(3)
  expect(h.api.getProject).toHaveBeenCalledWith(3)
  expect(h.store.snapshot().projects.find(p => p.id === 3)?.brief).toBe('brief 3') // detail is authoritative
  expect(h.store.snapshot()).not.toHaveProperty('imagesById')
  expect(h.store.snapshot()).not.toHaveProperty('images')
})
it('duplicate pin uses server merge and one pin can have multiple purposes', async () => {
  const h = harness(); await h.store.selectProject(1)
  vi.mocked(h.api.pinImage).mockResolvedValueOnce(pin(1, 42)).mockResolvedValueOnce(pin(1, 42, ['layout','style']))
  await h.store.pinImage(42, ['layout']); await h.store.pinImage(42, ['style'])
  expect(h.store.snapshot().pinsByProject[1]).toHaveLength(1)
  expect(h.store.snapshot().pinsByProject[1][0].purposes).toEqual(['layout','style'])
  expect(h.api.pinImage).toHaveBeenLastCalledWith(1, 42, ['style'], undefined)
})
it('one use removal keeps the others; last removes pin only, never shared entities', async () => {
  const api = fakeApi(); vi.mocked(api.getProject).mockResolvedValue(detail(1, [pin(1, 42, ['layout','style'])]))
  const h = harness(api); await h.store.selectProject(1)
  const entity = h.entities()[42]
  vi.mocked(api.removePurpose).mockResolvedValueOnce(pin(1,42,['style'])).mockResolvedValueOnce(null)
  await h.store.removePurpose(42, 'layout'); expect(h.store.snapshot().pinsByProject[1][0].purposes).toEqual(['style'])
  await h.store.removePurpose(42, 'style'); expect(h.store.snapshot().pinsByProject[1]).toEqual([])
  expect(h.entities()[42]).toBe(entity); expect(h.entities()[70]).toBeDefined()
})
it('stale project success cannot override current project or merge stale images', async () => {
  const gate = deferred<ProjectDetail>(); const h = harness()
  vi.mocked(h.api.getProject).mockReturnValueOnce(gate.promise).mockResolvedValueOnce(detail(2, [pin(2,43)]))
  const first = h.store.selectProject(1); await h.store.selectProject(2)
  gate.resolve(detail(1, [pin(1,42)])); await first
  expect(h.store.snapshot().activeProjectId).toBe(2); expect(h.store.snapshot().pinsByProject[1]).toBeUndefined()
  expect(h.entities()[42]).toBeUndefined(); expect(h.entities()[43]).toBeDefined()
})
it('stale project failure cannot replace newer success', async () => {
  const gate = deferred<ProjectDetail>(); const h = harness()
  vi.mocked(h.api.getProject).mockReturnValueOnce(gate.promise).mockResolvedValueOnce(detail(2))
  const first = h.store.selectProject(1); await h.store.selectProject(2); gate.reject(new Error('old')); await first
  expect(h.store.snapshot().detailState.status).toBe('ok')
})
it('deselect invalidates pending detail and restores only library PiP IDs', async () => {
  const gate = deferred<ProjectDetail>(); const h = harness(); vi.mocked(h.api.getProject).mockReturnValue(gate.promise)
  const first = h.store.selectProject(1); await h.store.selectProject(null); gate.resolve(detail(1,[pin(1,42)])); await first
  expect(h.merge).not.toHaveBeenCalled(); expect(projectPipIds(h.store.snapshot(), [70])).toEqual([70])
})
it('pin failure is not optimistic, clears busy and permits explicit retry', async () => {
  const h = harness(); await h.store.selectProject(1)
  vi.mocked(h.api.pinImage).mockRejectedValueOnce(new Error('private raw error')).mockResolvedValueOnce(pin(1,42))
  await h.store.pinImage(42,['layout'])
  expect(h.store.snapshot().pinsByProject[1]).toEqual([]); expect(h.store.snapshot().busyPins).toEqual([])
  expect(h.store.snapshot().pinError).not.toContain('private')
  await h.store.pinImage(42,['layout']); expect(h.store.snapshot().pinsByProject[1]).toHaveLength(1)
})
it('remove failure preserves existing pin without needing an optimistic rollback', async () => {
  const h = harness(); vi.mocked(h.api.getProject).mockResolvedValue(detail(1,[pin(1,42)])); await h.store.selectProject(1)
  vi.mocked(h.api.removePurpose).mockRejectedValueOnce(new Error('private'))
  await h.store.removePurpose(42,'layout'); expect(h.store.snapshot().pinsByProject[1]).toHaveLength(1)
})
it('in-flight duplicate pin is blocked and late result stays in its captured project', async () => {
  const h = harness(); await h.store.selectProject(1); const gate = deferred<Pin>()
  vi.mocked(h.api.pinImage).mockReturnValue(gate.promise)
  const saving = h.store.pinImage(42,['layout']); await h.store.pinImage(42,['style']); await h.store.selectProject(2)
  gate.resolve(pin(1,42)); await saving
  expect(h.api.pinImage).toHaveBeenCalledTimes(1); expect(h.store.snapshot().pinsByProject[2]).toEqual([])
  expect(h.store.snapshot().pinsByProject[1][0].image_id).toBe(42)
  expect(projectPipIds(h.store.snapshot(),[])).toEqual([])
})
it('late pin listing cannot undo a completed pin; read retries, write does not', async () => {
  const h = harness(); await h.store.selectProject(1)
  const saving = deferred<Pin>(); const listing = deferred<PinsResponse>()
  vi.mocked(h.api.pinImage).mockReturnValue(saving.promise)
  vi.mocked(h.api.listPins).mockReturnValueOnce(listing.promise).mockResolvedValueOnce({ items: [pin(1,42)], images: [image(42)], allowed_purposes: purposes })
  const write = h.store.pinImage(42,['layout']); const read = h.store.refreshPins()
  saving.resolve(pin(1,42)); await write
  listing.resolve({ items: [], images: [], allowed_purposes: purposes }); await read
  expect(h.store.snapshot().pinsByProject[1]).toHaveLength(1)
  expect(h.api.pinImage).toHaveBeenCalledTimes(1); expect(h.api.listPins).toHaveBeenCalledTimes(2)
})
it('pending create cannot force selection back after user selected another project', async () => {
  const h = harness(); const gate = deferred<Project>(); vi.mocked(h.api.createProject).mockReturnValue(gate.promise)
  const creating = h.store.createProject('new',''); await h.store.selectProject(2); gate.resolve(project(3)); await creating
  expect(h.store.snapshot().activeProjectId).toBe(2)
})
it('brief update does not change pins or invent a project note', async () => {
  const h = harness(); vi.mocked(h.api.getProject).mockResolvedValue(detail(1,[pin(1,42)])); await h.store.selectProject(1)
  const pins = h.store.snapshot().pinsByProject[1]
  await h.store.updateBrief('现代')
  expect(h.api.updateProject).toHaveBeenCalledExactlyOnceWith(1,{brief:'现代'})
  expect(h.store.snapshot().pinsByProject[1]).toBe(pins)
})
it('PiP action gets only confirmed pinned shared entities; no search/recommendation entity leaks', async () => {
  const h = harness(); vi.mocked(h.api.getProject).mockResolvedValue(detail(1,[pin(1,42)])); await h.store.selectProject(1)
  const receive = vi.fn(); const result = sendProjectPinsToPip(h.store.snapshot(),h.entities(),[70],receive)
  expect(result.imageIds).toEqual([42]); expect(receive.mock.calls[0][0][0]).toBe(h.entities()[42])
  expect(result.imageIds).not.toContain(70)
})
it('loading/failed project never falls back to library in PiP; missing entities are reported', async () => {
  const h = harness(); const gate = deferred<ProjectDetail>(); vi.mocked(h.api.getProject).mockReturnValueOnce(gate.promise)
  const pending = h.store.selectProject(1); expect(projectPipIds(h.store.snapshot(),[70])).toEqual([])
  gate.reject(new Error('offline')); await pending; expect(projectPipIds(h.store.snapshot(),[70])).toEqual([])
  vi.mocked(h.api.getProject).mockResolvedValue(detail(1,[pin(1,42)])); await h.store.selectProject(1)
  expect(sendProjectPinsToPip(h.store.snapshot(),{},[],vi.fn()).missingIds).toEqual([42])
})
it('legacy unassigned pin is visible to UI but not confirmed for PiP', async () => {
  const h = harness(); vi.mocked(h.api.getProject).mockResolvedValue(detail(1,[pin(1,42,[])])); await h.store.selectProject(1)
  expect(h.store.snapshot().pinsByProject[1]).toHaveLength(1); expect(projectPipIds(h.store.snapshot(),[])).toEqual([])
})
it('rejects empty/unknown purposes without any save', async () => {
  const h = harness(); await h.store.selectProject(1)
  await h.store.pinImage(42,[]); await h.store.pinImage(42,['invented'])
  expect(h.api.pinImage).not.toHaveBeenCalled()
})

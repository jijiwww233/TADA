import { afterEach, expect, it, vi } from 'vitest'
import { createProjectApi } from '../api'
import { detail, image, pin, project, purposes } from './fixtures'

afterEach(() => vi.unstubAllGlobals())
function scripted(...values: unknown[]) {
  const fetcher = vi.fn<typeof fetch>()
  values.forEach(body => fetcher.mockResolvedValueOnce(new Response(JSON.stringify(body), { status: 200 })))
  return { api: createProjectApi(fetcher), fetcher }
}
it('GET projects retains items and allowed purposes envelope', async () => {
  const body = { items: [project(1)], allowed_purposes: purposes }
  const { api, fetcher } = scripted(body)
  expect(await api.listProjects()).toEqual(body)
  expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/projects', { method: 'GET' })
})
it('create sends name/brief only; PATCH updates brief without inventing project note', async () => {
  const { api, fetcher } = scripted(project(3), { ...project(3), brief: '新 brief' })
  await api.createProject({ name: '新项目', brief: '展板' }); await api.updateProject(3, { brief: '新 brief' })
  expect(fetcher.mock.calls.map(([url, options]) => [url, options?.method, JSON.parse(options?.body as string)])).toEqual([
    ['/api/projects', 'POST', { name: '新项目', brief: '展板' }], ['/api/projects/3', 'PATCH', { brief: '新 brief' }],
  ])
})
it('GET detail and pins preserve display info, canonical URL and raw position', async () => {
  const d = detail(1, [pin(1, 42)]); const list = { items: d.pins, images: d.images, allowed_purposes: purposes }
  const { api, fetcher } = scripted(d, list)
  expect(await api.getProject(1)).toEqual(d); expect(await api.listPins(1)).toEqual(list)
  expect(fetcher.mock.calls.map(c => c[0])).toEqual(['/api/projects/1', '/api/projects/1/pins'])
  expect(d.images[0].sources[0].position).toBe(129)
})
it('POST pin omits untouched note, explicit empty note clears it; trusts server merged purposes', async () => {
  const merged = pin(1, 42, ['layout', 'style'])
  const { api, fetcher } = scripted(pin(1, 42), merged, merged)
  await api.pinImage(1, 42, ['layout']); expect(await api.pinImage(1, 42, ['style'], '')).toEqual(merged)
  await api.pinImage(1, 42, ['style'], null)
  expect(fetcher.mock.calls.map(([url, options]) => [url, options?.method, JSON.parse(options?.body as string)])).toEqual([
    ['/api/projects/1/pins', 'POST', { image_id: 42, purposes: ['layout'] }],
    ['/api/projects/1/pins', 'POST', { image_id: 42, purposes: ['style'], note: '' }],
    ['/api/projects/1/pins', 'POST', { image_id: 42, purposes: ['style'], note: null }],
  ])
})
it('DELETE unwraps pin envelope and last-purpose null, without deleting images', async () => {
  const { api, fetcher } = scripted({ pin: pin(1, 42, ['style']) }, { pin: null })
  expect((await api.removePurpose(1, 42, 'layout'))?.purposes).toEqual(['style'])
  expect(await api.removePurpose(1, 42, 'style')).toBeNull()
  expect(fetcher.mock.calls.map(c => c[0])).toEqual([
    '/api/projects/1/pins/42/purposes/layout', '/api/projects/1/pins/42/purposes/style',
  ])
  expect(fetcher.mock.calls.every(c => c[1]?.method === 'DELETE' && c[1]?.body === undefined)).toBe(true)
})
it('missing deletion envelope is an error rather than accidental pin removal', async () => {
  const { api } = scripted({})
  await expect(api.removePurpose(1, 42, 'layout')).rejects.toMatchObject({ code: 'invalid_response' })
})
it('rejects cross-project/image responses before they reach state', async () => {
  const { api } = scripted(pin(2, 42))
  await expect(api.pinImage(1, 42, ['layout'])).rejects.toMatchObject({ code: 'invalid_response' })
})
it.each([404, 409, 422, 503])('HTTP %s exposes only known local errors and never retries', async status => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ detail: {
    code: status === 409 ? 'invalid_stored_purposes' : 'unknown Bearer private', message: 'raw secret provider body',
  } }), { status }))
  await expect(createProjectApi(fetcher).listProjects()).rejects.toMatchObject({ status,
    code: status === 409 ? 'invalid_stored_purposes' : 'request_failed' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it('network failure has a safe result-unknown message, no automatic write retry', async () => {
  const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error('secret raw failure'))
  await expect(createProjectApi(fetcher).createProject({ name: 'a', brief: '' })).rejects.toThrow('结果可能未知')
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it('does not accept malformed display/identity as success', async () => {
  const { api } = scripted({ ...detail(1), images: [{ ...image(42), url: null }] })
  await expect(api.getProject(1)).rejects.toMatchObject({ code: 'invalid_response' })
})

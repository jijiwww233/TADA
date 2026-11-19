import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import ImportPanel from './ImportPanel'
import { extractUrls } from './importLinks'

const api = vi.hoisted(() => ({
  getImportStatus: vi.fn(),
  importShare: vi.fn(),
  importCaptured: vi.fn(),
}))
vi.mock('./api', async importOriginal => ({ ...await importOriginal<typeof import('./api')>(), ...api }))

const NOTE = '0123456789abcdef01234567'

function status(blocked: boolean, last: object = {}) {
  return {
    blocked,
    manual_hold: blocked,
    message: blocked ? '小红书冷却中：联网导入暂停，两个入口暂时禁用' : null,
    last: { captured: null, share: null, ...last },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getImportStatus.mockResolvedValue(status(false))
})

it('keeps both entries disabled and labelled 冷却中 while the XHS lock is on', async () => {
  api.getImportStatus.mockResolvedValue(status(true))
  render(<ImportPanel onImported={() => {}} />)
  const buttons = await screen.findAllByRole('button', { name: '冷却中' })
  expect(buttons).toHaveLength(2)
  buttons.forEach((button) => expect(button).toBeDisabled())
  expect(screen.getByRole('textbox', { name: '分享链接' })).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent('平台冷却中')
  expect(api.importShare).not.toHaveBeenCalled()
})

it('pulls links out of pasted share text and reports new/duplicate counts', async () => {
  api.importShare.mockResolvedValue({
    mode: 'share', created: 1, skipped: 1, total: 2, failed: [],
    at: '2026-11-05T19:30:00+0900',
  })
  const imported = vi.fn()
  render(<ImportPanel onImported={imported} />)
  const box = await screen.findByRole('textbox', { name: '分享链接' })
  fireEvent.change(box, {
    target: {
      value: '复制这条信息 http://xhslink.com/a/abc，还有 ' +
        'https://www.xiaohongshu.com/explore/' + NOTE + ' 打开小红书',
    },
  })
  fireEvent.click(screen.getByRole('button', { name: '导入分享链接' }))
  await waitFor(() =>
    expect(api.importShare).toHaveBeenCalledWith([
      'http://xhslink.com/a/abc',
      'https://www.xiaohongshu.com/explore/' + NOTE,
    ]),
  )
  expect(await screen.findByText(/新增 1 条 · 重复跳过 1 条 · 失败 0 条/)).toBeTruthy()
  expect(imported).toHaveBeenCalled()
})

it('imports pasted capture JSON and rejects malformed input without a request', async () => {
  const imported = vi.fn()
  render(<ImportPanel onImported={imported} />)
  const box = await screen.findByRole('textbox', { name: '采集数据 JSON' })
  fireEvent.change(box, { target: { value: 'not json' } })
  fireEvent.click(screen.getByRole('button', { name: '导入采集数据' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('不是有效 JSON')
  expect(api.importCaptured).not.toHaveBeenCalled()

  api.importCaptured.mockResolvedValue({ mode: 'captured', created: 2, skipped: 0, total: 2, failed: [] })
  const payload = { notes: [{ note_id: NOTE }] }
  fireEvent.change(box, { target: { value: JSON.stringify(payload) } })
  fireEvent.click(screen.getByRole('button', { name: '导入采集数据' }))
  await waitFor(() => expect(api.importCaptured).toHaveBeenCalledWith(payload))
  expect(await screen.findByText(/新增 2 条 · 重复跳过 0 条/)).toBeTruthy()
  expect(imported).toHaveBeenCalled()
})

it('shows the sanitized failure link and never echoes the raw share url', async () => {
  api.importShare.mockResolvedValue({
    mode: 'share', created: 0, skipped: 0, total: 1,
    failed: [{ input_index: 1, link: 'https://www.xiaohongshu.com/explore/' + NOTE,
      note_id: NOTE, error: '网络请求失败' }],
  })
  render(<ImportPanel onImported={() => {}} />)
  const box = await screen.findByRole('textbox', { name: '分享链接' })
  fireEvent.change(box, { target: { value: 'https://www.xiaohongshu.com/explore/' + NOTE + '?xsec_token=SECRET' } })
  fireEvent.click(screen.getByRole('button', { name: '导入分享链接' }))
  const line = await screen.findByText(/网络请求失败/)
  expect(line.textContent).toContain('www.xiaohongshu.com/explore/')
  expect(line.textContent).not.toContain('SECRET')
  expect(document.querySelector('ul.error')?.textContent).not.toContain('SECRET')
})

it('shows the last outcome from the status payload before a new import', async () => {
  api.getImportStatus.mockResolvedValue(status(false, {
    share: { mode: 'share', created: 3, skipped: 2, total: 5, failed: [] },
  }))
  render(<ImportPanel onImported={() => {}} />)
  expect(await screen.findByText(/上次分享链接导入：新增 3 条 · 重复跳过 2 条/)).toBeTruthy()
})

it('extractUrls strips captions that run into the link', () => {
  expect(extractUrls('看这个 http://xhslink.com/a/xyz，顺便 https://www.xiaohongshu.com/explore/' + NOTE + ' 打开'))
    .toEqual(['http://xhslink.com/a/xyz', 'https://www.xiaohongshu.com/explore/' + NOTE])
  expect(extractUrls('没有链接')).toEqual([])
  expect(extractUrls('http://xhslink.com/a/xyz 与 http://xhslink.com/a/xyz')).toEqual(['http://xhslink.com/a/xyz'])
})

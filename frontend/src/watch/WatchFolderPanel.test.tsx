import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WatchFolderPanel from './WatchFolderPanel'
import type { WatchFolder, WatchStatus } from './contract'

vi.mock('./api', () => ({
  getWatchStatus: vi.fn(),
  addWatchFolder: vi.fn(),
  setWatchFolderEnabled: vi.fn(),
  setWatchFolderSubfolders: vi.fn(),
  setWatchAutoAnalysis: vi.fn(),
  removeWatchFolder: vi.fn(),
  setWatchDailyLimit: vi.fn(),
  analyzeNow: vi.fn(),
  scanWatchNow: vi.fn(),
}))

import * as api from './api'

const mocked = vi.mocked(api)

const folder = (patch: Partial<WatchFolder> = {}): WatchFolder => ({
  id: 1,
  path: 'D:/Pictures/a',
  enabled: true,
  include_subfolders: true,
  last_import_at: null,
  imported_count: 0,
  last_error: null,
  pending_files: 0,
  ...patch,
})

const status = (patch: Partial<WatchStatus> = {}): WatchStatus => ({
  allowed: true,
  watching: true,
  auto_analysis_enabled: false,
  analysis_configured: true,
  folders: [
    folder({ id: 1, path: 'D:/Pictures/a', enabled: true, last_import_at: '2026-11-07T05:00:00', imported_count: 12 }),
    folder({ id: 2, path: 'D:/Pictures/b', enabled: false, imported_count: 3, last_error: '读取失败', pending_files: 2 }),
  ],
  daily_limit: 50,
  analyzed_today: 4,
  deferred: 2,
  estimated_cost_today: 0.0123,
  estimated_cost_at_limit: 0.4567,
  prices: { input_per_million: 2, output_per_million: 8 },
  ...patch,
})

beforeEach(() => {
  vi.clearAllMocks()
  mocked.getWatchStatus.mockResolvedValue(status())
  mocked.addWatchFolder.mockResolvedValue(folder({ id: 9, path: 'D:/Pictures/new' }))
  mocked.setWatchFolderEnabled.mockResolvedValue(folder())
  mocked.setWatchFolderSubfolders.mockResolvedValue(folder({ include_subfolders: false }))
  mocked.setWatchAutoAnalysis.mockResolvedValue(undefined)
  mocked.removeWatchFolder.mockResolvedValue({ removed: true })
  mocked.setWatchDailyLimit.mockImplementation(async (dailyLimit: number) => ({ daily_limit: dailyLimit }))
  mocked.analyzeNow.mockResolvedValue({ queued: 2, started: true, deferred: 0 })
  mocked.scanWatchNow.mockResolvedValue({ folders: 1, imported: 2, duplicates: 1, failed: 0 })
})

describe('WatchFolderPanel', () => {
  it('renders folders, statuses, import counts and today cost stats', async () => {
    render(<WatchFolderPanel />)
    expect(await screen.findByText('自动导入文件夹')).toBeInTheDocument()
    expect(await screen.findByText('D:/Pictures/a')).toBeInTheDocument()
    expect(screen.getByText('D:/Pictures/b')).toBeInTheDocument()
    expect(screen.getByText('监听中')).toBeInTheDocument()
    expect(screen.getByText('已暂停')).toBeInTheDocument()
    expect(screen.getByText('累计导入 12 张')).toBeInTheDocument()
    expect(screen.getByText('累计导入 3 张')).toBeInTheDocument()
    expect(screen.getByText('读取失败')).toBeInTheDocument()
    expect(screen.getByText(/自动监听已开启/)).toBeInTheDocument()
    expect(screen.getByText(/今天已自动分析 4 张/)).toBeInTheDocument()
    expect(screen.getByText('自动分析：已暂停')).toBeInTheDocument()
    expect(screen.getByText(/预计花费约 ¥0.0123/)).toBeInTheDocument()
    expect(screen.getByText(/按上限 50 张约 ¥0.4567/)).toBeInTheDocument()
    expect(screen.getByText(/待自动分析 2 张/)).toBeInTheDocument()
    expect(mocked.getWatchStatus).toHaveBeenCalledTimes(1)
  })

  it('explains that a non-entry instance only supports manual scan', async () => {
    mocked.getWatchStatus.mockResolvedValue(status({ allowed: false, watching: false }))
    render(<WatchFolderPanel />)
    expect(await screen.findByText(/本实例不会自动监听/)).toBeInTheDocument()
    expect(screen.getByText(/可添加文件夹后点「立即扫描」手动导入/)).toBeInTheDocument()
  })

  it('adds a folder, clears the input and refreshes the status', async () => {
    const user = userEvent.setup()
    render(<WatchFolderPanel />)
    await screen.findByText('D:/Pictures/a')
    const input = screen.getByLabelText('文件夹路径')
    const button = screen.getByRole('button', { name: '添加文件夹' })
    expect(button).toBeDisabled()
    await user.type(input, 'D:/Pictures/new')
    expect(button).toBeEnabled()
    await user.click(button)
    await waitFor(() => expect(mocked.addWatchFolder).toHaveBeenCalledWith('D:/Pictures/new'))
    await waitFor(() => expect(mocked.getWatchStatus).toHaveBeenCalledTimes(2))
    expect(screen.getByLabelText('文件夹路径')).toHaveValue('')
  })

  it('pauses and resumes a folder with the inverted enabled flag', async () => {
    const user = userEvent.setup()
    render(<WatchFolderPanel />)
    await screen.findByText('D:/Pictures/a')
    await user.click(screen.getByRole('button', { name: '暂停' }))
    await waitFor(() => expect(mocked.setWatchFolderEnabled).toHaveBeenCalledWith(1, false))
    await user.click(screen.getByRole('button', { name: '继续' }))
    await waitFor(() => expect(mocked.setWatchFolderEnabled).toHaveBeenCalledWith(2, true))
  })

  it('removes a folder and refreshes the list', async () => {
    const user = userEvent.setup()
    render(<WatchFolderPanel />)
    await screen.findByText('D:/Pictures/a')
    await user.click(screen.getAllByRole('button', { name: '移除' })[0])
    await waitFor(() => expect(mocked.removeWatchFolder).toHaveBeenCalledWith(1))
    await waitFor(() => expect(mocked.getWatchStatus).toHaveBeenCalledTimes(2))
  })

  it('toggles subfolders for only the selected folder', async () => {
    const user = userEvent.setup()
    render(<WatchFolderPanel />)
    await screen.findByText('D:/Pictures/a')
    const [first, second] = screen.getAllByRole('checkbox', { name: '包含子文件夹' })
    expect(first).toBeChecked()
    expect(second).toBeChecked()
    await user.click(first)
    await waitFor(() => expect(mocked.setWatchFolderSubfolders).toHaveBeenCalledWith(1, false))
  })

  it('starts auto analysis only when the user enables it', async () => {
    const user = userEvent.setup()
    render(<WatchFolderPanel />)
    expect(await screen.findByText('自动分析：已暂停')).toBeInTheDocument()
    expect(mocked.setWatchAutoAnalysis).not.toHaveBeenCalled()
    mocked.getWatchStatus.mockResolvedValue(status({ auto_analysis_enabled: true }))
    await user.click(screen.getByRole('button', { name: '开启自动分析' }))
    expect(await screen.findByText('自动分析：已开启')).toBeInTheDocument()
    expect(mocked.setWatchAutoAnalysis).toHaveBeenCalledWith(true)
  })

  it('saves the daily limit and shows the returned value', async () => {
    const user = userEvent.setup()
    render(<WatchFolderPanel />)
    await screen.findByText('D:/Pictures/a')
    const input = screen.getByLabelText('每日自动分析上限')
    mocked.getWatchStatus.mockResolvedValue(status({ daily_limit: 10 }))
    await user.clear(input)
    await user.type(input, '10')
    await user.click(screen.getByRole('button', { name: '保存上限' }))
    await waitFor(() => expect(mocked.setWatchDailyLimit).toHaveBeenCalledWith(10))
    expect(await screen.findByText('每日上限已保存')).toBeInTheDocument()
    expect(screen.getByLabelText('每日自动分析上限')).toHaveValue(10)
  })

  it('queues deferred images now and disables the action when nothing is deferred', async () => {
    const user = userEvent.setup()
    render(<WatchFolderPanel />)
    await screen.findByText('D:/Pictures/a')
    await user.click(screen.getByRole('button', { name: '现在分析（2 张）' }))
    await waitFor(() => expect(mocked.analyzeNow).toHaveBeenCalledTimes(1))
    expect(await screen.findByRole('status')).toHaveTextContent('已排入分析队列')
  })

  it('disables now-analyze when deferred is zero', async () => {
    mocked.getWatchStatus.mockResolvedValue(status({ deferred: 0 }))
    render(<WatchFolderPanel />)
    await screen.findByText('D:/Pictures/a')
    expect(screen.getByRole('button', { name: '现在分析（0 张）' })).toBeDisabled()
  })

  it('scans once and shows the per-run result', async () => {
    const user = userEvent.setup()
    render(<WatchFolderPanel />)
    await screen.findByText('D:/Pictures/a')
    await user.click(screen.getByRole('button', { name: '立即扫描一次' }))
    await waitFor(() => expect(mocked.scanWatchNow).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/扫描完成：新增 2，重复 1，失败 0/)).toBeInTheDocument()
    await waitFor(() => expect(mocked.getWatchStatus).toHaveBeenCalledTimes(2))
  })

  it('surfaces failed loads and failed actions instead of rendering blank', async () => {
    mocked.getWatchStatus.mockRejectedValueOnce(new Error('读取自动导入状态失败'))
    render(<WatchFolderPanel />)
    expect(await screen.findByText('读取自动导入状态失败')).toBeInTheDocument()
    expect(screen.getByText('自动导入文件夹')).toBeInTheDocument()

    const user = userEvent.setup()
    mocked.addWatchFolder.mockRejectedValueOnce(new Error('路径不是文件夹'))
    await user.type(screen.getByLabelText('文件夹路径'), 'D:/bad')
    await user.click(screen.getByRole('button', { name: '添加文件夹' }))
    expect(await screen.findByText('路径不是文件夹')).toBeInTheDocument()
  })
})

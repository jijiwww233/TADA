import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { ProjectVibe } from './ProjectVibe'
import { VibeSimilarityControls } from './VibeSimilarityControls'
import { loadProjectVibe, pinVibeImages, rejectProjectImage } from './api'

vi.mock('./api', () => ({
  loadProjectVibe: vi.fn(),
  pinVibeImages: vi.fn(),
  rejectProjectImage: vi.fn(),
}))

const project = { id: 1, name: '海报', brief: '复古海报', created_at: '' }

it('shows only combined as available for an unanalyzed image', () => {
  render(<VibeSimilarityControls imageId={10} mode="combined" available={['combined']}
    resultIds={[]} loading={false} projects={[]} activeProjectId={null} onSelect={vi.fn()} onPinned={vi.fn()} />)
  expect(screen.getByText('这张还没分析')).toBeInTheDocument()
  expect(screen.getByRole('option', { name: '综合' })).not.toBeDisabled()
  expect(screen.getByRole('option', { name: '配色' })).toBeDisabled()
  expect(screen.getByRole('option', { name: '风格（试验）' })).toBeDisabled()
})

it('switches the existing similarity dimension and pins the result batch in one call', async () => {
  const user = userEvent.setup()
  const select = vi.fn()
  const pinned = vi.fn()
  vi.mocked(pinVibeImages).mockResolvedValue({ pinned: 2 })
  render(<VibeSimilarityControls imageId={10} mode="combined" available={['combined', 'layout']}
    resultIds={[20, 30]} loading={false} projects={[project]} activeProjectId={1}
    onSelect={select} onPinned={pinned} />)
  await user.selectOptions(screen.getByLabelText('找相似维度'), 'layout')
  expect(select).toHaveBeenCalledWith('layout')
  await user.selectOptions(screen.getByLabelText('用途'), 'layout')
  await user.click(screen.getByRole('button', { name: '全部钉进项目' }))
  expect(await screen.findByText('已钉入 2 张')).toBeInTheDocument()
  expect(pinVibeImages).toHaveBeenCalledWith(1, [20, 30], 'layout')
  expect(pinned).toHaveBeenCalledWith(1)
})

it('hides already pinned rediscovery items and dismisses a rejection without replacement', async () => {
  const user = userEvent.setup()
  vi.mocked(loadProjectVibe).mockResolvedValue({
    items: [1, 2].map(id => ({
      image_id: id, url: '/images/' + id, width: 80, height: 80,
      original_name: '样图 ' + id, created_at: '', is_video: false, sources: [],
    })),
  })
  vi.mocked(rejectProjectImage).mockResolvedValue({ rejected: true })
  render(<ProjectVibe project={project} pinned={new Set([1])} onOpen={vi.fn()} onPinned={vi.fn()} />)
  expect(await screen.findByAltText('样图 2')).toBeInTheDocument()
  expect(screen.queryByAltText('样图 1')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '不要这种' }))
  await waitFor(() => expect(screen.queryByAltText('样图 2')).not.toBeInTheDocument())
  expect(rejectProjectImage).toHaveBeenCalledWith(1, 2)
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ImageDetail from '../ImageDetail'
import type { ImageDetail as Detail } from '../api'
import { saveAnnotation } from './api'

vi.mock('./api', () => ({ saveAnnotation: vi.fn() }))

const mockedSave = vi.mocked(saveAnnotation)

const localDetail = (patch: Partial<Detail> = {}): Detail => ({
  id: 42,
  url: '/images/42.png',
  width: 800,
  height: 600,
  original_name: '我的参考.png',
  created_at: '2026-11-01 12:00:00',
  posts: [{ id: 9, platform: 'local', url: null, title: null, position: 0 }],
  analysis: {
    status: 'done', processing: false, model: 'qwen', visible_text: null,
    subject: ['排版', '配色'], layout: null, style: null, description: 'AI 描述',
    prompt_tokens: null, completion_tokens: null, attempts: 1, error: null, updated_at: '',
  },
  colors: [],
  annotation: { description: '我的描述', tags: ['我的标签'], edited: true, edited_at: '2026-11-01T10:00:00' },
  ...patch,
})

describe('ImageDetail local source and annotation integration', () => {
  beforeEach(() => { mockedSave.mockReset() })

  it('shows local provenance and my annotation, then reloads after saving', async () => {
    mockedSave.mockResolvedValue({ description: '保存后的描述', tags: ['我的标签'], edited: true })
    const loadImage = vi.fn()
      .mockResolvedValueOnce(localDetail())
      .mockResolvedValueOnce(localDetail({
        annotation: { description: '保存后的描述', tags: ['我的标签'], edited: true, edited_at: '2026-11-01T10:00:00' },
      }))

    render(<ImageDetail imageId={42} onClose={() => {}} loadImage={loadImage} />)

    expect(await screen.findByText(/来源：本地/)).toBeInTheDocument()
    expect(screen.getAllByText(/我的参考\.png/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/2026-11-01 12:00:00/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '我的标注（可修改）' })).toBeInTheDocument()
    expect(screen.getByText('AI 描述')).toBeInTheDocument()

    const textarea = screen.getByLabelText('我的标注描述')
    expect(textarea).toHaveValue('我的描述')
    expect(screen.getByTestId('annotation-edited')).toBeInTheDocument()

    fireEvent.change(textarea, { target: { value: '保存后的描述' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    // Only the changed field is sent; untouched tags stay out of the payload.
    await waitFor(() => expect(mockedSave).toHaveBeenCalledWith(42, { description: '保存后的描述' }))
    expect((mockedSave.mock.calls[0] as unknown as [number, { tags?: unknown }])[1]).not.toHaveProperty('tags')
    await waitFor(() => expect(loadImage).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByLabelText('我的标注描述')).toHaveValue('保存后的描述'))
  })

  it('keeps the existing https post links and shows local source only for local posts', async () => {
    const loadImage = vi.fn(async () => localDetail({
      posts: [{ id: 9, platform: 'local', url: null, title: null, position: 0 }],
      annotation: null,
    }))
    render(<ImageDetail imageId={42} onClose={() => {}} loadImage={loadImage} />)
    expect(await screen.findByText(/来源：本地/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '原帖链接（网页端可能受限）' })).not.toBeInTheDocument()
    expect(screen.getByText(/当前显示 AI 内容/)).toBeInTheDocument()
  })
})
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import AnnotationEditor from './AnnotationEditor'
import type { Annotation, AnnotationInput } from './contract'

type SaveFn = (imageId: number, input: AnnotationInput) => Promise<Annotation | null>

const annotation = (patch: Partial<Annotation> = {}): Annotation => ({
  description: '用户描述', tags: ['用户标签'], edited: true, edited_at: '2026-11-01T10:00:00', ...patch,
})

function renderEditor(overrides: {
  save?: SaveFn
  annotation?: Annotation
  aiDescription?: string
  aiTags?: string[]
} = {}) {
  const save = overrides.save ?? vi.fn(async () => annotation({ description: '新描述', tags: ['新标签'] }))
  const onSaved = vi.fn<(annotation: Annotation | null) => void>()
  render(
    <AnnotationEditor
      imageId={7}
      annotation={overrides.annotation ?? annotation()}
      aiDescription={overrides.aiDescription ?? 'AI 描述'}
      aiTags={overrides.aiTags ?? ['AI 标签']}
      onSaved={onSaved}
      save={save as unknown as SaveFn}
    />,
  )
  return { save: save as unknown as ReturnType<typeof vi.fn>, onSaved }
}

const removeTags = (tags: string[]) => {
  for (const tag of tags) fireEvent.click(screen.getByRole('button', { name: '移除标签 ' + tag }))
}

describe('AnnotationEditor', () => {
  it('sends only changed fields: tags only keeps the description untouched', async () => {
    const save = vi.fn(async () => annotation({ description: null, tags: ['新标签'], edited: true }))
    const { onSaved } = renderEditor({ save })
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()

    removeTags(['用户标签'])
    fireEvent.change(screen.getByLabelText('新标签'), { target: { value: '新标签' } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save).toHaveBeenCalledWith(7, { tags: ['新标签'] })
    const payload = (save.mock.calls[0] as unknown as [number, AnnotationInput])[1]
    expect(payload).not.toHaveProperty('description')
    // saved.description is null -> fall back to the AI description.
    await waitFor(() => expect(screen.getByLabelText('我的标注描述')).toHaveValue('AI 描述'))
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('sends only changed fields: description only keeps the tags untouched', async () => {
    const save = vi.fn(async () => annotation({ description: '新描述', tags: null, edited: true }))
    const { onSaved } = renderEditor({ save })
    fireEvent.change(screen.getByLabelText('我的标注描述'), { target: { value: '新描述' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save).toHaveBeenCalledWith(7, { description: '新描述' })
    const payload = (save.mock.calls[0] as unknown as [number, AnnotationInput])[1]
    expect(payload).not.toHaveProperty('tags')
    await waitFor(() => expect(screen.getByLabelText('我的标注描述')).toHaveValue('新描述'))
    expect(screen.getByText('AI 标签')).toBeInTheDocument()
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('sends both fields when both changed, and disables save when nothing changed', async () => {
    const save = vi.fn(async () => annotation({ description: '新描述', tags: ['新标签'], edited: true }))
    const { onSaved } = renderEditor({ save })
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()

    fireEvent.change(screen.getByLabelText('我的标注描述'), { target: { value: '新描述' } })
    removeTags(['用户标签'])
    fireEvent.change(screen.getByLabelText('新标签'), { target: { value: '新标签' } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(save).toHaveBeenCalledWith(7, { description: '新描述', tags: ['新标签'] }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).toBeDisabled())
  })

  it('sends an explicit empty string when the description is cleared', async () => {
    const save = vi.fn(async () => annotation({ description: '', tags: null, edited: true }))
    renderEditor({ save })
    fireEvent.change(screen.getByLabelText('我的标注描述'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith(7, { description: '' }))
    await waitFor(() => expect(screen.getByLabelText('我的标注描述')).toHaveValue(''))
    expect(screen.getByText('AI 标签')).toBeInTheDocument()
  })

  it('sends an explicit empty array when all tags are removed', async () => {
    const save = vi.fn(async () => annotation({ description: null, tags: [], edited: true }))
    renderEditor({ save })
    removeTags(['用户标签'])
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith(7, { tags: [] }))
    await waitFor(() => expect(screen.getByText('暂无用户标签')).toBeInTheDocument())
    expect(screen.getByLabelText('我的标注描述')).toHaveValue('AI 描述')
  })

  it('restores the AI content with null overrides only when the user has edited', async () => {
    const save = vi.fn(async () => annotation({ description: null, tags: null, edited: false }))
    const { onSaved } = renderEditor({ save })
    fireEvent.click(screen.getByRole('button', { name: '恢复 AI 内容' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith(7, { description: null, tags: null }))
    await waitFor(() => expect(screen.getByLabelText('我的标注描述')).toHaveValue('AI 描述'))
    expect(screen.getByText('AI 标签')).toBeInTheDocument()
    expect(screen.queryByTestId('annotation-edited')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '恢复 AI 内容' })).not.toBeInTheDocument()
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('marks the fields that have a user override', () => {
    renderEditor()
    expect(screen.getByTestId('description-edited')).toBeInTheDocument()
    expect(screen.getByTestId('tags-edited')).toBeInTheDocument()
  })

  it('does not show the restore action or field markers for a non-edited annotation', () => {
    renderEditor({ annotation: annotation({ description: null, tags: null, edited: false }) })
    expect(screen.queryByRole('button', { name: '恢复 AI 内容' })).not.toBeInTheDocument()
    expect(screen.queryByTestId('annotation-edited')).not.toBeInTheDocument()
    expect(screen.queryByTestId('description-edited')).not.toBeInTheDocument()
    expect(screen.queryByTestId('tags-edited')).not.toBeInTheDocument()
  })

  it('shows the fixed error message when saving fails', async () => {
    const save = vi.fn(async () => { throw new Error('保存失败，请重试') })
    const { onSaved } = renderEditor({ save })
    fireEvent.change(screen.getByLabelText('我的标注描述'), { target: { value: '改一下' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('保存失败，请重试')
    expect(onSaved).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
  })

  it('enforces the tag limits: 20 tags, 60 chars each and no duplicates', () => {
    const many = Array.from({ length: 20 }, (_, index) => 'tag' + index)
    renderEditor({ annotation: annotation({ tags: many }) })
    const input = screen.getByLabelText('新标签')
    fireEvent.change(input, { target: { value: 'one-more' } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))
    expect(screen.getByRole('alert')).toHaveTextContent('最多 20 个标签')
    fireEvent.change(input, { target: { value: 'x'.repeat(61) } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))
    expect(screen.getByRole('alert')).toHaveTextContent('每个标签最多 60 字')
    fireEvent.change(input, { target: { value: many[0] } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))
    expect(screen.getByRole('alert')).toHaveTextContent('标签重复')
  })
})

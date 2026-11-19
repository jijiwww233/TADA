import { useEffect, useState } from 'react'
import type { Annotation, AnnotationInput } from './contract'
import { saveAnnotation as defaultSaveAnnotation } from './api'

export interface AnnotationEditorProps {
  imageId: number
  annotation?: Annotation | null
  aiDescription?: string | null
  aiTags?: string[] | null
  onSaved?: (annotation: Annotation | null) => void
  /** Test seam: defaults to the real PUT endpoint. */
  save?: (imageId: number, input: AnnotationInput) => Promise<Annotation | null>
}

const MAX_TAGS = 20
const MAX_TAG_LENGTH = 60
const TAG_SEPARATOR = '\u0000'
const tagKey = (tags: readonly string[]) => tags.join(TAG_SEPARATOR)

const chipStyle = {
  display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 6px',
  border: '1px solid #cbd5e1', borderRadius: 999, fontSize: 13,
} as const

/**
 * "我的标注（可修改）": the user description/tags override the AI values when present.
 * A null save (恢复 AI 内容) restores the AI fallback; edited toggles the 用户改过 badge.
 */
export default function AnnotationEditor({
  imageId, annotation, aiDescription, aiTags, onSaved, save = defaultSaveAnnotation,
}: AnnotationEditorProps) {
  const initialDescription = annotation?.description ?? aiDescription ?? ''
  const initialTags = annotation?.tags ?? aiTags ?? []
  const [description, setDescription] = useState(initialDescription)
  const [tags, setTags] = useState<string[]>([...initialTags])
  const [base, setBase] = useState({ description: initialDescription, tags: tagKey(initialTags) })
  const [edited, setEdited] = useState(Boolean(annotation?.edited))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tagInput, setTagInput] = useState('')

  // Reloaded detail (image/project switch or after a save) refreshes the editor baseline.
  useEffect(() => {
    const nextDescription = annotation?.description ?? aiDescription ?? ''
    const nextTags = annotation?.tags ?? aiTags ?? []
    setDescription(nextDescription)
    setTags([...nextTags])
    setBase({ description: nextDescription, tags: tagKey(nextTags) })
    setEdited(Boolean(annotation?.edited))
    setSaving(false)
    setError(null)
    setTagInput('')
  }, [imageId, annotation?.description, annotation?.tags, annotation?.edited, aiDescription, aiTags])

  const dirty = description !== base.description || tagKey(tags) !== base.tags

  const addTag = () => {
    const value = tagInput.trim()
    if (value.length === 0) return
    if (tags.includes(value)) { setError('标签重复'); return }
    if (value.length > MAX_TAG_LENGTH) { setError(`每个标签最多 ${MAX_TAG_LENGTH} 字`); return }
    if (tags.length >= MAX_TAGS) { setError(`最多 ${MAX_TAGS} 个标签`); return }
    setError(null)
    setTags([...tags, value])
    setTagInput('')
  }

  const removeTag = (tag: string) => {
    setError(null)
    setTags(tags.filter(entry => entry !== tag))
  }

  const persist = async (input: AnnotationInput, mode: 'save' | 'reset') => {
    if (saving) return
    setSaving(true)
    setError(null)
    try {
      const saved = await save(imageId, input)
      // "恢复 AI" always shows the AI values; a normal save falls back to AI for fields whose
      // returned override is null (the untouched fields are not part of the payload).
      const nextDescription = mode === 'reset'
        ? (aiDescription ?? '')
        : (saved?.description ?? aiDescription ?? '')
      const nextTags = mode === 'reset'
        ? (aiTags ? [...aiTags] : [])
        : (saved?.tags ?? aiTags ?? [])
      setDescription(nextDescription)
      setTags(nextTags)
      setBase({ description: nextDescription, tags: tagKey(nextTags) })
      setEdited(mode === 'reset' ? false : Boolean(saved?.edited))
      onSaved?.(saved)
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : '保存失败，请重试')
    } finally {
      setSaving(false)
    }
  }

  const saveNow = () => {
    // Send only the fields the user actually changed. An emptied description stays as an
    // explicit '' and an emptied tag list as an explicit [] (null is reserved for "restore AI").
    const input: AnnotationInput = {}
    if (description !== base.description) input.description = description
    if (tagKey(tags) !== base.tags) input.tags = [...tags]
    if (input.description === undefined && input.tags === undefined) return
    void persist(input, 'save')
  }

  const restoreAi = () => {
    void persist({ description: null, tags: null }, 'reset')
  }

  return (
    <section aria-label="我的标注" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <p style={{ margin: 0 }}>
        {edited
          ? <span data-testid="annotation-edited" style={{ color: '#b45309' }}>用户改过</span>
          : <span className="muted">当前显示 AI 内容，可在此覆盖</span>}
      </p>

      <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span>
          描述
          {annotation?.description != null && (
            <span className="muted" data-testid="description-edited">（用户改过）</span>
          )}
        </span>
        <textarea
          aria-label="我的标注描述"
          rows={3}
          value={description}
          disabled={saving}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>

      <p style={{ margin: 0 }}>
        标签
        {annotation?.tags != null && (
          <span className="muted" data-testid="tags-edited">（用户改过）</span>
        )}
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {tags.map(tag => (
          <span key={tag} style={chipStyle}>
            {tag}
            <button
              type="button"
              aria-label={`移除标签 ${tag}`}
              disabled={saving}
              onClick={() => removeTag(tag)}
              style={{ border: 'none', background: 'transparent', cursor: 'pointer', padding: 0 }}
            >×</button>
          </span>
        ))}
        {tags.length === 0 && <span className="muted">暂无用户标签</span>}
      </div>

      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <input
          aria-label="新标签"
          value={tagInput}
          disabled={saving}
          maxLength={MAX_TAG_LENGTH}
          onChange={(event) => setTagInput(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); addTag() } }}
          placeholder="添加标签"
        />
        <button type="button" onClick={addTag} disabled={saving || tagInput.trim().length === 0}>添加</button>
        <span className="muted">{tags.length}/{MAX_TAGS}</span>
      </div>

      {error && <p role="alert" className="error" style={{ margin: 0 }}>{error}</p>}

      <div style={{ display: 'flex', gap: 6 }}>
        <button type="button" onClick={saveNow} disabled={saving || !dirty}>保存</button>
        {edited && (
          <button type="button" onClick={restoreAi} disabled={saving}>恢复 AI 内容</button>
        )}
      </div>
    </section>
  )
}
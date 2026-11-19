/** User-editable view of an image's AI annotation (null fields fall back to the AI values). */
export interface Annotation {
  description: string | null
  tags: string[] | null
  edited: boolean
  edited_at?: string
}

/**
 * Field-level override input. Omitted fields are left untouched in the backend overlay
 * (null = clear the override and fall back to AI, an empty string/array = explicit user value).
 */
export interface AnnotationInput {
  description?: string | null
  tags?: string[] | null
}

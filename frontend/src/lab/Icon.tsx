import { createElement } from 'react'
import type { IconNode } from 'lucide'

/**
 * Static lucide icon from its data (line style, 1.5 stroke). MorphIcon is only used where an icon changes:
 * it treats icons with filled dots (like Palette) as filled shapes, which would turn the line icon solid.
 */
export function Icon({ icon, size = 18, strokeWidth = 1.5, className }: { icon: IconNode; size?: number; strokeWidth?: number; className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden className={className}>
      {icon.map(([tag, attrs], i) => createElement(tag, { key: i, ...attrs }))}
    </svg>
  )
}

import type { ReactNode } from 'react'

/** Plain white stage for capsule / text demos (no colour, no filters). */
export function Stage({ children, height = 260 }: { children: ReactNode; height?: number }) {
  return (
    <div className="relative [display:grid] place-items-center overflow-hidden bg-white px-6" style={{ height }}>
      <div className="relative flex w-full justify-center">{children}</div>
    </div>
  )
}

/** Shared copy (opening PPT). */
export const TITLE = '我明明收藏过。'
export const SUBTITLE = '点收藏的时候想着“以后肯定用得上”，后来就再也没找到过。'

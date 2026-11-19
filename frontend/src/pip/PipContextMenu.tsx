import { useLayoutEffect, useRef, type ReactNode } from 'react'

export type PipMenuPoint = { x: number; y: number }

export function PipContextMenu({ point, viewport, label, children }: {
  point: PipMenuPoint
  viewport: { width: number; height: number }
  label: string
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const menu = ref.current
    if (!menu) return
    const { width, height } = menu.getBoundingClientRect()
    menu.style.left = `${Math.max(8, Math.min(point.x, viewport.width - width - 8))}px`
    menu.style.top = `${Math.max(8, Math.min(point.y, viewport.height - height - 8))}px`
  }, [point, viewport])
  return <div ref={ref} className="pip-popover pip-context m-frost" role="group" aria-label={label}>{children}</div>
}

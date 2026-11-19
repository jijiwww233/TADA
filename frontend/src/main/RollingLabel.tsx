import { useEffect, useRef } from 'react'

export function RollingLabel({ text }: { text: string }) {
  const ref = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    const label = ref.current
    const button = label?.closest('button')
    if (!label || !button) return

    const rollOnTap = (event: MouseEvent) => {
      const pointerType = (event as PointerEvent).pointerType
      const touch = pointerType === 'touch' || pointerType === 'pen'
        || (!pointerType && window.matchMedia('(hover: none)').matches)
      if (!event.detail || !touch || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

      // Flush the reset so repeated taps on the same button replay the roll.
      label.removeAttribute('data-roll-tap')
      void label.offsetWidth
      label.setAttribute('data-roll-tap', '')
    }

    button.addEventListener('click', rollOnTap)
    return () => button.removeEventListener('click', rollOnTap)
  }, [])

  return (
    <span ref={ref} className="m-roll-label" onAnimationEnd={event => {
      if (event.animationName === 'm-roll-in') event.currentTarget.removeAttribute('data-roll-tap')
    }}>
      <span>{text}</span>
      <span aria-hidden="true">{text}</span>
    </span>
  )
}

import { useEffect, useLayoutEffect, useRef } from 'react'
import type { LabImage } from '../lab/data'
import { thumbnailSrc } from '../imageThumbnail'
import './detail-strip.css'

export function ThumbnailStrip({ list, index, onSelect, active }: {
  list: LabImage[]
  index: number
  onSelect: (index: number) => void
  active: boolean
}) {
  const strip = useRef<HTMLElement>(null)
  const thumbs = useRef<(HTMLButtonElement | null)[]>([])

  useLayoutEffect(() => {
    const container = strip.current
    const thumb = thumbs.current[index]
    if (!container || !thumb) return
    const viewport = container.getBoundingClientRect()
    const item = thumb.getBoundingClientRect()
    const inset = parseFloat(getComputedStyle(container).paddingInlineStart)
    if (item.left < viewport.left + inset) container.scrollLeft += item.left - viewport.left - inset
    else if (item.right > viewport.right - inset) container.scrollLeft += item.right - viewport.right + inset
  }, [index])

  useEffect(() => {
    const container = strip.current
    if (!container) return
    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.defaultPrevented) return
      event.preventDefault()
      const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? thumbs.current[0]?.offsetHeight ?? 1
        : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? container.clientWidth : 1
      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY
      container.scrollLeft += delta * unit
    }
    container.addEventListener('wheel', onWheel, { passive: false })
    return () => container.removeEventListener('wheel', onWheel)
  }, [])

  useEffect(() => {
    if (!active) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
      if (event.target instanceof HTMLElement && event.target.closest('input,textarea,select,[contenteditable],.m-pwheel')) return
      const next = index + (event.key === 'ArrowLeft' ? -1 : 1)
      if (next < 0 || next >= list.length) return
      event.preventDefault()
      if (strip.current?.contains(document.activeElement)) thumbs.current[next]?.focus({ preventScroll: true })
      onSelect(next)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, index, list.length, onSelect])

  return (
    <nav className="m-ld-strip" ref={strip} aria-label="当前图片组" data-lenis-prevent>
      {list.map((item, i) => (
        <button type="button" key={item.id} className="m-ld-strip-item"
          ref={el => { thumbs.current[i] = el }} aria-label={`第 ${i + 1} 张`}
          aria-current={i === index ? 'true' : undefined} tabIndex={i === index ? 0 : -1}
          onClick={() => onSelect(i)}>
          <img src={thumbnailSrc(item.src)} alt="" width={item.w} height={item.h} loading="lazy"
            style={{ aspectRatio: `${item.w} / ${item.h}` }} />
        </button>
      ))}
    </nav>
  )
}

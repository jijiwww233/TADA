import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent } from 'react'
import { gsap } from 'gsap'
import { ENTER, FEEDBACK, OVERLAY_TWEEN, cubicBezier } from './motion'

const enterEase = cubicBezier(ENTER.ease)
const feedbackEase = cubicBezier(FEEDBACK.ease)

export type GalleryItem = { image: string; label?: string; alt?: string }

export interface AccordionGalleryProps {
  items: GalleryItem[]
  height?: number
  gap?: number
  radius?: number
  /** 展开那张占整排宽度的比例 */
  expandRatio?: number
  duration?: number
  ease?: string | ((t: number) => number)
  parallax?: number
  tilt?: number
  stagger?: number
  showLabels?: boolean
  grayscale?: boolean
  defaultIndex?: number
  className?: string
  /** 点已经展开的那一张时触发（打开它） */
  onOpen?: (index: number) => void
}

/**
 * 手风琴画廊（reactbits 的 accordion-gallery，改成本站的米／陶土配色）：
 * 悬停的那一张展开、图片横向视差、旁边的暗下去降饱和，标题从左边推进来。
 * 用 gsap 的 timeline 统一接管，鼠标扫过快也只会追到最后一张。
 */
export function AccordionGallery({
  items,
  height = 280,
  gap = 8,
  radius = 14,
  expandRatio = 0.5,
  duration = OVERLAY_TWEEN.duration,
  ease = OVERLAY_TWEEN.ease,
  parallax = 0.5,
  tilt = 6,
  stagger = ENTER.step,
  showLabels = true,
  grayscale = true,
  defaultIndex = 0,
  className = '',
  onOpen,
}: AccordionGalleryProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const panelRefs = useRef<(HTMLElement | null)[]>([])
  const mediaRefs = useRef<(HTMLElement | null)[]>([])
  const barRefs = useRef<(HTMLElement | null)[]>([])
  const textRefs = useRef<(HTMLElement | null)[]>([])
  const tlRef = useRef<gsap.core.Timeline | null>(null)
  const firstRunRef = useRef(true)
  const mediaSizeRef = useRef(320)

  const count = items.length
  const [active, setActive] = useState(Math.min(Math.max(defaultIndex, 0), Math.max(0, count - 1)))

  const prefersReduced =
    typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false

  const applyLayout = useCallback(
    (animate: boolean) => {
      const panels = panelRefs.current
      if (!panels.length) return

      const r = Math.min(Math.max(expandRatio, 0.2), 0.9)
      const grow = count > 1 ? (r * (count - 1)) / (1 - r) : 1
      const mediaSize = mediaSizeRef.current

      tlRef.current?.kill()
      const dur = animate && !prefersReduced ? duration : 0
      const tl = gsap.timeline()

      panels.forEach((panel, i) => {
        if (!panel) return
        const isActive = i === active
        const media = mediaRefs.current[i]
        const bar = barRefs.current[i]
        const text = textRefs.current[i]

        const rot = isActive ? 0 : i < active ? tilt : -tilt

        tl.to(panel, { flexGrow: isActive ? grow : 1, rotateY: rot, duration: dur, ease }, 0)

        if (media) {
          const drift = Math.max(-1.5, Math.min(1.5, active - i))
          const shift = drift * parallax * mediaSize * 0.06
          const gray = grayscale ? (isActive ? 0 : 1) : 0
          tl.to(
            media,
            {
              xPercent: -50,
              yPercent: -50,
              x: isActive ? 0 : shift,
              '--ag-gray': gray,
              '--ag-dim': isActive ? 0 : 0.12,
              duration: dur,
              ease,
            } as gsap.TweenVars,
            0,
          )
        }

        if (showLabels && bar && text) {
          if (isActive) {
            tl.to([bar, text], { opacity: 1, x: 0, duration: animate && !prefersReduced ? ENTER.duration : 0, ease: enterEase, stagger: prefersReduced ? 0 : stagger }, 0)
          } else {
            tl.to([bar, text], { opacity: 0, x: -14, duration: animate && !prefersReduced ? FEEDBACK.duration : 0, ease: feedbackEase }, 0)
          }
        }
      })

      tlRef.current = tl
    },
    [active, count, expandRatio, duration, ease, tilt, parallax, grayscale, showLabels, stagger, prefersReduced],
  )

  useEffect(() => {
    const el = rootRef.current
    if (!el) return

    const measure = () => {
      const rect = el.getBoundingClientRect()
      const total = rect.width
      const usable = Math.max(total - gap * (count - 1), 120)
      const size = Math.max(140, usable * Math.min(Math.max(expandRatio, 0.2), 0.9) * 1.22)
      mediaSizeRef.current = size
      el.style.setProperty('--ag-media-size', `${size}px`)
      applyLayout(!firstRunRef.current)
    }

    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [applyLayout, gap, count, expandRatio])

  useEffect(() => {
    applyLayout(!firstRunRef.current)
    firstRunRef.current = false
  }, [applyLayout])

  useEffect(() => () => { tlRef.current?.kill() }, [])

  const handleClick = (i: number, e: MouseEvent) => {
    if (i !== active) {
      e.preventDefault()
      setActive(i)
      return
    }
    onOpen?.(i)
  }

  const handleKeyDown = (i: number, e: KeyboardEvent) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i + 1) % count)
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i - 1 + count) % count)
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onOpen?.(i)
    }
  }

  const rootStyle = {
    '--ag-gap': `${gap}px`,
    '--ag-radius': `${radius}px`,
    height: `${height}px`,
  } as CSSProperties

  return (
    <div ref={rootRef} className={`m-ag${className ? ` ${className}` : ''}`} style={rootStyle} role="list" aria-label="每日灵感">
      {items.map((item, i) => {
        const isActive = i === active
        return (
          <div
            key={i}
            ref={el => { panelRefs.current[i] = el }}
            className={`m-ag-panel${isActive ? ' is-active' : ''}`}
            style={{ borderRadius: `${radius}px` }}
            onClick={e => handleClick(i, e)}
            onMouseEnter={() => setActive(i)}
            onFocus={() => setActive(i)}
            onKeyDown={e => handleKeyDown(i, e)}
            role="listitem"
            tabIndex={0}
            aria-current={isActive ? 'true' : undefined}
            aria-label={item.label}
          >
            <span className="m-ag-frame">
              <span className="m-ag-media" ref={el => { mediaRefs.current[i] = el }}>
                <img src={item.image} alt={item.alt ?? item.label ?? ''} draggable={false} loading="lazy" />
              </span>
              <span className="m-ag-overlay" aria-hidden="true" />
            </span>
            {showLabels && (
              <span className="m-ag-label" aria-hidden="true">
                <span className="m-ag-bar" ref={el => { barRefs.current[i] = el }} />
                <span className="m-ag-text" ref={el => { textRefs.current[i] = el }}>{item.label}</span>
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

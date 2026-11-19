import { useEffect, useRef, type CSSProperties } from 'react'

export interface GradualBlurProps {
  position?: 'top' | 'bottom'
  /** 渐隐带的高度 */
  height?: number | string
  /** 最深处模糊多少（会乘 12px） */
  strength?: number
  /** 兼容旧参数：现在固定两层，不再按层数铺 */
  layers?: number
  zIndex?: number
  className?: string
}

/**
 * 边缘渐进模糊（reactbits 的 gradual-blur，减负版）。
 * 之前每 26px 就叠一层，整条 10 层全宽 backdrop-filter，滚动时每帧要重算 10 次模糊，
 * 是滑动发涩的主要原因。现在只留两层——外圈 4px 柔边 + 深处 10.8px——蒙版照样从透明
 * 渐变到实心，看上去还是「越靠边越糊」，滤镜开销降到原来的五分之一。
 */
export function GradualBlur({ position = 'bottom', height = 260, strength = 0.9, zIndex = 40, className = '' }: GradualBlurProps) {
  const ref = useRef<HTMLDivElement | null>(null)
  const maxBlur = Math.max(strength, 0) * 12
  const dir = position === 'top' ? 'to top' : 'to bottom'
  const soft = (a: number, b: number, c: number) => `linear-gradient(${dir}, transparent ${a}%, rgba(0, 0, 0, 0.55) ${b}%, #000 ${c}%)`

  // 刚进首屏时几乎看不出来，往下滚才慢慢糊起来：只动 blur 前面的系数
  useEffect(() => {
    const el = ref.current
    if (!el) return
    let raf = 0
    const update = () => {
      raf = 0
      const y = window.scrollY
      const k = Math.min(1, Math.max(0.16, y / 620))
      el.style.setProperty('--gblur-k', k.toFixed(3))
    }
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(update) }
    update()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [])

  const layer = (px: number, mask: string): CSSProperties => {
    const filter = `blur(calc(${px.toFixed(2)}px * var(--gblur-k, 1)))`
    return { backdropFilter: filter, WebkitBackdropFilter: filter, maskImage: mask, WebkitMaskImage: mask }
  }

  return (
    <div
      ref={ref}
      aria-hidden="true"
      className={`m-gblur${className ? ` ${className}` : ''}`}
      style={{
        position: 'fixed',
        zIndex,
        height: typeof height === 'number' ? `${height}px` : height,
        left: 0,
        right: 0,
        ...(position === 'top' ? { top: 0 } : { bottom: 0 }),
      }}
    >
      <div className="m-gblur-l" style={layer(maxBlur * 0.37, soft(0, 55, 78))} />
      <div className="m-gblur-l" style={layer(maxBlur, soft(45, 85, 100))} />
    </div>
  )
}

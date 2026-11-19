import Lenis from 'lenis'

let instance: Lenis | null = null
const listeners = new Set<(position: number) => void>()

export function onSmoothScroll(listener: (position: number) => void) {
  listeners.add(listener)
  listener(instance?.scroll ?? window.scrollY)
  return () => { listeners.delete(listener) }
}
const scrollOptions = { lerp: 0.11, smoothWheel: true, wheelMultiplier: 1, touchMultiplier: 1.4 }

/** 全程用 Lenis 接住滚轮：惯性缓停，往下滚的那点惯性就是「舒服」的来源。 */
export function startSmoothScroll(): () => void {
  if (instance) return () => {}
  if (typeof window === 'undefined') return () => {}
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const lenis = new Lenis({ ...scrollOptions, smoothWheel: !reduced, autoRaf: true })
  lenis.on('scroll', ({ scroll }) => listeners.forEach(listener => listener(scroll)))
  instance = lenis
  // 截图/调试时可以直接跳位置
  ;(window as unknown as Record<string, unknown>).__lenis = lenis
  return () => {
    instance = null
    lenis.destroy()
  }
}

export function scrollToY(y: number) {
  if (instance) instance.scrollTo(y, { duration: 1.05 })
  else window.scrollTo({ top: y, behavior: 'smooth' })
}

export function scrollToInstant(y: number) {
  // 先让 Lenis 重新量一次页面高度：内容刚变过时它缓存的上限还是旧的，会把 y 夹到错的位置
  if (instance) { instance.resize(); instance.scrollTo(y, { immediate: true, force: true }) }
  else window.scrollTo(0, y)
}

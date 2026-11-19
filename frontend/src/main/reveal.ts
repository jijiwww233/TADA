import { useEffect } from 'react'

/**
 * 滚动进场（照 Orivane 那个站）：带 data-reveal 的块进入视口时，
 * 从下面 50px 浮上来、同时淡入；同一排可以用 --rv-d 错开。
 * 已经在视口里的块会在挂载后立刻走完，所以切页也像「从下面长出来」。
 */
export function useScrollReveal(dep: unknown) {
  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const all = () => Array.from(document.querySelectorAll<HTMLElement>('[data-reveal]:not(.is-in)'))
    if (reduced) {
      all().forEach(n => n.classList.add('is-in'))
      return
    }
    const io = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        entry.target.classList.add('is-in')
        io.unobserve(entry.target)
      }
    }, { rootMargin: '0px', threshold: 0.1 })
    const watch = (el: Element) => { if (el instanceof HTMLElement && !el.classList.contains('is-in')) io.observe(el) }
    const scan = (node: Node) => {
      if (!(node instanceof HTMLElement)) return
      if (node.hasAttribute('data-reveal')) watch(node)
      node.querySelectorAll('[data-reveal]:not(.is-in)').forEach(watch)
    }
    all().forEach(watch)
    // 列表重排、筛选之后新挂上来的块也要接住
    const mo = new MutationObserver(muts => {
      for (const m of muts) for (const n of m.addedNodes) scan(n)
    })
    mo.observe(document.body, { childList: true, subtree: true })
    return () => {
      io.disconnect()
      mo.disconnect()
    }
  }, [dep])
}
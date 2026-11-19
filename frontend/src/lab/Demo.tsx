import { useEffect, useRef, useState, type ReactNode } from 'react'

export type DemoMeta = {
  id: string
  name: string
  source: string
  url: string
  license: string
  deps: string
  /** Measured by docs/ui-intro/lab/measure (headless Chrome). Live number is shown next to it. */
  measured?: string
  gap: string
}

type Stat = { visible: boolean; frames: number; windowStart: number; avg: number; min: number; seconds: number }
const stats = new Map<string, Stat>()
declare global {
  interface Window {
    __labFps?: Record<string, { avg: number; min: number; seconds: number }>
    __labFpsReset?: (id: string) => void
  }
}
// Used by docs/ui-intro/lab/shoot.mjs to start a clean measurement.
window.__labFpsReset = id => {
  const s = stats.get(id)
  if (s) Object.assign(s, { frames: 0, windowStart: performance.now(), avg: 0, min: 0, seconds: 0 })
}

// One rAF loop for the whole page; each visible demo accumulates frames in 1s windows.
let looping = false
function loop(now: number) {
  for (const s of stats.values()) {
    if (!s.visible) continue
    s.frames++
    const dt = now - s.windowStart
    if (dt >= 1000) {
      const fps = (s.frames * 1000) / dt
      s.avg = s.seconds ? (s.avg * s.seconds + fps) / (s.seconds + 1) : fps
      s.min = s.seconds ? Math.min(s.min, fps) : fps
      s.seconds++
      s.frames = 0
      s.windowStart = now
    }
  }
  window.__labFps = Object.fromEntries([...stats].map(([k, s]) => [k, { avg: s.avg, min: s.min, seconds: s.seconds }]))
  requestAnimationFrame(loop)
}

function useFps(id: string, ref: React.RefObject<HTMLElement | null>) {
  const [view, setView] = useState({ avg: 0, min: 0, seconds: 0 })
  useEffect(() => {
    const s: Stat = { visible: false, frames: 0, windowStart: performance.now(), avg: 0, min: 0, seconds: 0 }
    stats.set(id, s)
    if (!looping) { looping = true; requestAnimationFrame(loop) }
    const io = new IntersectionObserver(([e]) => {
      s.visible = e.isIntersecting
      s.frames = 0
      s.windowStart = performance.now()
    }, { threshold: 0 })
    if (ref.current) io.observe(ref.current)
    const t = setInterval(() => setView({ avg: s.avg, min: s.min, seconds: s.seconds }), 1000)
    return () => { io.disconnect(); clearInterval(t); stats.delete(id) }
  }, [id, ref])
  return view
}

/** bleed: the stage spans the full window width and is not clipped (position: sticky needs that). */
export function Demo({ meta, children, tall, bleed }: { meta: DemoMeta; children: ReactNode; tall?: boolean; bleed?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const fps = useFps(meta.id, ref)
  return (
    <article ref={ref} id={meta.id} className={bleed ? '' : 'overflow-hidden rounded-[22px] border border-[var(--line)] bg-white shadow-[var(--shadow-1)]'}>
      <div className={`[display:grid] gap-x-6 gap-y-1.5 px-5 py-4 text-[13px] sm:grid-cols-[minmax(0,1.1fr)_minmax(0,2fr)] ${bleed ? 'mb-6 rounded-[22px] border border-[var(--line)] bg-white shadow-[var(--shadow-1)]' : 'border-b border-[var(--line)]'}`}>
        <div>
          <h3 className="text-[16px] font-semibold">{meta.name}</h3>
          <a className="text-[var(--ink-2)] underline decoration-[var(--line)] underline-offset-2" href={meta.url} target="_blank" rel="noreferrer">{meta.source}</a>
        </div>
        <dl className="[display:grid] grid-cols-[4.5em_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[var(--ink-2)]">
          <dt>许可证</dt><dd className="m-0 text-[var(--ink)]">{meta.license}</dd>
          <dt>依赖/大小</dt><dd className="m-0 text-[var(--ink)]">{meta.deps}</dd>
          <dt>实测帧率</dt>
          <dd className="num m-0 text-[var(--ink)]">
            {meta.measured ?? '—'}
            <span className="ml-2 text-[var(--ink-2)]">
              当前 {fps.seconds ? `${Math.round(fps.avg)} fps · 最低 ${Math.round(fps.min)}` : '测量中…'}
            </span>
          </dd>
          <dt>差距</dt><dd className="m-0 text-[var(--ink)]">{meta.gap}</dd>
        </dl>
      </div>
      {bleed
        ? <div className="relative ml-[calc(50%-50vw)] w-screen">{children}</div>
        : <div className={tall ? '' : 'relative'}>{children}</div>}
    </article>
  )
}

export function Replay({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      className="lab-btn absolute right-4 top-4 z-10">
      重播
    </button>
  )
}

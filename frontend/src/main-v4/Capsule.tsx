import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowUp, GalleryVerticalEnd, Palette, Search } from 'lucide'
import { Icon } from '../lab/Icon'

export type Mode = 'find' | 'ref'
const MODES: { k: Mode; label: string }[] = [
  { k: 'find', label: '找图' },
  { k: 'ref', label: '要参考' },
]
const RING_R = 76

/** 首屏胶囊：透明玻璃 + 背后很淡的叠色；左边是原有站点的色盘图标，右边实心陶土橙发送。 */
export function Capsule({ mode, onMode, onSearch, hue, onHue }: {
  mode: Mode
  onMode: (m: Mode) => void
  onSearch: (text: string) => void
  hue: number | null
  onHue: (h: number | null) => void
}) {
  const [text, setText] = useState('')
  const [wheel, setWheel] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [thumb, setThumb] = useState<{ x: number; w: number } | null>(null)
  const ring = useRef<HTMLDivElement | null>(null)
  const segRefs = useRef<(HTMLButtonElement | null)[]>([])

  const measure = useCallback(() => {
    const b = segRefs.current[MODES.findIndex(m => m.k === mode)]
    if (b) setThumb({ x: b.offsetLeft, w: b.offsetWidth })
  }, [mode])
  useLayoutEffect(() => { measure() }, [measure])
  useEffect(() => {
    const onResize = () => measure()
    window.addEventListener('resize', onResize)
    document.fonts?.ready.then(measure)
    return () => window.removeEventListener('resize', onResize)
  }, [measure])

  const hueAt = useCallback((clientX: number, clientY: number) => {
    const box = ring.current?.getBoundingClientRect()
    if (!box) return
    const dx = clientX - (box.left + box.width / 2)
    const dy = clientY - (box.top + box.height / 2)
    if (Math.hypot(dx, dy) < box.width * 0.24) { onHue(null); return }
    onHue((Math.atan2(dy, dx) * 180 / Math.PI + 90 + 360) % 360)
  }, [onHue])

  const knob = hue === null ? null : {
    left: `calc(50% + ${Math.cos((hue - 90) * Math.PI / 180) * RING_R}px)`,
    top: `calc(50% + ${Math.sin((hue - 90) * Math.PI / 180) * RING_R}px)`,
    background: `oklch(0.72 0.15 ${hue})`,
  }

  return (
    <div className="m-capsule">
      <button type="button" aria-label="按颜色找图" aria-expanded={wheel}
        onClick={() => setWheel(w => !w)} className={`m-cap-icon ${wheel ? 'is-on' : ''}`}>
        <Icon icon={Palette} size={17} strokeWidth={1.5} />
      </button>
      <div className="m-cap-track" role="radiogroup" aria-label="模式">
        {thumb && <span className="m-cap-thumb" style={{ width: thumb.w, transform: `translateX(${thumb.x}px)` }} />}
        {MODES.map((m, i) => (
          <button key={m.k} ref={el => { segRefs.current[i] = el }} type="button" role="radio" aria-checked={mode === m.k}
            onClick={() => onMode(m.k)} className={`m-cap-seg ${mode === m.k ? 'is-on' : ''}`}>{m.label}</button>
        ))}
      </div>
      <label className="m-cap-search">
        <Icon icon={mode === 'find' ? Search : GalleryVerticalEnd} size={17} strokeWidth={1.6} />
        <input className="m-cap-empty" value={text} aria-label="搜索"
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') onSearch(text.trim()) }}
          placeholder={mode === 'find' ? '那张图……收藏在哪了' : '说说要做什么，找参考'} />
      </label>
      <button type="button" aria-label="发送" className="m-cap-send" onClick={() => onSearch(text.trim())}>
        <Icon icon={ArrowUp} size={18} strokeWidth={1.8} />
      </button>
      {wheel && (
        <div className="m-wheel-pop">
          <div ref={ring} className="m-wheel-ring"
            onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); setDragging(true); hueAt(e.clientX, e.clientY) }}
            onPointerMove={e => { if (dragging) hueAt(e.clientX, e.clientY) }}
            onPointerUp={() => setDragging(false)}
            onPointerCancel={() => setDragging(false)}
            style={{ touchAction: 'none' }}>
            {knob && <span className="m-wheel-knob" style={knob} />}
          </div>
          <div className="m-wheel-center"><span className="m-wheel-dot" style={hue !== null ? { background: `oklch(0.72 0.15 ${hue})` } : undefined} /></div>
          <p className="m-wheel-caption">{hue === null ? '拖动色环 · 按颜色重排' : `色相 ${Math.round(hue)}° · 点中心取消`}</p>
        </div>
      )}
    </div>
  )
}

import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { MorphIcon } from 'morphicons/react'
import { ArrowUp, GalleryVerticalEnd, Palette, Search, type IconNode } from 'lucide'
import { useImages } from './data'
import { Icon } from './Icon'
import { HueRing } from './HueRing'

/**
 * Capsule (round 5): a clear body with a fine ink edge, nothing under it (values in lab.css .capsule*). Layout like
 * ChatGPT's composer: colour-wheel button on the left (where its "+" is), send arrow on the right.
 * Hierarchy: send (solid clay, --wm-accent) > selected 找图 / 要参考 (pale soft-accent pill, --wm-soft-*) > the rest.
 * All text colours come from the --wm-* variables.
 */
export const SPRING_SLIDE = { type: 'spring', stiffness: 170, damping: 26 } as const // morphicons "smooth" = memo page-level range
export const SPRING_SMALL = { type: 'spring', stiffness: 380, damping: 18 } as const // memo small-button spring

/**
 * The capsule icon set (the user picked candidate 3 in round 4; the other candidates are gone): 找图 = search,
 * 要参考 = a stack of picked images (GalleryVerticalEnd). The search icon morphs into the stack. Use these everywhere.
 */
export const ICON_FIND = Search
export const ICON_REF = GalleryVerticalEnd

type Mode = 'find' | 'ref'
export type CapsulePresentation = { icon: IconNode; content: ReactNode; action?: ReactNode; actionLabel: string; onAction?: () => void; disabled?: boolean; underlay?: ReactNode; surface?: 'card' | 'status'; /** 纯装饰的动作图标：显示为细线，不做按钮、不带手形光标 */ actionDecorative?: boolean }
const MODES: { k: Mode; label: string }[] = [{ k: 'find', label: '找图' }, { k: 'ref', label: '要参考' }]

/** 找图 / 要参考: faint track, pale soft-accent thumb; the thumb only ever slides left/right (one element, measured). */
function ModeSwitch({ mode, setMode, reduced }: { mode: Mode; setMode: (m: Mode) => void; reduced: boolean }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const [box, setBox] = useState<{ x: number; w: number } | null>(null)
  useLayoutEffect(() => {
    const measure = () => {
      const b = refs.current[MODES.findIndex(m => m.k === mode)]
      if (b) setBox({ x: b.offsetLeft, w: b.offsetWidth })
    }
    measure()
    document.fonts?.ready.then(measure)
  }, [mode])
  return (
    <div role="radiogroup" aria-label="模式" className="capsule-track relative flex shrink-0">
      {box && <motion.span aria-hidden className="capsule-thumb absolute top-[3px] bottom-[3px] left-0" initial={false}
        animate={{ x: box.x, width: box.w }} transition={reduced ? { duration: 0 } : SPRING_SLIDE} />}
      {MODES.map((m, i) => (
        <button key={m.k} ref={el => { refs.current[i] = el }} type="button" role="radio" aria-checked={mode === m.k}
          onClick={() => setMode(m.k)} className={`capsule-seg relative ${mode === m.k ? 'is-on' : ''}`}>
          {m.label}
        </button>
      ))}
    </div>
  )
}

/** Search capsule: colour wheel (left) + mode switch + morphing icon + field + send (right). */
export function Capsule({ children, placeholder = '描述你记得的那张图…', className = '', style, presentation, reduced = false, intro = false, onSend, sendLabel = '发送', sendDisabled = false }:
  { children?: ReactNode; placeholder?: string; className?: string; style?: CSSProperties; presentation?: CapsulePresentation; reduced?: boolean; intro?: boolean; onSend?: () => void; sendLabel?: string; sendDisabled?: boolean }) {
  const [mode, setMode] = useState<Mode>('find')
  const [wheel, setWheel] = useState(false)
  return (
    <div className={`relative w-full ${className}`} style={style}>
      <div className={`capsule flex items-center gap-2 px-[8px] ${presentation ? 'capsule-presenting' : ''} ${presentation?.surface ? `capsule-${presentation.surface}` : ''}`}>
        {presentation?.underlay}
        <button type="button" style={presentation || intro ? { display: 'none' } : undefined} aria-label="按颜色找图" aria-expanded={wheel} onClick={() => setWheel(w => !w)} className={`capsule-icon-btn ${wheel ? 'is-on' : ''}`}>
          <Icon icon={Palette} />
        </button>
        {!presentation && <ModeSwitch mode={mode} setMode={setMode} reduced={reduced} />}
        <MorphIcon icon={presentation?.icon ?? (intro ? ICON_REF : mode === 'find' ? ICON_FIND : ICON_REF)} size={18} strokeWidth={1.5} spring="smooth" reducedMotion={reduced ? 'always' : 'user'} className="capsule-icon ml-1.5 shrink-0" />
        <div className="capsule-field flex min-w-0 flex-1 items-center overflow-hidden whitespace-nowrap text-[16px]">
          {presentation?.content ?? children ?? <input aria-label="搜索" className="w-full" placeholder={mode === 'find' ? placeholder : '说说你要做什么，给你找参考…'} />}
        </div>
        {presentation?.actionDecorative ? (
          <span className="capsule-action-decor" aria-hidden="true">{presentation.action}</span>
        ) : (
          <button type="button" aria-label={presentation?.actionLabel ?? sendLabel} disabled={presentation?.disabled ?? sendDisabled} onClick={presentation?.onAction ?? onSend} className="capsule-send">
            {presentation?.action ?? <Icon icon={ArrowUp} size={18} strokeWidth={1.75} />}
          </button>
        )}
      </div>
      <AnimatePresence>{!presentation && wheel && <ColorWheel onClose={() => setWheel(false)} />}</AnimatePresence>
    </div>
  )
}

/** Opened from the palette button: a pale hue ring; picking a hue re-sorts the collection by colour. Colour only appears here. */
function ColorWheel({ onClose }: { onClose: () => void }) {
  const { cards } = useImages()
  const [hue, setHue] = useState<number | null>(null)
  const ring = useRef<SVGSVGElement>(null)
  const dist = (h?: number) => (h === undefined || hue === null ? 999 : Math.min(Math.abs(h - hue), 360 - Math.abs(h - hue)))
  const sorted = hue === null ? cards : [...cards].sort((a, b) => dist(a.hues[0]) - dist(b.hues[0]))
  const pick = (e: React.PointerEvent) => {
    if (e.type === 'pointermove' && e.buttons !== 1) return
    const r = ring.current!.getBoundingClientRect()
    const a = Math.atan2(e.clientY - (r.top + r.height / 2), e.clientX - (r.left + r.width / 2))
    setHue(((a * 180) / Math.PI + 90 + 360) % 360)
  }
  return (
    // Blur Fade timing, 1.3x round 2 (220 → 286ms)
    <motion.div className="capsule-panel absolute left-0 top-[calc(100%+10px)] z-20 flex w-[360px] gap-4 p-4"
      initial={{ opacity: 0, y: -6, filter: 'blur(6px)' }} animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      exit={{ opacity: 0, y: -6, filter: 'blur(6px)' }} transition={{ duration: 0.286, ease: [0.4, 0, 0.2, 1] }}
      onKeyDown={e => e.key === 'Escape' && onClose()}>
      {/* 色环本体抽到 HueRing.tsx，素材库手机顶栏的色环也用它；这里画出来和原来一模一样 */}
      <HueRing ref={ring} hue={hue} className="size-[104px] shrink-0 cursor-crosshair touch-none" onPointerDown={pick} onPointerMove={pick}
        onKeyDown={e => { if (e.key === 'ArrowRight' || e.key === 'ArrowUp') setHue(h => ((h ?? 0) + 10) % 360); if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') setHue(h => ((h ?? 0) + 350) % 360) }} />
      <div className="min-w-0 flex-1">
        <p className="text-[12px] text-[var(--ink-2)]">{hue === null ? '拖动色环，按颜色重排' : '离这个颜色最近的在前'}</p>
        <div className="mt-2 [display:grid] grid-cols-5 gap-1.5">
          {sorted.map(it => (
            <motion.img layout transition={SPRING_SLIDE} key={it.id} src={it.src} alt="" className="aspect-[3/4] w-full rounded-[4px] object-cover" />
          ))}
        </div>
      </div>
    </motion.div>
  )
}

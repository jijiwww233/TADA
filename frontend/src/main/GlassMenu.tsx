import { RollingLabel } from './RollingLabel'
import { forwardRef, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { motion, MotionConfig } from 'motion/react'
import { OVERLAY } from './motion'
import { REFINE } from './refine'

/** 例外：照 Lusano 的菜单不进统一的五档；卡片高度保留第四轮用的这条（原搜索胶囊的弹簧），文字时长见 main.css */
const MENU_EASE = [0.23, 1, 0.32, 1] as const

export type GlassMenuRow = { key: string; label: string; count?: number; rollLabel?: boolean; on: boolean; act: () => void }

/** 指针离开胶囊和菜单后多久收起 */
const OPEN_DELAY = 200
const CLOSE_DELAY = 300

/**
 * 玻璃菜单（照 Lusano 导航菜单量的数值）：收着是一颗胶囊（☰ + 标题），指针移上去就往下长成同宽的卡片，
 * 指针离开胶囊和卡片 0.2 秒后收起；点标题也能开合（键盘 / 触屏用）。
 * 往下长（下方放不下就往上长，靠近顶栏就整体下移，菜单任何时候都完整露出）用的是主页搜索胶囊展开那条弹簧（Capsule 的 SPRING），玻璃是项目页转盘侧栏那块（.m-frost）。
 * 行首 (01) 是这一行在菜单里的真实顺序；行尾数字是真实数量。
 *
 * variant="text"：顶栏右侧那排带下划线的衬线小字（hero 里筛选 / 排序那种），胶囊位置换成一颗字，
 * 菜单从字下面长出来（下方放不下就往上长）。"head" 是那颗字的内容。
 */
export const GlassMenu = forwardRef<HTMLDivElement, {
  title: string
  label: string
  open: boolean
  onOpenChange: (open: boolean) => void
  rows?: GlassMenuRow[]
  splitAt?: number
  /** splitAt 两段的小标题；不传就是筛选菜单原来的「筛选 / 排序」 */
  sections?: [string, string]
  body?: ReactNode
  children?: ReactNode
  searchIcon?: ReactNode
  layoutId?: string
  reduce?: boolean
  footer?: ReactNode
  /** 传了就在面板右上角放一个 × 关闭按钮（筛选面板用） */
  onClose?: () => void
  width?: number
  /** 'pill'：主页筛选菜单那颗胶囊（默认）；'text'：一颗带下划线的字当触发 */
  variant?: 'pill' | 'text'
  /** text 变体里那颗字的内容（例如「筛选 全部 +」） */
  head?: ReactNode
  /** 菜单上沿离窗口顶部至少留多少像素；不传就自己避开顶栏 */
  topInset?: number
}>(function GlassMenu({ title, label, open, onOpenChange, rows = [], splitAt, sections = ['筛选', '排序'], body, children, searchIcon, layoutId, reduce, footer, onClose, width = 180, variant = 'pill', head, topInset }, ref) {
  const n = rows.length
  const inner = useRef<HTMLDivElement>(null)
  const [h, setH] = useState(0)
  const timer = useRef<number | null>(null)

  // 内容多高就长到多高（和搜索胶囊量工具条的做法一样）
  const measure = useCallback(() => { if (inner.current) setH(inner.current.offsetHeight) }, [])
  useLayoutEffect(() => {
    measure()
    const el = inner.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    document.fonts?.ready.then(measure)
    return () => ro.disconnect()
  }, [measure])

  // 打开前量一下胶囊在窗口里的位置：下面放得下就往下长；放不下而上面放得下就往上长；
  // 都放不下就往下长，但整体挪到不被顶栏 / 窗口底边裁掉的位置
  const root = useRef<HTMLDivElement | null>(null)
  const [place, setPlace] = useState<{ dir: 'down' | 'up'; shift: number }>({ dir: 'down', shift: 0 })
  const decide = useCallback(() => {
    const el = root.current
    if (!el || searchIcon) return
    const r = el.getBoundingClientRect()
    if (window.matchMedia('(max-width: 768px)').matches && el.closest('.m-appbar')) {
      setPlace({ dir: 'down', shift: 0 })
      return
    }
    if (variant === 'text') {
      const full = h + 14
      setPlace({ dir: r.bottom + full > window.innerHeight - 8 ? 'up' : 'down', shift: 0 })
      return
    }
    const topbar = topInset ?? (parseFloat(getComputedStyle(el).getPropertyValue('--m-topbar-height')) || 54)
    const full = r.height + (inner.current?.offsetHeight ?? h)
    const minTop = topbar + 8, maxBottom = window.innerHeight - 8
    if (r.top + full <= maxBottom) setPlace({ dir: 'down', shift: Math.max(0, minTop - r.top) })
    else if (r.bottom - full >= minTop) setPlace({ dir: 'up', shift: 0 })
    else setPlace({ dir: 'down', shift: Math.max(minTop - r.top, Math.min(0, maxBottom - (r.top + full))) })
  }, [h, searchIcon, topInset, variant])
  useLayoutEffect(() => { if (open) decide() }, [open, decide])

  const stay = () => { if (timer.current) { window.clearTimeout(timer.current); timer.current = null } }
  const enter = () => {
    if (variant === 'text') return
    stay()
    if (!open) timer.current = window.setTimeout(() => onOpenChange(true), OPEN_DELAY)
  }
  const leave = () => {
    if (variant === 'text') return
    stay()
    if (open) timer.current = window.setTimeout(() => onOpenChange(false), CLOSE_DELAY)
  }
  useEffect(() => stay, [])
  useEffect(() => {
    if (!open) return
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) onOpenChange(false)
    }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') onOpenChange(false) }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', escape)
    }
  }, [open, onOpenChange])

  const panel = (
    <div className="m-gm-panel">
      {onClose && <button type="button" className="m-gm-close" aria-label="关闭面板" onClick={onClose}>×</button>}
      {variant === 'pill' && (
        <button type="button" className="m-gm-head" aria-expanded={open} aria-label={label} onClick={() => { stay(); onOpenChange(searchIcon ? true : !open) }}>
          {searchIcon ?? <span className="m-gm-icon" aria-hidden="true"><i /><i /><i /></span>}
          <span className="m-gm-title">{title}</span>
          {/* 下拉统一：能展开的都带一个 ▾，展开时转过来 */}
          {!searchIcon && <span className="m-gm-chevron" aria-hidden="true">▾</span>}
        </button>
      )}
      <motion.div className="m-gm-body" inert={open ? undefined : true} initial={false}
        animate={{ height: open ? h : 0 }}
        // 引导版：菜单每天开很多次，展开 1 秒 → 0.45 秒、收起 0.5 → 0.3 秒，曲线不变（行内文字的时长见 refine.css）
        transition={{ duration: reduce ? 0 : REFINE ? (open ? 0.45 : 0.3) : open ? 1 : 0.5, ease: MENU_EASE }}>
        <div className={`m-gm-inner${footer ? '' : ' m-gm-no-footer'}`} ref={inner}>
          <span className="m-gm-rule" aria-hidden="true" />
          {splitAt !== undefined && <span className="m-gm-section">{sections[0]}</span>}
          {rows.map((row, i) => (
            <div className="m-gm-entry" key={row.key}>
            {i === splitAt && <span className="m-gm-section m-gm-section-divider">{sections[1]}</span>}
            <button type="button" className={`m-gm-row${row.on ? ' is-on' : ''}`} aria-pressed={row.on}
              style={{ '--i': i } as React.CSSProperties} onClick={row.act}>
              <span className="m-gm-mask">
                <span className="m-gm-text">
                  <i>({String(splitAt !== undefined && i >= splitAt ? i - splitAt + 1 : i + 1).padStart(2, '0')})</i>
                  <span className="m-gm-label">{row.rollLabel ? <RollingLabel text={row.label} /> : row.label}</span>
                  {row.count !== undefined && <b>{row.count}</b>}
                </span>
              </span>
            </button>
            </div>
          ))}
          {children && <div className="m-gm-mask m-gm-content">
            <div className="m-gm-text">{children}</div>
          </div>}
          {body}
          {footer && <div className="m-gm-foot">{footer}</div>}
        </div>
      </motion.div>
    </div>
  )

  return (
    <MotionConfig reducedMotion="user">
      <motion.div layoutId={layoutId} transition={reduce ? { duration: 0 } : OVERLAY} ref={el => { root.current = el; if (typeof ref === 'function') ref(el); else if (ref) ref.current = el }}
        className={`m-gm${open ? ' is-open' : ''}${variant === 'text' ? ' m-gm-word' : ''}`} data-dir={place.dir}
        style={{ '--gm-w': `${width}px`, '--gm-n': n, '--gm-shift': `${place.shift}px` } as React.CSSProperties}
        onPointerEnter={e => { if (e.pointerType !== 'touch' && window.matchMedia('(hover: hover)').matches) enter() }}
        onPointerLeave={e => { if (e.pointerType !== 'touch' && window.matchMedia('(hover: hover)').matches) leave() }}>
        {variant === 'text' ? (
          <>
            <button type="button" className="m-gm-word-head" aria-expanded={open} aria-label={label}
              onClick={() => { stay(); onOpenChange(!open) }}>{head}</button>
            {panel}
          </>
        ) : panel}
      </motion.div>
    </MotionConfig>
  )
})

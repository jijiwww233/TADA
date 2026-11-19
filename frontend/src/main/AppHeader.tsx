import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { GlassMenu, type GlassMenuRow } from './GlassMenu'
import { Onboarding } from './Onboarding'
import { REFINE } from './refine'

/** 中间胶囊菜单里的一页：(01) 素材库 … (06) 帮助 */
const PAGES: { k: 'library' | 'projects' | 'activity' | 'pip' | 'settings' | 'help'; label: string }[] = [
  { k: 'library', label: '素材库' },
  { k: 'projects', label: '项目' },
  { k: 'activity', label: '日历' },
  // 引导版：「小窗」说的是窗口形态，不说用途；改叫「参考画板」
  { k: 'pip', label: REFINE ? '参考画板' : '小窗' },
  { k: 'settings', label: '设置' },
  { k: 'help', label: '帮助' },
]
/** 克制版：前三项是去哪一页，后三项是工具（小窗 / 设置 / 帮助），菜单里分成两段 */
const PAGE_COUNT = 3
export type AppPage = 'library' | 'projects' | 'activity' | 'settings'

/** 色环：半径和搜索行那个一样（76） */
const RING_R = 76

/**
 * 统一顶栏（照 Lusano 顶栏的左 / 中 / 右三块）：左边品牌字，正中一颗胶囊（当前页名字，悬停展开六项菜单），
 * 右边放这一页自己的工具（带下划线的衬线小字，由 MainApp 传进来）。高度和留白见 app-header.css。
 */
export function AppHeader({ title, current, onHome, onGo, onPip, tools }: {
  /** 正中胶囊里显示的当前页名字 */
  title: string
  /** 菜单里算「当前页」的那一项 */
  current: AppPage
  onHome: () => void
  onGo: (page: AppPage) => void
  onPip: () => void

  tools?: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [help, setHelp] = useState(false)
  /** 第 7 条：第一次来的人看不出正中这颗胶囊能点——没展开过之前，下面挂一行小字提示；展开过一次就不再出现 */
  const [firstVisit, setFirstVisit] = useState(() => {
    try { return !window.localStorage.getItem('m-nav-seen') } catch { return false }
  })
  const markSeen = () => {
    if (!firstVisit) return
    setFirstVisit(false)
    try { window.localStorage.setItem('m-nav-seen', '1') } catch { /* 隐私模式存不了就算了 */ }
  }
  const menu = useRef<HTMLDivElement>(null)
  const rows: GlassMenuRow[] = PAGES.map(page => ({
    key: page.k,
    label: page.label,
    on: page.k === current,
    rollLabel: page.k === current,
    act: () => {
      if (page.k === 'help') {
        menu.current?.querySelector<HTMLButtonElement>('.m-gm-head')?.focus({ preventScroll: true })
        setHelp(true)
        return
      }
      setOpen(false)
      if (page.k === 'pip') onPip()
      else onGo(page.k)
    },
  }))
  return (
    <>
    {/* 名字单独一层钉在左上：压到图片上时自动反色（mix-blend-mode），所以不能放在顶栏那一层里面 */}
    <button type="button" className="m-brand m-brand-float" onClick={onHome}>TADA</button>
    <header className="m-topbar m-appbar" data-page={current}>
      {/* data-nav 是“存为项目”飞行动画的目标：原来飞向顶栏的 PROJECTS，现在飞向正中这颗胶囊 */}
      <div className="m-appbar-center" data-nav="projects" data-help={help ? '' : undefined}>
        <GlassMenu ref={menu} label={help ? '关闭帮助' : `切换页面，当前是${title}`} title={help ? '帮助' : title}
          width={help ? 360 : 180} rows={help ? [] : rows} open={open} topInset={0}
          splitAt={REFINE && !help ? PAGE_COUNT : undefined} sections={['页面', '工具']}
          body={help ? <Onboarding /> : undefined} footer={help ? '按 / 开始搜索' : undefined}
          onOpenChange={next => { setOpen(next); if (next) markSeen(); if (!next) setHelp(false) }} />
        {firstVisit && !open && (
          <p className="m-nav-hint" aria-hidden="true">点这里切换：素材库 · 项目 · 日历 · 设置</p>
        )}
      </div>
      <div className="m-appbar-tools">{tools}</div>
    </header>
    </>
  )
}

/** 顶栏右侧的「颜色」：点开还是原来那个可拖的色环，只是从搜索行挪到了顶栏 */
export function HeaderColor({ hue, onHue, open, onOpenChange }: { hue: number | null; onHue: (h: number | null) => void; open: boolean; onOpenChange: (open: boolean) => void }) {
  const [dragging, setDragging] = useState(false)
  const wrap = useRef<HTMLSpanElement | null>(null)
  const ring = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const away = (event: PointerEvent) => { if (!wrap.current?.contains(event.target as Node)) onOpenChange(false) }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [open, onOpenChange])

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
    <span className="m-appbar-wheel-host" ref={wrap}>
      {open && (
        <div className="m-wheel-pop m-appbar-wheel" ref={ring}>
          <div className="m-wheel-ring"
            onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); setDragging(true); hueAt(event.clientX, event.clientY) }}
            onPointerMove={event => { if (dragging) hueAt(event.clientX, event.clientY) }}
            onPointerUp={() => setDragging(false)}
            onPointerCancel={() => setDragging(false)}
            style={{ touchAction: 'none' }}>
            {knob && <span className="m-wheel-knob" style={knob} />}
          </div>
          <div className="m-wheel-center"><span className="m-wheel-dot" style={hue !== null ? { background: `oklch(0.72 0.15 ${hue})` } : undefined} /></div>
          <p className="m-wheel-caption">{hue === null ? '拖动色环 · 按颜色重排' : `色相 ${Math.round(hue)}° · 点中心取消`}</p>
        </div>
      )}
    </span>
  )
}

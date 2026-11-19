import { RollingLabel } from './RollingLabel'
import { Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { motion } from 'motion/react'
import { ArrowUp, Search } from 'lucide'
import { Icon } from '../lab/Icon'
import { OVERLAY } from './motion'
import { onSmoothScroll } from './smooth'
import { REFINE } from './refine'
import { demoReferenceQuery } from './wiringData'
import { useNarrowPhoneLayout } from './useMobileLayout'

export type Mode = 'find' | 'ref'
/** 8d：两个模式各一句话说清区别，直接写在切换按钮下面 */
const MODES: { k: Mode; label: string; hint: string }[] = [
  { k: 'find', label: '找图', hint: '描述画面或颜色，找回收藏过的图' },
  { k: 'ref', label: '要参考', hint: '按排版、配色、风格找参考' },
]
const PLACEHOLDER = 'Tada! I remember it…'
/** 大字下面那行中文小字 */
const CN_HINT: Record<Mode, string> = {
  find: '描述你记得的画面，比如：雨天街道上的霓虹',
  ref: '写下你要做的东西，比如：夏日音乐节海报',
}
/**
 * 引导版：大字保持英文品牌句不动（中文字形和 Newsreader 细斜体放在一起会割裂）；
 * 聚焦后在「找图 · 要参考」左边淡入一行同字号衬线小字：一句话说清这个模式要你写什么 + 几个能直接点的例子。
 */
const EXAMPLES: Record<Mode, { lead: string; items: string[] }> = {
  find: { lead: '试试：', items: ['暖黄色的灯里面好像有星星', '黑色背景上的发光球', '很复古的花，深色背景'] },
  ref: { lead: '试试：', items: [demoReferenceQuery, '极简作品集封面', '夏日音乐节海报'] },
}
const PHONE_EXAMPLES: Record<Mode, string[]> = {
  find: ['星星台灯', '黑底发光球', '深色花卉'],
  ref: [demoReferenceQuery],
}

export function SearchLine({ mode, onMode, text, onText, onSearch, onEscape, field, active, reduce, searching }: {
  mode: Mode
  onMode: (m: Mode) => void
  text: string
  onText: (value: string) => void
  onSearch: (text: string, docked: boolean) => void
  onEscape: () => void
  field: React.RefObject<HTMLInputElement | null>
  active: boolean
  reduce: boolean
  searching: boolean
}) {
  const phone = useNarrowPhoneLayout()
  const root = useRef<HTMLElement | null>(null)
  /** 「试试」那一行：手机上放不下会横滑，右边还有没露出来的词时才挂 data-more（淡出只在这时出现） */
  const tries = useRef<HTMLDivElement | null>(null)
  const line = useRef<HTMLDivElement | null>(null)
  const [docked, setDocked] = useState(false)
  useEffect(() => {
    const node = tries.current
    if (!node) return
    const check = () => {
      const more = node.scrollLeft + node.clientWidth < node.scrollWidth - 2
      if (more) node.dataset.more = ''
      else delete node.dataset.more
    }
    const recheck = () => requestAnimationFrame(check)
    check()
    recheck()
    node.addEventListener('scroll', check, { passive: true })
    window.addEventListener('resize', check)
    if (typeof document !== 'undefined' && document.fonts && document.fonts.ready) document.fonts.ready.then(recheck).catch(() => {})
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(check)
    ro?.observe(node)
    for (const child of Array.from(node.children)) ro?.observe(child)
    return () => { node.removeEventListener('scroll', check); window.removeEventListener('resize', check); ro?.disconnect() }
  }, [mode, docked, phone])
  const [focused, setFocused] = useState(false)
  /** 搜索区在原位时的高度：吸到顶上以后原位留出同样高的空，下面的瀑布流不跳 */
  const [restHeight, setRestHeight] = useState<number | null>(null)
  useLayoutEffect(() => {
    const node = line.current
    if (docked || !node || typeof ResizeObserver === 'undefined') return
    const measure = () => setRestHeight(node.offsetHeight)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(node)
    return () => ro.disconnect()
  }, [docked])

  useEffect(() => {
    const section = root.current
    if (!active || !section) return
    return onSmoothScroll(position => {
      const threshold = section.getBoundingClientRect().bottom + window.scrollY
      setDocked(position >= threshold)
    })
  }, [active])

  // / 聚焦输入框（在别处打字时不抢）
  useEffect(() => {
    if (!active) return
    const shortcut = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.isComposing || event.ctrlKey || event.altKey || event.metaKey || event.defaultPrevented) return
      const target = event.target
      if (target instanceof HTMLElement && (target.isContentEditable || target.closest('input,textarea,select,[role="textbox"]'))) return
      event.preventDefault()
      field.current?.focus({ preventScroll: true })
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  }, [active, field])

  return (
    <section className="m-sl-anchor" ref={root} aria-label="搜索"
      style={docked && restHeight ? { minHeight: restHeight } : undefined}>
      <motion.div ref={line} layout layoutId="library-search" transition={reduce ? { duration: 0 } : OVERLAY}
        className={`m-searchline m-sl-v2${docked ? ' is-floating' : ''}`}>
      <div className="m-sl-in">
        <div className="m-sl-row"
          onFocus={() => { if (!phone) setFocused(true) }}
          onBlur={event => {
            // 焦点只是在搜索行里挪（输入框 ↔ 找图 / 要参考）时不算离开，示例不闪
            if (!phone && !event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false)
          }}>
          {/* 8c：放大镜常驻，一眼看出这是搜索框；点放大镜也能聚焦 */}
          <button type="button" className="m-sl-icon" aria-label="聚焦搜索框" tabIndex={-1}
            onClick={() => field.current?.focus({ preventScroll: true })}>
            <Icon icon={Search} size={docked ? 17 : 22} aria-hidden="true" />
          </button>
          <div className="m-sl-field">
          {/* 引导版：没在搜的时候也有一根光标在跳，一眼看出这行大字是能打字的 */}
          {REFINE && !docked && !focused && !text && <span className="m-sl-fakecaret" aria-hidden="true" />}
          <input
            ref={field}
            className="m-sl-input"
            style={reduce ? { transition: 'none' } : undefined}
            type="text"
            enterKeyHint="search"
            value={text}
            aria-label="搜索"
            autoComplete="off"
            spellCheck={false}
            // 不聚焦时保持品牌句；点进输入框后换成中文提示（评审要求）
            placeholder={docked
              ? (!phone && REFINE && mode === 'ref' ? '写你要做什么' : '搜索收藏')
              : focused ? '描述你记得的画面…' : PLACEHOLDER}
            onFocus={() => { if (phone) setFocused(true) }}
            onBlur={() => { if (phone) setFocused(false) }}
            onChange={event => onText(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                const next = text.trim()
                if (next) onSearch(next, docked)
                return
              }
              if (event.key === 'Escape') {
                event.preventDefault()
                onEscape()
                field.current?.blur()
              }
            }} />
          </div>

          <div className="m-sl-tools">
            <div className="m-sl-modes" role="group" aria-label="搜索方式">
              {MODES.map((option, index) => (
                <Fragment key={option.k}>
                  {index > 0 && <span className="m-sl-dot" aria-hidden="true">·</span>}
                  <button type="button" className={`m-sl-mode${mode === option.k ? ' is-on' : ''}`} title={option.hint}
                    aria-pressed={mode === option.k} aria-describedby={mode === option.k && !docked ? 'm-sl-modehint' : undefined} onClick={() => {
                    onMode(option.k)
                    if (phone) field.current?.blur()
                    else if (REFINE) field.current?.focus({ preventScroll: true })
                  }}><RollingLabel text={option.label} /></button>
                </Fragment>
              ))}
            </div>
          </div>
          {text.trim() && <button className={`m-sl-send${docked ? '' : ' m-sl-send-inline'}`} type="button" aria-label="搜索"
            title="搜索（回车）" aria-busy={searching || undefined}
            onClick={() => onSearch(text.trim(), docked)}><Icon icon={ArrowUp} size={17} aria-hidden="true" /></button>}
        </div>
        {!docked && <div className="m-sl-rule" />}
        {/* 英文大字下面补一行中文小字，说清这里要写什么 */}
        {!docked && phone && (
          <p className="m-sl-phone-hint" id="m-sl-modehint">
            {mode === 'find' ? '描述你记得的画面，比如：' : '写下你要做的东西，比如：'}
            {PHONE_EXAMPLES[mode].map((example, index) => (
              <Fragment key={example}>
                {index > 0 && '、'}
                <button type="button" onClick={() => { onText(example); onSearch(example, docked) }}>{example}</button>
              </Fragment>
            ))}
          </p>
        )}
        {!docked && !phone && <p className="m-sl-cnhint">{CN_HINT[mode]}</p>}
        {/* 8c / 8d：输入线下面一行——左边是能直接点的示例，右边一句话说明当前模式 */}
        {!docked && !phone && REFINE && (
          <div className="m-sl-sub">
            <div className="m-sl-tries" ref={tries}>
              <span className="m-sl-tries-lead">{EXAMPLES[mode].lead}</span>
              {/* 一行纯文字：试试：甲 · 乙 · 丙；没有框、没有下划线，悬停 / 键盘选中变陶土橙 */}
              {EXAMPLES[mode].items.map((example, index) => (
                <Fragment key={example}>
                  {index > 0 && <span className={`m-sl-try-sep${index > 1 ? ' m-sl-try-more' : ''}`} aria-hidden="true">·</span>}
                  <button type="button" className={`m-sl-try${index > 1 ? ' m-sl-try-more' : ''}`}
                    // 按下时不抢输入框的焦点，点完直接按这句搜
                    onMouseDown={event => event.preventDefault()}
                    onClick={() => { onText(example); onSearch(example, docked) }}>{example}</button>
                </Fragment>
              ))}
            </div>
            <p className="m-sl-modehint" id="m-sl-modehint">
              <b>{MODES.find(option => option.k === mode)?.label}</b>：{MODES.find(option => option.k === mode)?.hint}
            </p>
          </div>
        )}
        {searching && <div className="m-sl-status" role="status">正在搜索…</div>}
      </div>
      </motion.div>
    </section>
  )
}

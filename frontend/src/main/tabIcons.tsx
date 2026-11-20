import type { ReactNode } from 'react'

/**
 * 手机底部导航的四个图标：照 Framework7 Icons（iOS 风格，MIT 协议）的画法——
 * 未选中是细线描边（深灰），选中是实心（陶土橙），实心里的细节（山、页头线、日期点、齿轮中心）挖成底色。
 *   .m-tab-solid 主形状：未选中只描边，选中填满
 *   .m-tab-line  始终是线（跟着文字色走）
 *   .m-tab-cut   细节线：未选中跟文字色，选中时换成底色，看起来是在实心上挖出来的
 * 样式见 styles/phone.css「底部导航栏」。
 *
 * 注意：这四个是按 Framework7 Icons 的样子自己画的简化图形，不是官方图标文件（这次环境里装不了依赖）。
 * 换成官方图标时只改这一个文件：npm i framework7-icons，然后用
 * photo_on_rectangle / folder / calendar / gear_alt（选中用对应的 _fill）。
 */

/** 齿轮外轮廓：8 个齿，外半径 10.5、内半径 8.1，中心 (14,14) */
const gear = (() => {
  const points: string[] = []
  const teeth = 8
  const step = Math.PI * 2 / teeth
  for (let i = 0; i < teeth; i += 1) {
    const base = i * step
    const corners: [number, number][] = [
      [8.1, base - step * 0.5],
      [8.1, base - step * 0.24],
      [10.5, base - step * 0.15],
      [10.5, base + step * 0.15],
      [8.1, base + step * 0.24],
    ]
    for (const [r, a] of corners) points.push(`${(14 + r * Math.cos(a)).toFixed(2)},${(14 + r * Math.sin(a)).toFixed(2)}`)
  }
  return points.join(' ')
})()

function Svg({ children }: { children: ReactNode }) {
  return (
    <svg className="m-tab-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28" width="24" height="24"
      fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  )
}

/** 素材库：两张叠着的照片（photo_on_rectangle） */
export function LibraryIcon() {
  return <Svg>
    <path className="m-tab-line" d="M8.5 5H21a2 2 0 0 1 2 2v11" />
    <rect className="m-tab-solid" x="4" y="9" width="15" height="13" rx="2" />
    <path className="m-tab-cut" d="M5 20l4.5-4.5 4 4 2-2 2.5 2.5" />
    <circle className="m-tab-cut" cx="14.5" cy="12.8" r="1.2" />
  </Svg>
}

/** 项目：文件夹（folder） */
export function ProjectsIcon() {
  return <Svg>
    <path className="m-tab-solid" d="M3.5 8.5a2 2 0 0 1 2-2h5.2l2.3 2.5h9.5a2 2 0 0 1 2 2v9.5a2 2 0 0 1-2 2h-17a2 2 0 0 1-2-2Z" />
    <path className="m-tab-cut" d="M4.5 12.5h19" />
  </Svg>
}

/** 日历：页头一条线 + 两个挂环 + 几个日期点（calendar） */
export function CalendarIcon() {
  return <Svg>
    <rect className="m-tab-solid" x="4" y="6" width="20" height="17.5" rx="2.5" />
    <path className="m-tab-cut" d="M4.5 11h19" />
    <path className="m-tab-line" d="M9.5 4v4M18.5 4v4" />
    <path className="m-tab-cut" d="M9.5 15h.01M14 15h.01M18.5 15h.01M9.5 19h.01M14 19h.01" strokeWidth={2.4} />
  </Svg>
}

/** 设置：齿轮（gear_alt） */
export function SettingsIcon() {
  return <Svg>
    <polygon className="m-tab-solid" points={gear} />
    <circle className="m-tab-cut" cx="14" cy="14" r="3.3" />
  </Svg>
}

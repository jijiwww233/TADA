import { forwardRef, type SVGProps } from 'react'
import { oklchToHex } from './data'

const SEGMENTS = Array.from({ length: 36 }, (_, i) => i * 10)

/**
 * 浅色色环（36 段淡彩 + 米白圆心，选中后圆心染成那个颜色、环上一颗米白小圆点）。
 * 原来写在注册引导页的搜索胶囊里（Capsule.tsx 的 ColorWheel），抽出来让素材库手机顶栏的色环共用同一个样子。
 * 只管画；拖动、键盘的处理由用的地方传进来。
 */
export const HueRing = forwardRef<SVGSVGElement, { hue: number | null } & Omit<SVGProps<SVGSVGElement>, 'ref'>>(
  function HueRing({ hue, ...rest }, ref) {
    return (
      <svg ref={ref} viewBox="-60 -60 120 120" role="slider" aria-label="色相" aria-valuenow={Math.round(hue ?? 0)} tabIndex={0} {...rest}>
        {SEGMENTS.map(h => {
          const a0 = ((h - 90 - 5.4) * Math.PI) / 180, a1 = ((h - 90 + 5.4) * Math.PI) / 180
          return <path key={h} d={`M${Math.cos(a0) * 56} ${Math.sin(a0) * 56} A56 56 0 0 1 ${Math.cos(a1) * 56} ${Math.sin(a1) * 56} L${Math.cos(a1) * 38} ${Math.sin(a1) * 38} A38 38 0 0 0 ${Math.cos(a0) * 38} ${Math.sin(a0) * 38}Z`} fill={oklchToHex(0.84, 0.06, h)} />
        })}
        {hue !== null && <circle cx={Math.cos(((hue - 90) * Math.PI) / 180) * 47} cy={Math.sin(((hue - 90) * Math.PI) / 180) * 47} r="7" fill="#fbf9f4" stroke="rgba(60,40,20,.2)" />}
        <circle r="30" fill={hue === null ? '#fbf9f4' : oklchToHex(0.9, 0.045, hue)} stroke="rgba(60,40,20,.08)" />
      </svg>
    )
  },
)

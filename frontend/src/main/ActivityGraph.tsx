import ContributionSkyline from './skyline'
import type { ContributionDay } from './skyline'

/** 只画热力格子，统计数字交给页面自己排。 */
export function Heatmap({ days, grain = 'day' }: { days: ContributionDay[]; grain?: 'day' | 'week' | 'total' }) {
  return (
    <ContributionSkyline
      data={days}
      view="2d"
      showToggle={false}
      showStats={false}
      // 8g：打开图例（少 → 多）；颜色取自令牌 --color-data-1..4（canvas 读不了 CSS 变量，这里写同一组值）
      showLegend
      // 第三版：从浅到深都是陶土橙叠在米白上（25% / 50% / 75% / 100%），不出现别的颜色
      palette={['#e6d2c9', '#d2ab9b', '#be846c', '#aa5c3d']}
      unit="张"
      unitPlural="张"
      title=""
      footer={null}
      locale="zh-CN"
      grain={grain}
    />
  )
}

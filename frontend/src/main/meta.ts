import type { LabImage } from '../lab/data'

export const PLATFORMS = ['小红书', 'Pinterest', '站酷', '花瓣', '本地']

const labels: Record<string, string> = { xhs: '小红书', pinterest: 'Pinterest', zcool: '站酷', huaban: '花瓣', local: '本地' }

export const sourceLabel = (item: LabImage) => item.platform ? labels[item.platform] ?? item.platform
  : PLATFORMS[item.id % PLATFORMS.length]

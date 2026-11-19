import type { LabImage } from '../lab/data'

export const PLATFORMS = ['小红书', 'Pinterest', '站酷', '花瓣', '本地']

export const sourceLabel = (item: LabImage) => PLATFORMS[item.id % PLATFORMS.length]

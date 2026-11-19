/** 瀑布流的排序档：时间 / 颜色 / 像不像。筛选与排序菜单挪到顶栏后，菜单和瀑布流共用这一份。 */
export type SortKey = 'time' | 'color' | 'similar'
export const SORTS: { k: SortKey; label: string }[] = [
  { k: 'time', label: '时间' },
  { k: 'color', label: '颜色' },
  { k: 'similar', label: '像不像' },
]

/**
 * 「克制版」改动的总开关。默认开；地址后面加 ?refine=0 就回到原版，用来对比或回滚。
 * 所有改动都读这一个常量，CSS 只在根节点带 .m-refine 时生效（见 refine.css）。
 */
export const REFINE = typeof window === 'undefined'
  || new URLSearchParams(window.location.search).get('refine') !== '0'

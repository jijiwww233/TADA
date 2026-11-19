import { useCallback, useSyncExternalStore } from 'react'

const mobileQuery = '(max-width: 768px)'
/** 手机：触屏 + 窄屏，和 styles/phone.css 用同一个条件；带鼠标的电脑永远不命中 */
export const PHONE_QUERY = '(hover: none) and (pointer: coarse) and (max-width: 600px)'

function useMedia(query: string) {
  const subscribe = useCallback((onChange: () => void) => {
    const list = window.matchMedia(query)
    list.addEventListener('change', onChange)
    return () => list.removeEventListener('change', onChange)
  }, [query])
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches)
}

export function useMobileLayout() {
  return useMedia(mobileQuery)
}

export function usePhoneLayout() {
  return useMedia(PHONE_QUERY)
}

/** 触屏设备（手机 + iPad，不论宽窄）；带鼠标的电脑永远不命中 */
export function useTouchLayout() {
  return useMedia('(hover: none) and (pointer: coarse)')
}

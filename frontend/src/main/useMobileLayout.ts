import { useSyncExternalStore } from 'react'

const mobileQuery = '(max-width: 768px)'

function subscribe(onChange: () => void) {
  const query = window.matchMedia(mobileQuery)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}

export function useMobileLayout() {
  return useSyncExternalStore(subscribe, () => window.matchMedia(mobileQuery).matches)
}

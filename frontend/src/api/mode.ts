import { useEffect, useState } from 'react'

const key = 'wominmin-data-mode-v2'
const change = 'wominmin-mode-changed'

export function useDemoMode() {
  const read = () => {
    const explicit = new URLSearchParams(window.location.search).get('data')
    if (explicit === 'live' || explicit === 'demo') return explicit === 'demo'
    if (new URLSearchParams(window.location.search).get('refine') === '0') return true
    return (localStorage.getItem(key) ?? import.meta.env.VITE_DEFAULT_DATA_MODE) !== 'real'
  }
  const [demo, setDemo] = useState(read)
  useEffect(() => {
    const refresh = () => setDemo(read())
    window.addEventListener('storage', refresh)
    window.addEventListener(change, refresh)
    return () => {
      window.removeEventListener('storage', refresh)
      window.removeEventListener(change, refresh)
    }
  }, [])
  return [demo, (value: boolean) => {
    const url = new URL(window.location.href)
    if (url.searchParams.has('data') || url.searchParams.get('refine') === '0') {
      if (url.searchParams.get('refine') === '0') url.searchParams.set('data', value ? 'demo' : 'live')
      else url.searchParams.delete('data')
      window.history.replaceState(window.history.state, '', url)
    }
    localStorage.setItem(key, value ? 'demo' : 'real')
    setDemo(value)
    window.dispatchEvent(new Event(change))
  }] as const
}

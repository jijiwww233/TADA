import { useState } from 'react'
export default function SyncAll() {
  const [sending, setSending] = useState(false), [error, setError] = useState('')
  return <div><button disabled={sending} onClick={async () => {
    setSending(true); setError('')
    try {
      const response = await fetch('/api/sync/all', { method: 'POST' })
      if (!response.ok) throw new Error('同步启动失败，请重试')
      window.dispatchEvent(new Event('sync-all-started'))
    } catch { setError('同步启动失败，请重试') } finally { setSending(false) }
  }}>同步全部平台</button>{error && <span role="alert">{error}</span>}</div>
}

import { useCallback, useEffect, useState } from 'react'
import { getAnalysisStatus, retryAnalysis, setAnalysisRunning, type AnalysisStatus } from './api'

const fmt = (n: number) => n.toLocaleString('zh-CN')

export default function AnalysisBar({ refreshKey }: { refreshKey: number }) {
  const [st, setSt] = useState<AnalysisStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setSt(await getAnalysisStatus())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    load()
  }, [load, refreshKey])

  // poll faster while the queue is running
  useEffect(() => {
    const t = setInterval(load, st?.running ? 2000 : 10000)
    return () => clearInterval(t)
  }, [load, st?.running])

  if (error) return <p className="analysis-bar error">{error}</p>
  if (!st) return null

  const toggle = async () => setSt(await setAnalysisRunning(!st.running))
  const retryFailed = async () => {
    await retryAnalysis()
    load()
  }

  return (
    <div className="analysis-bar">
      <span>
        AI 分析：已完成 {st.done} / 共 {st.total}
        {st.pending > 0 && ` · 待分析 ${st.pending}`}
        {st.processing > 0 && ` · 分析中 ${st.processing}`}
        {st.failed > 0 && <span className="error"> · 失败 {st.failed}</span>}
        {' · '}累计 {fmt(st.prompt_tokens + st.completion_tokens)} tokens（输入 {fmt(st.prompt_tokens)} / 输出{' '}
        {fmt(st.completion_tokens)}）
      </span>
      {st.configured ? (
        <button onClick={toggle}>{st.running ? '暂停分析' : '开始分析'}</button>
      ) : (
        <span className="error">还没配置 AI 服务，图片仍可浏览；配置后才能分析</span>
      )}
      {st.failed > 0 && <button onClick={retryFailed}>重试失败项</button>}
      {st.running && st.current_image_id !== null && <span className="muted">正在分析 #{st.current_image_id}</span>}
      {st.last_error && <span className="error">已暂停：{st.last_error}</span>}
    </div>
  )
}

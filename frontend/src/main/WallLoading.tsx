import './wall-loading.css'

const PLACEHOLDER_RATIOS = [0.78, 1.25, 1, 0.8, 1.3, 0.72, 1.1, 0.9]

export function WallLoading({ text = '正在打开素材库…' }: { text?: string }) {
  return (
    <div className="m-wall-loading" role="status" aria-live="polite">
      <p className="m-wall-loading-text">{text}</p>
      <div className="m-wall-loading-grid" aria-hidden="true">
        {PLACEHOLDER_RATIOS.map((ratio, i) => <span key={i} style={{ aspectRatio: ratio }} />)}
      </div>
    </div>
  )
}

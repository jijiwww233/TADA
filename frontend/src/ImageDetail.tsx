import ReferenceLabel from './ReferenceLabel'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { getImage, retryAnalysis, type ImageDetail as Detail, type Layout } from './api'
import AnnotationEditor from './annotations/AnnotationEditor'
import { thumbnailSrc } from './imageThumbnail'

const LAYOUT_LABELS: [Exclude<keyof Layout, 'layout_note'>, string][] = [
  ['title_position', '标题位置'],
  ['columns', '分栏'],
  ['whitespace', '留白'],
  ['image_text_ratio', '图文比例'],
  ['density', '密度'],
]

const STATUS_LABELS = { pending: '待分析', done: '已完成', failed: '失败' }

export default function ImageDetail({ imageId, onClose, loadImage = getImage, pinActions, onSimilar, onOpen }: {
  imageId: number; onClose: () => void; loadImage?: (id: number) => Promise<Detail>
  onOpen?: (id: number) => void
  onSimilar?: (id: number) => void
  pinActions?: ReactNode
}) {
  const [d, setD] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setD(await loadImage(imageId))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [imageId, loadImage])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const a = d?.analysis
  const retry = async () => {
    await retryAnalysis(imageId)
    load()
  }

  return (
    <div className="detail-backdrop" onClick={onClose}>
      <div className="detail" onClick={(e) => e.stopPropagation()}>
        <button className="detail-close" onClick={onClose} aria-label="关闭">
          ×
        </button>
        {error && <p className="error">{error}</p>}
        {d && (
          <>
            <div className="detail-image">
              <img src={d.url} alt={d.original_name ?? ''} />
            </div>
            <div className="detail-info">
              <ReferenceLabel imageId={imageId} />
              <h2>{d.original_name ?? `图片 #${d.id}`}</h2>
              {onSimilar && <button type="button" onClick={() => onSimilar(imageId)}>找相似</button>}
              {pinActions && <section aria-label="详情项目钉选">{pinActions}</section>}

              {d.posts.some((p) => p.platform === 'local') && (
                <p>
                  来源：本地 · 原文件名：{d.original_name ?? `图片 #${d.id}`} · 收藏时间：{d.created_at || '时间未知'}
                </p>
              )}

              {d.posts.some((p) => p.url?.startsWith('https://')) && (
                <section>
                  <h3>来源</h3>
                  {d.posts
                    .filter((p) => p.url?.startsWith('https://'))
                    .map((p) => (
                      <div key={p.id}>
                        {p.title || '无标题'} · 第 {p.position+1} / {p.image_count ?? p.position+1} 张{' '}
                        <a
                          href={p.platform === 'xhs' ? `/api/posts/${p.id}/source` : p.url!}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          原帖链接（网页端可能受限）
                        </a>
                        {p.platform === 'xhs' && (
                          <span className="muted">（冷却期间原帖联网解析已暂停）</span>
                        )}
                        {p.sync_error && <p className="error">同步失败：{p.sync_error}；本地素材保留</p>}
                        {p.connection_state==='needs_login' && <p className="error">登录已失效或尚未连接，本地图片仍可使用</p>}
                        {p.connection_state==='blocked' && <p className="error">平台被拦截，同步暂停，本地图片仍可使用</p>}
                        <p className="muted">本地已保存 · {p.source_state === 'deleted' ? '原帖已删除' : p.source_state === 'unavailable' ? '原帖暂不可用（原因未知）' : '原帖状态未确认'} · {p.fav_state === 'removed' ? '已取消收藏' : p.fav_state === 'possibly_removed' ? '可能已取消收藏（未确认）' : p.fav_state === 'unverified' ? '收藏状态待确认' : '保留本地素材'}</p>
                        {p.is_video && <p>仅分析封面与简介</p>}
                        {(p.images?.length ?? 0)>1 && <ol aria-label="帖子图片顺序" className="detail-post-images">{p.images!.map(slot=><li key={slot.position}>
                          <button type="button" disabled={slot.image_id===null || !slot.url || !onOpen} onClick={()=>{if(slot.image_id!==null)onOpen?.(slot.image_id)}} aria-label={`第${slot.position+1}张，共${p.image_count ?? p.images!.length}张`}>
                            {slot.url ? <img src={thumbnailSrc(slot.url)} alt={`第${slot.position+1}张`} width="72" height="72" loading="lazy"/> : <span>第{slot.position+1}张尚未保存</span>}
                          </button></li>)}</ol>}
                      </div>
                    ))}
                </section>
              )}

              <section>
                <h3>主色（Pillow 提取）</h3>
                {d.colors.length === 0 ? (
                  <p className="muted">还没提取</p>
                ) : (
                  <div className="swatches">
                    {d.colors.map((c) => (
                      <div key={c.rank} className="swatch">
                        <span style={{ background: c.hex }} />
                        <code>{c.hex}</code>
                        <small>{(c.ratio * 100).toFixed(1)}%</small>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              {!a || a.status === 'pending' ? (
                <p className="muted">AI 分析：{a?.processing ? '分析中…' : '待分析'}</p>
              ) : a.status === 'failed' ? (
                <section>
                  <p className="error">AI 分析失败，图片仍保存在本地：{a.error || '请稍后重试'}</p>
                  <button onClick={retry}>重试</button>
                </section>
              ) : (
                <>
                  <section>
                    <h3>描述</h3>
                    <p>{a.description}</p>
                  </section>
                  <section>
                    <h3>主题</h3>
                    <div className="tags">
                      {a.subject?.map((t) => <span key={t}>{t}</span>)}
                    </div>
                  </section>
                  <section>
                    <h3>图中文字</h3>
                    {a.visible_text ? <pre className="visible-text">{a.visible_text}</pre> : <p className="muted">无</p>}
                  </section>
                  {a.layout && (
                    <section>
                      <h3>排版</h3>
                      <dl>
                        {LAYOUT_LABELS.map(([k, label]) => (
                          <div key={k}>
                            <dt>{label}</dt>
                            <dd>{Array.isArray(a.layout![k]) ? a.layout![k].join(' / ') || '未记录' : '未记录'}</dd>
                          </div>
                        ))}
                      </dl>
                      {a.layout.layout_note && <p>{a.layout.layout_note}</p>}
                    </section>
                  )}
                  {a.style && (
                    <section>
                      <h3>风格</h3>
                      <div className="tags">
                        {a.style.tags.map((t) => <span key={t}>{t}</span>)}
                        <span className="medium">{a.style.medium}</span>
                      </div>
                      {a.style.style_note && <p>{a.style.style_note}</p>}
                    </section>
                  )}
                  <section>
                    <h3>我的标注（可修改）</h3>
                    <AnnotationEditor
                      imageId={imageId}
                      annotation={d.annotation ?? null}
                      aiDescription={a.description}
                      aiTags={a.subject}
                      onSaved={() => { void load() }}
                    />
                  </section>
                </>
              )}

              {a && (
                <p className="meta">
                  {STATUS_LABELS[a.status]}
                  {a.model && ` · ${a.model}`}
                  {' · '}
                  {a.prompt_tokens === null && a.completion_tokens === null
                    ? '无 token 记录'
                    : `输入 ${a.prompt_tokens ?? 0} / 输出 ${a.completion_tokens ?? 0} tokens`}
                  {' · '}调用 {a.attempts} 次
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

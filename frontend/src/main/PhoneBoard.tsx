import { useEffect, useState } from 'react'
import type { LabImage } from '../lab/data'
import { thumbnailSrc } from '../imageThumbnail'
import { createPipSceneApi } from '../pip/persistence'

/**
 * 手机上的参考画板（简单版）：画布在手机上没法用，这里只把画板里的图按两列排出来，点图进详情。
 * 电脑和 iPad（触屏但宽度 > 600）仍然打开原来的画布小窗，这个组件只在手机上出现。
 * 有项目时先读画布里保存的图（按画布上从上到下、从左到右的顺序），再补上项目里还没放上画布的图；
 * 读不到（演示数据、网络出错）就直接用项目里的图。
 */
export function PhoneBoard({ projectId, name, items, library, onOpen }: {
  projectId: number | null
  name: string | null
  items: LabImage[]
  library: LabImage[]
  onOpen: (item: LabImage, list: LabImage[], index: number) => void
}) {
  const [shown, setShown] = useState<LabImage[]>(items)
  useEffect(() => {
    setShown(items)
    if (projectId === null) return
    let live = true
    createPipSceneApi().get(projectId).then(snapshot => {
      if (!live || !snapshot.scene) return
      const known = new Map([...library, ...items].map(item => [item.id, item]))
      const placed = [...snapshot.scene.items]
        .sort((a, b) => a.y - b.y || a.x - b.x)
        .flatMap(entry => known.get(entry.image_id) ?? [])
      const ids = new Set(placed.map(item => item.id))
      setShown([...placed, ...items.filter(item => !ids.has(item.id))])
    }).catch(() => { /* 读不到就用项目里的图 */ })
    return () => { live = false }
  }, [projectId, items, library])

  return (
    <section className="m-page m-pboard">
      <p className="m-pboard-hint">参考画板需要大屏幕，请在电脑或 iPad 上打开。</p>
      <h2 className="m-pboard-title">{name ?? '全部收藏'}<span className="m-pboard-count">{shown.length} 张</span></h2>
      {shown.length ? (
        <div className="m-pboard-grid">
          {shown.map((item, index) => (
            <button type="button" key={item.id} className="m-pboard-tile" onClick={() => onOpen(item, shown, index)}
              style={{ aspectRatio: `${item.w} / ${item.h}` }}>
              <img src={thumbnailSrc(item.src)} alt="" width={item.w} height={item.h} loading="lazy" />
            </button>
          ))}
        </div>
      ) : (
        <p className="m-pboard-empty">画板里还没有图。</p>
      )}
    </section>
  )
}

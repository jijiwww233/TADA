import CircularGallery from '../lab/vendor/reactbits/CircularGallery'
import { take, type Manifest } from '../lab/data'

/** 首屏底部的旋转海报弧：复用 lab 的 CircularGallery（原生 WebGL2），按 Accutal 的占地面积裁断。 */
export function ArcGallery({ manifest, reduced }: { manifest: Manifest; reduced: boolean }) {
  const items = take(manifest.cards, 16).map(i => ({ image: i.src, alt: `收藏 ${i.id}` }))
  return (
    <div className="m-arc" aria-hidden>
      <CircularGallery items={items} bend={2.4} font="600 24px system-ui" showTitles={false}
        borderRadius={0.06} autoScroll={reduced ? 0 : 0.28} scrollEase={0.04} shadow={0.12} />
    </div>
  )
}

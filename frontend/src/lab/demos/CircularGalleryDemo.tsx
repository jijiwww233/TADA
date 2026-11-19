import CircularGallery from '../vendor/reactbits/CircularGallery'
import { take, useImages } from '../data'

export default function CircularGalleryDemo({ reduced }: { reduced: boolean }) {
  const items = take(useImages().cards, 16).map(i => ({ image: i.src, alt: `收藏 #${i.id}` }))
  // System font + no fontUrl, so the component never fetches Google Fonts.
  return (
    <div className="bg-white" style={{ height: 520 }}>
      <CircularGallery items={items} bend={2.4} textColor="#2b2d33" font="600 24px system-ui" showTitles={false}
        borderRadius={0.06} autoScroll={reduced ? 0 : 0.35} scrollEase={0.04} shadow={0.12} />
    </div>
  )
}

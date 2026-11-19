import type { ImageItem } from './api'
import { startImageDrag } from './pip/dragData'
import { thumbnailSrc } from './imageThumbnail'

export default function ImageGrid({ images, onOpen }: { images: ImageItem[]; onOpen: (id: number) => void }) {
  if (images.length === 0) return <p className="empty">还没有图片，先拖几张进来。</p>
  return (
    <div className="grid">
      {images.map((img) => (
        <figure key={img.id} title={img.original_name ?? ''} draggable
          onDragStart={event => startImageDrag(event, img.id)}
          onClick={() => onOpen(img.id)}>
          <img src={thumbnailSrc(img.url)} alt={img.original_name ?? ''} loading="lazy" draggable={false} />
          {(img.image_count ?? 0)>1 && <figcaption>第 {img.position+1} / {img.image_count} 张</figcaption>}
          {img.is_video && <figcaption>仅分析封面与简介</figcaption>}
        </figure>
      ))}
    </div>
  )
}

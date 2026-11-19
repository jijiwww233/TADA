import { useState } from 'react'
import { Capsule } from '../Capsule'
import { Replay } from '../Demo'
import { useImages } from '../data'
import { HalftoneNumber } from '../reveal'

/** "N 张收藏 → M 张视觉参考" in the clear capsule, 半调 entrance only. */
export default function NumbersDemo() {
  const { total, visual_ref } = useImages()
  const [k, setK] = useState(0)
  const N = HalftoneNumber
  const cls = 'text-[20px] font-semibold text-[var(--wm-title)]'
  return (
    <div className="relative [display:grid] h-[260px] place-items-center overflow-hidden bg-[var(--page)] px-6">
      <Replay onClick={() => setK(k + 1)} />
      <div className="relative w-[min(620px,100%)]">
        <Capsule>
          <span className="num flex items-baseline gap-2 whitespace-nowrap">
            <N value={total} replay={k} className={cls} />
            <span className="text-[var(--wm-body)]">张收藏 →</span>
            <N value={visual_ref} replay={k} delay={600} className={cls} />
            <span className="text-[var(--wm-body)]">张视觉参考</span>
          </span>
        </Capsule>
      </div>
    </div>
  )
}

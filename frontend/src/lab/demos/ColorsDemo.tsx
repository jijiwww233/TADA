import { Capsule } from '../Capsule'
import { Headline } from '../title'

/** The picked text colours (油墨) + second accent (B 旧纸), all from the --wm-* variables in lab.css. */
export default function ColorsDemo() {
  return (
    <section className="[display:grid] grid-cols-[minmax(0,340px)_minmax(0,1fr)] items-center gap-x-12 bg-[var(--page)] px-12 py-10">
      <Headline lead="zh" size={76} />
      <div className="flex flex-col gap-3.5">
        <p className="text-[15px] text-[var(--wm-body)]">正文：收藏过的图，用一句话就能找回来。</p>
        <p className="xs-row text-[var(--wm-muted)]"><span>2,318 saved</span><span>2019—2026</span></p>
        <Capsule><input aria-label="搜索" className="w-full" defaultValue="去年夏天那张芥末黄的海报" /></Capsule>
        <Capsule />
      </div>
    </section>
  )
}

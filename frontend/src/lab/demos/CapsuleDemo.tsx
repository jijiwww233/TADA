import { Capsule } from '../Capsule'

/** The clear capsule: nothing under it. Clay send > second accent (B 旧纸) on the selected pill > the rest. */
export default function CapsuleDemo() {
  return (
    <div className="flex flex-col items-center bg-[var(--page)] px-6 py-10">
      <div className="w-[min(640px,100%)]"><Capsule placeholder="试试 Tab、左右切换" /></div>
    </div>
  )
}

import { MeshGradient } from '@paper-design/shaders-react'

/** 胶囊背后的叠色：@paper-design MeshGradient，只用一块很淡的光斑（取自收藏图的芥末黄、铁锈红）。 */
export function CapsuleMesh({ colors }: { colors: string[] }) {
  return (
    <div className="m-mesh" aria-hidden>
      <MeshGradient colors={colors} speed={0.06} distortion={0.8} swirl={0.3} style={{ width: '100%', height: '100%' }} />
    </div>
  )
}

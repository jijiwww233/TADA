import { spread, take, useImages } from '../data'
import { RING_COUNT, RING_RADIUS, RING_SECONDS, ringAngle } from '../intro/ringGeometry'

/** Hand-made reference: 16 cards on a CSS 3D ring, one slow turn every 40s. No library. */
export default function CssRingDemo() {
  const images = take(spread(useImages().cards), RING_COUNT)
  const radius = RING_RADIUS
  return (
    <div className="relative [display:grid] h-[520px] place-items-center overflow-hidden bg-white" style={{ perspective: 1600 }}>
      <div className="lab-ring relative h-[190px] w-[150px]" style={{ transformStyle: 'preserve-3d', animationDuration: `${RING_SECONDS}s` }}>
        {images.map((img, i) => (
          <div key={i} className="absolute inset-0 overflow-hidden rounded-2xl bg-white shadow-[0_10px_30px_oklch(0.3_0.03_260/0.1)]"
            style={{ transform: `rotateY(${ringAngle(i, images.length)}deg) translateZ(${radius}px)`, backfaceVisibility: 'hidden' }}>
            <img src={img.src} alt="" className="size-full object-cover" draggable={false} />
          </div>
        ))}
      </div>
    </div>
  )
}

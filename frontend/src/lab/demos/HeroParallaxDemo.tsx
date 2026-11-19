import { HeroParallax } from '../vendor/aceternity/hero-parallax'
import { take, useImages } from '../data'

export default function HeroParallaxDemo() {
  const products = take(useImages().cards, 15).map((img, i) => ({ title: `#${img.id}-${i}`, link: '#', thumbnail: img.src }))
  return <div className="bg-white"><HeroParallax products={products} /></div>
}

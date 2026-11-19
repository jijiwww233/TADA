import { useEffect, useMemo, useState } from 'react'
import CircularGallery from '../vendor/reactbits/CircularGallery'
import { take, useImages } from '../data'
import { cardThumbnailUrl } from './cardThumbnail'

/** Same component and local images as the lab's “一圈图片” group. No remote font URL. */
export function IntroGallery({ reduced }: { reduced: boolean }) {
  const [speed,setSpeed]=useState(reduced?0:-2.8)
  useEffect(()=>{
    if(reduced){setSpeed(0);return}
    const started=performance.now()
    setSpeed(-2.8)
    const timer=setInterval(()=>{
      const t=Math.min(1,(performance.now()-started)/6500)
      setSpeed(-.35-2.45*(1-t)**3)
      if(t===1)clearInterval(timer)
    },50)
    return()=>clearInterval(timer)
  },[reduced])
  const { cards } = useImages()
  // 一圈图片也用缩略图（长边 <=1024），避免全尺寸图进 WebGL 纹理。
  const [thumbs, setThumbs] = useState<string[]>(() => cards.map(card => card.src))
  useEffect(() => {
    let live = true
    Promise.all(cards.map(card => cardThumbnailUrl(card.src))).then(next => { if (live) setThumbs(next) }, () => {})
    return () => { live = false }
  }, [cards])
  const items = useMemo(() => take(cards,16).map((i,index)=>({ image:thumbs[index % Math.max(1, thumbs.length)] ?? i.src, alt:`收藏 #${i.id}` })),[cards,thumbs])
  return <div className="intro-circular-gallery">
    {items.length>0&&<CircularGallery items={items} bend={2.4} font="600 24px system-ui" showTitles={false}
      borderRadius={.06} autoScroll={speed} scrollEase={.04} shadow={.12}
      intro={!reduced} wave={reduced?0:1} openOnClick={false} />}
  </div>
}

import { useEffect, useMemo, useState } from 'react'
import CircularGallery from '../vendor/reactbits/CircularGallery'
import { take, useImages } from '../data'
import { cardThumbnailUrl } from './cardThumbnail'

/** Same component and local images as the lab's “一圈图片” group. No remote font URL. */
export function IntroGallery({ reduced, phone = false }: { reduced: boolean; phone?: boolean }) {
  const [speed,setSpeed]=useState(reduced||phone?0:-2.8)
  useEffect(()=>{
    if(reduced){setSpeed(0);return}
    const started=performance.now()
    if(phone){
      // 手机：环刚在画布上落成这一排，转盘先停 0.6 秒，再用 1.5 秒从 0 缓缓加到巡航速度 −0.35（电脑版不变）
      setSpeed(0)
      const timer=setInterval(()=>{
        const t=Math.min(1,Math.max(0,(performance.now()-started-600)/1500))
        setSpeed(-.35*t*t*(3-2*t))
        if(t===1)clearInterval(timer)
      },50)
      return()=>clearInterval(timer)
    }
    setSpeed(-2.8)
    const timer=setInterval(()=>{
      const t=Math.min(1,(performance.now()-started)/6500)
      setSpeed(-.35-2.45*(1-t)**3)
      if(t===1)clearInterval(timer)
    },50)
    return()=>clearInterval(timer)
  },[reduced,phone])
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
      // 手机：电脑版 1440×900 时卡高 216、宽 168（屏宽 11.7%），卡距 = 1.26 卡宽，一屏约 6.8 张。
      // 直接按屏宽 11.7% 换算到 390 只有 46px（指甲盖），所以按「一屏约 2.6 张：中间一张完整、两边各露一部分」定：
      // 卡距 150 → 卡宽 119（屏宽 31%）、高 153；转盘高约 287 → itemSize = 153 / (0.6 × 287) ≈ 0.89（原来 0.52 时卡宽只有 70）
      itemSize={phone ? .89 : undefined} borderRadius={.06} autoScroll={speed} scrollEase={.04} shadow={.12}
      // 手机：整理页的环已经在画布上滑下来排成这一排了（flowScene 的 row），转盘不再自己飞入，直接接上
      intro={!reduced && !phone} wave={reduced?0:1} openOnClick={false} />}
  </div>
}

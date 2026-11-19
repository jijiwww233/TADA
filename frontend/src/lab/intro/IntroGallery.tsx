import { useEffect, useMemo, useState } from 'react'
import CircularGallery from '../vendor/reactbits/CircularGallery'
import { take, useImages } from '../data'
import { cardThumbnailUrl } from './cardThumbnail'
import { ONE_TAKE_GALLERY_HOLD_MS, ONE_TAKE_GALLERY_SPEED, PHONE_ONE_TAKE, oneTakeGallery, oneTakeItemSize } from './oneTake'

/** Same component and local images as the lab's “一圈图片” group. No remote font URL. */
export function IntroGallery({ reduced, phone = false }: { reduced: boolean; phone?: boolean }) {
  /** 一镜到底（手机）：卡片从环上飞进来落成这一排，转盘卡宽 = S、停得更久再转、转速快 1.5 倍、从环正前方那张开始排 */
  const oneTake = phone && PHONE_ONE_TAKE
  const [speed,setSpeed]=useState(reduced||phone?0:-2.8)
  useEffect(()=>{
    if(reduced){setSpeed(0);return}
    const started=performance.now()
    if(phone){
      // 手机：环刚在画布上落成这一排，转盘先停着，再用 1.5 秒从 0 缓缓加到巡航速度 −0.35（电脑版不变）。
      // 停的时间从 0.6 秒加到 1.0 秒：画布上那排卡要到 0.85 秒才淡完，转盘提前转起来会和它错开、看到两层图
      setSpeed(0)
      const hold=oneTake?ONE_TAKE_GALLERY_HOLD_MS:1000, cruise=oneTake?ONE_TAKE_GALLERY_SPEED:-.35
      const timer=setInterval(()=>{
        const t=Math.min(1,Math.max(0,(performance.now()-started-hold)/1500))
        setSpeed(cruise*t*t*(3-2*t))
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
  },[reduced,phone,oneTake])
  const { cards } = useImages()
  // 一圈图片也用缩略图（长边 <=1024），避免全尺寸图进 WebGL 纹理。
  const [thumbs, setThumbs] = useState<string[]>(() => cards.map(card => card.src))
  // 手机：等缩略图到了再挂转盘（最多等 0.5 秒）。原来先用原图挂上、缩略图一到 items 变了，转盘整个重建一次，
  // 正好落在「环 → 转盘」交接的那一下，看起来卡一下。画布那排卡在这段时间里垫着。
  const [thumbsReady, setThumbsReady] = useState(!phone)
  useEffect(() => {
    let live = true, settled = false
    const timer = phone ? setTimeout(() => { if (live && !settled) { settled = true; setThumbsReady(true) } }, 500) : undefined
    Promise.all(cards.map(card => cardThumbnailUrl(card.src))).then(next => {
      if (!live || (phone && settled)) return
      settled = true; setThumbs(next); setThumbsReady(true)
    }, () => { if (live && !settled) { settled = true; setThumbsReady(true) } })
    return () => { live = false; clearTimeout(timer) }
  }, [cards, phone])
  // 一镜到底：转盘第 0 格（正中）= 环散开时正前方那张，其余按环上的顺序往两边排（挂上时读一次）
  const [offset] = useState(() => oneTake ? oneTakeGallery.offset : 0)
  const items = useMemo(() => {
    const list = take(cards,16).map((i,index)=>({ image:thumbs[index % Math.max(1, thumbs.length)] ?? i.src, alt:`收藏 #${i.id}` }))
    return offset && list.length ? list.map((_, m) => list[(m + offset) % list.length]) : list
  },[cards,thumbs,offset])
  const itemSize = useMemo(() => !phone ? undefined : oneTake ? oneTakeItemSize(innerWidth, innerHeight) : .89, [phone, oneTake])
  if (!thumbsReady) return null
  return <div className="intro-circular-gallery">
    {items.length>0&&<CircularGallery items={items} bend={2.4} font="600 24px system-ui" showTitles={false}
      // 手机：电脑版 1440×900 时卡高 216、宽 168（屏宽 11.7%），卡距 = 1.26 卡宽，一屏约 6.8 张。
      // 直接按屏宽 11.7% 换算到 390 只有 46px（指甲盖），所以按「一屏约 2.6 张：中间一张完整、两边各露一部分」定：
      // 卡距 150 → 卡宽 119（屏宽 31%）、高 153；转盘高约 287 → itemSize = 153 / (0.6 × 287) ≈ 0.89（原来 0.52 时卡宽只有 70）
      // 一镜到底：卡宽 = 基准尺寸 S（390 宽 86.4px），itemSize 按屏幕算（oneTake.ts 的 oneTakeItemSize）
      itemSize={itemSize} borderRadius={.06} autoScroll={speed} scrollEase={.04} shadow={.12}
      // 手机：整理页的环已经在画布上滑下来排成这一排了（flowScene 的 row），转盘不再自己飞入，直接接上
      intro={!reduced && !phone} wave={reduced?0:1} openOnClick={false} />}
  </div>
}

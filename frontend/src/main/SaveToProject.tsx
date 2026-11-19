import { useEffect, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { FEEDBACK, OVERLAY, OVERLAY_TWEEN, enterDelay } from './motion'
import type { LabImage } from '../lab/data'
import { thumbnailSrc } from '../imageThumbnail'
import { GlassFolder, type FolderFile } from './GlassFolder'

/* 第五轮统一：飞行 / 飞走用浮层弹簧（260 / 28），合上“弹一下”用小反馈（0.15s），每张按出场错开（40ms，最多 8 个） */
const FLY = OVERLAY
const BUMP = { duration: FEEDBACK.duration, ease: FEEDBACK.ease }
const FLIGHT = Math.round(OVERLAY_TWEEN.duration * 1000) // 一张飞到文件夹口要多久（弹簧停稳的时间）
const SETTLE = 260 // 合上以后停一下再飞走（等待，不是动画）
const AWAY = Math.round(OVERLAY_TWEEN.duration * 1000) // 文件夹缩小飞向导航
const FOLDER_W = 200

type Rect = { x: number; y: number; w: number; h: number }
export type SaveJob = { name: string; items: LabImage[]; from: Rect[] }

/**
 * “存为项目”：选中的图依次飞进毛玻璃文件夹 → 合上弹一下 → 缩小飞向顶栏 PROJECTS。
 * 只是一层不接收指针的浮层；飞到时调 onArrive（加项目、让 PROJECTS 下划线闪一下），播完调 onDone。
 */
export function SaveToProject({ job, target, onArrive, onDone }: {
  job: SaveJob
  /** 顶栏 PROJECTS 的位置 */
  target: Rect | null
  onArrive: () => void
  onDone: () => void
}) {
  const reduce = useReducedMotion()
  const n = job.items.length
  const [files, setFiles] = useState<FolderFile[]>([])
  const [landed, setLanded] = useState<Set<number>>(new Set())
  const [phase, setPhase] = useState<'fill' | 'close' | 'away'>('fill')

  const vw = window.innerWidth
  const vh = window.innerHeight
  // 文件夹落在视口正中；文件夹口大约在它上沿往下 30%
  const fx = vw / 2 - FOLDER_W / 2
  const fy = vh / 2 - FOLDER_W * 0.48
  const mouth = { x: vw / 2, y: fy + FOLDER_W * 0.3 }

  useEffect(() => {
    if (reduce) { onArrive(); onDone(); return }
    const timers: number[] = []
    job.items.forEach((it, i) => {
      timers.push(window.setTimeout(() => {
        setLanded(prev => new Set(prev).add(i))
        setFiles(prev => [...prev, { name: `${it.id}-${i}`, image: thumbnailSrc(it.src) }])
      }, enterDelay(i) * 1000 + FLIGHT))
    })
    const closeAt = enterDelay(n - 1) * 1000 + FLIGHT + 20
    timers.push(window.setTimeout(() => setPhase('close'), closeAt))
    timers.push(window.setTimeout(() => setPhase('away'), closeAt + SETTLE))
    timers.push(window.setTimeout(onArrive, closeAt + SETTLE + AWAY))
    timers.push(window.setTimeout(onDone, closeAt + SETTLE + AWAY + 120))
    return () => timers.forEach(t => window.clearTimeout(t))
    // 一次存入只播一遍
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (reduce) return null

  const away = phase === 'away' && target
  const tx = away ? target.x + target.w / 2 - (fx + FOLDER_W / 2) : 0
  const ty = away ? target.y + target.h / 2 - (fy + FOLDER_W * 0.5) : 0

  return (
    <div className="m-savefly" aria-hidden="true">
      <motion.div className="m-savefly-folder" style={{ left: fx, top: fy, width: FOLDER_W }}
        initial={{ opacity: 0, scale: 0.9 }}
        animate={away
          ? { opacity: 0.2, scale: 0.08, x: tx, y: ty, transition: FLY }
          : phase === 'close'
            ? { opacity: 1, scale: [0.94, 1], x: 0, y: 0, transition: BUMP }
            : { opacity: 1, scale: 1, x: 0, y: 0, transition: FLY }}>
        <GlassFolder label={job.name} meta={`${n} 张`} image={job.items[0] && thumbnailSrc(job.items[0].src)} files={files} open={phase === 'fill'} action="none" width={FOLDER_W} />
      </motion.div>

      {job.items.map((it, i) => {
        const from = job.from[i]
        if (!from || landed.has(i)) return null
        const w = 56
        const h = w * (it.h / it.w)
        return (
          <motion.img key={`${it.id}-${i}`} className="m-savefly-card" src={thumbnailSrc(it.src)} alt=""
            style={{ left: 0, top: 0 }}
            initial={{ x: from.x, y: from.y, width: from.w, height: from.h, opacity: 1 }}
            animate={{ x: mouth.x - w / 2, y: mouth.y - h / 2, width: w, height: h, opacity: 0.9,
              transition: { ...FLY, delay: enterDelay(i) } }} />
        )
      })}
    </div>
  )
}

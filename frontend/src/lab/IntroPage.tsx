import { useEffect, useMemo } from 'react'
import { MotionConfig } from 'motion/react'
import './lab-reset.css'
import './lab.css'
import { ManifestProvider, useManifest, warmHue } from './data'
import ScatterStage from './scatter/ScatterStage'
import { BOUNCES } from './scatter/choreo'
import { homePlan } from './scatter/homePlans'
import './scatter/home.css'
// 开场大标题「TADA! 图然想起」：引导页自带的中文衬线字体只裁了旧标题那几个字，「图然想起」要退到主界面那套宋体
import '../main/main-serif-sc.css'

/**
 * /intro：只有引导页本身的入口（课堂演示用）。舞台、状态机、接线都是 /lab#scatter 里那一套，
 * 只是不带选样页的说明文字，调试开关也收起来。地址后面加 ?play 会自动从头播放；加 ?page=key / platforms / ready 直接从那一页开始（排练用）。
 */
export default function IntroPage() {
  const manifest = useManifest()
  const plan = useMemo(() => homePlan('premotio'), [])
  const autoplay = useMemo(() => new URLSearchParams(location.search).has('play'), [])
  const startPage = useMemo(() => {
    const page = new URLSearchParams(location.search).get('page')
    return page === 'key' || page === 'platforms' || page === 'ready' ? page : undefined
  }, [])
  useEffect(() => { document.title = 'TADA! 图然想起' }, [])

  if (manifest === null) return <div className="lab" />
  if (manifest === 'missing') {
    return <div className="lab [display:grid] place-items-center p-10 text-center"><p>没有找到展示图片</p></div>
  }
  return (
    <ManifestProvider value={manifest}>
      <MotionConfig reducedMotion="user">
        <div className="lab" style={{ '--paper-h': warmHue(manifest) } as React.CSSProperties}>
          <ScatterStage plan={plan} bounce={BOUNCES[1]} direction="premotio" english={false} reduced={false} clean autoplay={autoplay} startPage={startPage} />
        </div>
      </MotionConfig>
    </ManifestProvider>
  )
}

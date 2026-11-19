import { lazy, Suspense, useEffect, useState, type ComponentType } from 'react'
import { MotionConfig } from 'motion/react'
import './lab-reset.css'
import './lab.css'
import { Demo, type DemoMeta } from './Demo'
import { ManifestProvider, useManifest, warmHue } from './data'
import { GROUPS, type DemoEntry } from './meta'
import { MEASURED } from './measured'

const demos = {
  scatter: lazy(() => import('./demos/ScatterDemo')),
  hero: lazy(() => import('./demos/HeroParallaxDemo')),
  circular: lazy(() => import('./demos/CircularGalleryDemo')),
  cssring: lazy(() => import('./demos/CssRingDemo')),
  reveal: lazy(() => import('./demos/RevealDemo')),
  blurfade: lazy(() => import('./demos/BlurFadeDemo')),
  numbers: lazy(() => import('./demos/NumbersDemo')),
  colors: lazy(() => import('./demos/ColorsDemo')),
  capsule: lazy(() => import('./demos/CapsuleDemo')),
  fonts: lazy(() => import('./demos/FontsDemo')),
}
export type DemoKey = keyof typeof demos

function DemoView({ entry, reduced }: { entry: DemoEntry; reduced: boolean }) {
  const C = demos[entry.component] as ComponentType<Record<string, unknown>>
  return (
    <Demo meta={{ ...entry.meta, deps: MEASURED[entry.meta.id]?.deps ?? entry.meta.deps, measured: MEASURED[entry.meta.id]?.fps } as DemoMeta} tall={entry.tall} bleed={entry.bleed}>
      <Suspense fallback={<div className="h-[260px]" />}>
        <C reduced={reduced} {...entry.props} />
      </Suspense>
    </Demo>
  )
}

export default function Lab() {
  const manifest = useManifest()
  const [reduced, setReduced] = useState(false)
  useEffect(() => { document.title = '选样页 · 我明明收藏过' }, [])

  if (manifest === null) return <div className="lab" />
  if (manifest === 'missing') {
    return (
      <div className="lab [display:grid] place-items-center p-10 text-center">
        <p>没有找到展示图片。先运行：<code>backend/.venv/Scripts/python tools/lab_show_assets.py</code>（读 D:\CC-Work\给克的\展示页图片）</p>
      </div>
    )
  }
  return (
    <ManifestProvider value={manifest}>
      <MotionConfig reducedMotion={reduced ? 'always' : 'user'}>
        <div className={`lab ${reduced ? 'lab--reduced' : ''}`} style={{ '--paper-h': warmHue(manifest) } as React.CSSProperties}>
          <nav className="paper sticky top-3 z-50 mx-auto mt-3 flex w-[min(1100px,calc(100%-24px))] flex-wrap items-center gap-x-4 gap-y-1 rounded-full px-5 py-2.5 text-[13px]">
            <strong className="mr-2 text-[14px]">选样页</strong>
            {GROUPS.map((g, i) => <a key={g.id} href={`#${g.id}`} className="text-[var(--ink-2)] no-underline hover:text-[var(--ink)]">{i + 1}. {g.short}</a>)}
            <label className="ml-auto flex cursor-pointer items-center gap-2 text-[var(--ink-2)]">
              <input type="checkbox" checked={reduced} onChange={e => setReduced(e.target.checked)} />减少动效
            </label>
          </nav>

          <div className="mx-auto w-[min(1100px,calc(100%-24px))] pb-32 pt-14">
            <h1 className="text-[34px] font-semibold tracking-tight">选样页 · 一至三页连续演示</h1>
            <p className="mt-3 max-w-[760px] text-[var(--ink-2)]">
              第 1 组：第一页散卡排墙、收进胶囊后，接填钥匙、连平台、揭晓和恢复搜索框；同一页面、同一个胶囊。勾选“演示模式”从头自动播放，或用“从第二页开始”单独查看。全程本地模拟，不接后端、不打开窗口、不保存钥匙。
              首页原 16 张散卡、楼梯标题和纯 B 墙保留，配色统一用“油墨 + 旧纸 B”的变量。其它组件演示仍保留。图片使用你挑的 {manifest.items.length} 张。
              帧率前半是实测记录（无头 Chrome · Intel UHD 核显 · 不锁 60 帧，数字代表余量；第 1 组是快速滚动时测的）；“当前”是你这台浏览器此刻的帧率。
            </p>
            {GROUPS.map((g, i) => (
              <section key={g.id} id={g.id} className="mt-20 scroll-mt-24">
                <h2 className="text-[24px] font-semibold">{i + 1}. {g.title}</h2>
                <p className="mt-2 max-w-[760px] text-[var(--ink-2)]">要的：{g.target}</p>
                <div className="mt-6 [display:grid] gap-8">
                  {g.demos.map(d => <DemoView key={d.meta.id} entry={d} reduced={reduced} />)}
                </div>
              </section>
            ))}
          </div>
        </div>
      </MotionConfig>
    </ManifestProvider>
  )
}

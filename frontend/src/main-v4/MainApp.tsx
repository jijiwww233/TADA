import { useEffect, useRef, useState } from 'react'
import { FolderClosed, GalleryVerticalEnd, PictureInPicture2, Settings2, Upload, X } from 'lucide'
import { Icon } from '../lab/Icon'
import { take, useManifest, type LabImage } from '../lab/data'
import { Detail } from './Detail'
import { Library } from './Library'
import { Onboarding } from './Onboarding'
import { Projects } from './Projects'
import { SettingsDrawer } from './SettingsDrawer'
import { PLATFORMS } from './meta'
import './main.css'

type View = 'library' | 'projects'

/** /main：注册流程之后的主界面 demo。一个主页 + 一个项目页，其余全部是浮层。 */
export default function MainApp() {
  const manifest = useManifest()
  const [view, setView] = useState<View>('library')
  const [detail, setDetail] = useState<LabImage | null>(null)
  const [pendingOpen, setPendingOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [reduce, setReduce] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [scrolled, setScrolled] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [keep, setKeep] = useState<Set<number>>(new Set())
  const [platforms, setPlatforms] = useState<Set<string>>(new Set())
  const [tutorialOpen, setTutorialOpen] = useState(true)
  const fileRef = useRef<HTMLInputElement | null>(null)
  const toastTimer = useRef<number | null>(null)

  const say = (msg: string) => {
    setToast(msg)
    if (toastTimer.current) window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(null), 2400)
  }

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 80)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    if (view === 'projects') window.scrollTo(0, 0)
  }, [view])

  if (manifest === null) return <div className="m-app" />
  if (manifest === 'missing') {
    return <div className="m-app" style={{ display: 'grid', placeItems: 'center', minHeight: '100vh' }}>
      <p style={{ fontFamily: 'var(--m-mono)', fontSize: 12, color: 'var(--m-ink-3)' }}>没有找到展示图片：先运行 backend/.venv/Scripts/python tools/lab_show_assets.py</p>
    </div>
  }

  const togglePlatform = (p: string) => {
    const next = new Set(platforms)
    if (next.has(p)) next.delete(p); else next.add(p)
    setPlatforms(next)
  }
  const pendingItems = take(manifest.cards, 12)
  const dock = [
    { k: 'library', label: '素材库', icon: <Icon icon={GalleryVerticalEnd} size={20} strokeWidth={1.5} />, on: view === 'library', act: () => setView('library') },
    { k: 'projects', label: '项目', icon: <Icon icon={FolderClosed} size={20} strokeWidth={1.5} />, on: view === 'projects', act: () => setView('projects') },
    { k: 'pip', label: '小窗', icon: <Icon icon={PictureInPicture2} size={20} strokeWidth={1.5} />, on: false, act: () => say('参考小窗会单独开一个窗口（演示）') },
    { k: 'settings', label: '设置', icon: <Icon icon={Settings2} size={20} strokeWidth={1.5} />, on: false, act: () => setSettingsOpen(true) },
  ]

  return (
    <div className={`m-app ${reduce ? 'm-reduce' : ''}`} style={{ position: 'relative' }}
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={e => { if (e.currentTarget === e.target) setDragging(false) }}
      onDrop={e => {
        e.preventDefault(); setDragging(false)
        say(`松手即导入：收到 ${e.dataTransfer.files.length || 1} 张（演示）`)
      }}>
      <div className="m-grain" />

      <header className={`m-topbar ${view === 'projects' || scrolled ? 'is-solid' : ''}`}>
        <span className="m-count">697 张收藏 · 112 张视觉参考</span>
        <span className="m-spacer" />
        <div className="m-toplinks">
          {PLATFORMS.filter(p => p !== '本地').map(p => (
            <button key={p} type="button" className={`m-toplink ${platforms.has(p) ? 'is-on' : ''}`} onClick={() => togglePlatform(p)}>{p}</button>
          ))}
          <button type="button" className="m-toplink" onClick={() => say('只看视觉参考（演示）')}>Visual ref</button>
        </div>
        <button type="button" className="m-btn m-ghost" onClick={() => fileRef.current?.click()}>
          <Icon icon={Upload} size={14} /> 导入
        </button>
      </header>
      <input ref={fileRef} type="file" accept="image/*" multiple hidden
        onChange={e => { say(`选到 ${e.target.files?.length ?? 0} 个文件，正式版走本地导入（演示）`); e.target.value = '' }} />

      {view === 'library'
        ? <Library manifest={manifest} onOpen={setDetail} onPending={() => { setKeep(new Set()); setPendingOpen(true) }}
            platforms={platforms} onPlatforms={setPlatforms} reduced={reduce} />
        : <Projects manifest={manifest} onOpen={setDetail} onToast={say} />}

      <nav className="m-dock" aria-label="主导航">
        {dock.map(d => (
          <button key={d.k} type="button" className={d.on ? 'is-on' : ''} onClick={d.act} title={d.label}>
            {d.icon}<span className="m-dock-label">{d.label}</span>
          </button>
        ))}
      </nav>

      <Onboarding open={tutorialOpen} onClose={() => setTutorialOpen(false)} />
      <Detail key={detail ? detail.id : 'closed'} item={detail} onClose={() => setDetail(null)} onToast={say} />
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} onToast={say} reduce={reduce} onReduce={setReduce} />

      <div className={`m-modal ${pendingOpen ? 'is-on' : ''}`}>
        <div className="m-modal-bg" onClick={() => setPendingOpen(false)} />
        <div className="m-modal-panel">
          <div className="m-modal-head">
            <h2>待确认</h2>
            <span>AI 拿不准是不是视觉参考的图 · 12 张</span>
            <span className="m-spacer" />
            <button type="button" className="m-close" style={{ position: 'static' }} aria-label="关闭" onClick={() => setPendingOpen(false)}><Icon icon={X} size={16} /></button>
          </div>
          <div className="m-pending-grid">
            {pendingItems.map((it, i) => {
              const on = keep.has(i)
              return (
                <button type="button" key={`${it.id}-${i}`}
                  className={`m-pending-item ${on ? 'is-keep' : ''}`}
                  onClick={() => {
                    const next = new Set(keep)
                    if (next.has(i)) next.delete(i); else next.add(i)
                    setKeep(next)
                  }}>
                  <img src={it.src} alt="" loading="lazy" />
                  {on && <b>✓</b>}
                </button>
              )
            })}
          </div>
          <div className="m-modal-foot">
            <span>点一下选中，再点取消。保留的会进视觉参考。</span>
            <span className="m-spacer" />
            <button type="button" className="m-btn m-primary" onClick={() => { say(`已保留 ${keep.size} 张（演示）`); setPendingOpen(false) }}>
              保留 {keep.size} 张
            </button>
          </div>
        </div>
      </div>

      <div className={`m-drop ${dragging ? 'is-on' : ''}`}><p>松手，导入到收藏</p></div>
      <div className={`m-toast ${toast ? 'is-on' : ''}`}>{toast}</div>
    </div>
  )
}

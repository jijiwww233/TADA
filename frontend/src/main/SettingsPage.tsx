import { useEffect, useRef, useState } from 'react'
import { FolderPlus } from 'lucide'
import { Icon } from '../lab/Icon'
import type { LabImage } from '../lab/data'
import { sourceLabel } from './meta'
import { PlatformSync } from './PlatformSync'
import { scrollToY } from './smooth'
import { enterDelayMs } from './motion'
import type { WiringApi } from '../api/wiring'
import { useWiringResource } from '../api/useWiring'
import { useMobileLayout } from './useMobileLayout'
import { filterImageFiles, IMAGE_EXTENSIONS } from '../localImport/collect'

const GROUPS = [
  { id: 'api', label: 'API' },
  { id: 'platforms', label: '平台' },
  { id: 'import', label: '本地导入' },
  { id: 'analysis', label: '分析' },
  { id: 'about', label: '关于' },
] as const
type GroupId = typeof GROUPS[number]['id']

const pad2 = (n: number) => String(n).padStart(2, '0')
const topbarHeight = () => parseFloat(getComputedStyle(document.querySelector('.m-app') ?? document.documentElement).getPropertyValue('--m-topbar-height')) || 54

const readKey = (api: WiringApi) => api.getKeyStatus()
const readWatch = (api: WiringApi) => api.getWatchStatus()

/**
 * 设置页（导航 SETTINGS 打开，取代右侧抽屉；抽屉组件还在 SettingsDrawer.tsx）。
 * 排版和详情页、项目页同一套：全衬线、Lusano 式大留白；左栏分组目录钉在视口里，点一下平滑滚到那组（原有 Lenis），
 * 滚到哪组哪组高亮；右侧每组一个小标题加内容。每组的项和抽屉里一一对应，不新增；
 * “素材在库”和“平台”两个数从真实数据算，其余照抽屉原样。出场用原有的 data-reveal。
 */
export function SettingsPage({ items, onToast, reduce, onReduce, api, demo, onDemo, onImport, onSynced }: {
  api: WiringApi
  demo: boolean
  onDemo: (value: boolean) => void
  onImport?: (files: File[]) => void
  onSynced: () => void
  items: LabImage[]
  onToast: (s: string) => void
  reduce: boolean
  onReduce: (v: boolean) => void
}) {
  const mobile = useMobileLayout()
  const key = useWiringResource(api, readKey)
  const watch = useWiringResource(api, readWatch)
  const keyStatus = key.value.status === 'ready' ? key.value.data : null
  const watchStatus = watch.value.status === 'ready' ? watch.value.data : null
  const [limit, setLimit] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState(false)
  const [folder, setFolder] = useState('')
  const [addingFolder, setAddingFolder] = useState(false)
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')
  const changeMode = () => {
    setDraft('')
    setEditing(false)
    setLimit(null)
    setNote('')
    setFolder('')
    setAddingFolder(false)
    onDemo(!demo)
  }
  const updateWatch = async (change: () => Promise<unknown>) => {
    try { await change(); watch.reload() }
    catch { onToast('没保存上：文件夹路径不存在或服务没响应。检查路径后再试') }
  }
  const save = async () => {
    if (saving) return
    setSaving(true)
    setNote('')
    try {
      if (draft.trim()) {
        const result = await api.verifyAndSaveKey(draft)
        setDraft('')
        setEditing(false)
        key.reload()
        setNote(result.restart_required ? '重启后生效' : '已保存')
      }
      if (limit !== null) {
        await api.setDailyLimit(limit)
        setLimit(null)
        watch.reload()
      }
    } catch { setNote('没保存上：API Key 没验证通过，或网络出错。检查 Key 有没有复制完整，再点「保存设置」') }
    finally { setSaving(false) }
  }
  const [active, setActive] = useState<GroupId>('api')
  const groups = useRef<Partial<Record<GroupId, HTMLElement | null>>>({})
  // “本地”是本机导入，不算平台
  const platformCount = new Set(items.map(sourceLabel).filter(s => s !== '本地')).size

  // 当前分组 = 标题已经滚到顶栏下 120px 以内的最后一组；滚到页底时算最后一组
  useEffect(() => {
    let raf = 0
    const pick = () => {
      raf = 0
      const line = topbarHeight() + 120
      let best: GroupId = GROUPS[0].id
      for (const g of GROUPS) { const el = groups.current[g.id]; if (el && el.getBoundingClientRect().top <= line) best = g.id }
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2) best = GROUPS[GROUPS.length - 1].id
      setActive(best)
    }
    const kick = () => { if (!raf) raf = requestAnimationFrame(pick) }
    pick()
    window.addEventListener('scroll', kick, { passive: true })
    return () => { window.removeEventListener('scroll', kick); if (raf) cancelAnimationFrame(raf) }
  }, [])

  const go = (id: GroupId) => {
    const el = groups.current[id]
    if (!el) return
    scrollToY(el.getBoundingClientRect().top + window.scrollY - topbarHeight() - 48)
  }
  const group = (id: GroupId, i: number) => ({
    ref: (el: HTMLElement | null) => { groups.current[id] = el },
    id: `settings-${id}`,
    className: 'm-setp-group',
    'data-reveal': '',
    'aria-labelledby': `settings-${id}-h`,
    style: { '--rv-d': enterDelayMs(i) } as React.CSSProperties,
  })
  const heading = (id: GroupId, i: number) => (
    <h2 className="m-setp-h" id={`settings-${id}-h`}><i>({pad2(i + 1)})</i>{GROUPS[i].label}</h2>
  )

  return (
    <section className="m-page m-setp" aria-label="设置">
      <nav className="m-setp-toc" aria-label="设置分组">
        <div data-reveal>
          <p className="m-setp-title">设置</p>
          <ol>
            {GROUPS.map((g, i) => (
              <li key={g.id}>
                <button type="button" className={active === g.id ? 'is-on' : ''} aria-current={active === g.id ? 'true' : undefined}
                  onClick={() => go(g.id)}>
                  <i>({pad2(i + 1)})</i><span>{g.label}</span>
                </button>
              </li>
            ))}
          </ol>
        </div>
      </nav>

      <div className="m-setp-body m-set">
        {import.meta.env.VITE_PUBLIC_DEMO === '1' && <p role="status">演示版不提供此功能</p>}
        <section {...group('api', 0)}>
          {heading('api', 0)}
          <div className="m-set-row"><label htmlFor="m-api-key-page">API Key</label>
            {keyStatus?.configured && !editing ? <span className="m-setp-key">
              <b>{keyStatus.last4 ? `···· ${keyStatus.last4}` : '已配置'}</b>
              <button type="button" className="m-setp-link" onClick={() => setEditing(true)}>更换</button>
            </span> : <input id="m-api-key-page" className="m-set-input" type="password" placeholder="粘贴"
              autoComplete="off" value={draft} onChange={e => setDraft(e.target.value)} />}
          </div>
          {(note || keyStatus?.restart_required) && <small role="status">{note || '重启后生效'}</small>}
        </section>

        <section {...group('platforms', 1)}>
          {heading('platforms', 1)}
          {demo || import.meta.env.VITE_PUBLIC_DEMO === '1'
            ? <p className="ds-help">平台连接与自动同步可在本机素材库使用。</p>
            : <PlatformSync onSynced={onSynced} />}
        </section>

        <section {...group('import', 2)}>
          {heading('import', 2)}
          {mobile && onImport && <div className="m-set-row">
            {/* 手机：label 直接包着文件框，安卓点了就是相册；accept 用 image/*（只写扩展名时安卓会打开文件管理器而不是相册） */}
            <label className="ds-btn ds-btn--secondary ds-file-btn">选图片
              <input type="file" multiple accept="image/*"
                onChange={event => {
                  const files = filterImageFiles(Array.from(event.currentTarget.files ?? []))
                  event.currentTarget.value = ''
                  if (files.length) onImport(files)
                  else onToast(`选的文件里没有能导入的图片：支持 ${IMAGE_EXTENSIONS.slice(0, 5).join('、')} 等格式`)
                }} />
            </label>
          </div>}
          <div className="m-set-row"><span>自动导入文件夹</span><b>{watchStatus?.folders.length || '未添加'}</b></div>
          {watchStatus?.folders.map(item => <div key={item.id} className="m-set-row">
            <span>{item.path}</span>
            <label><input type="checkbox" checked={item.enabled}
              onChange={e => { const value = e.target.checked; void updateWatch(() => api.setWatchFolderEnabled(item.id, value)) }} />监听</label>
            <label><input type="checkbox" checked={item.include_subfolders}
              onChange={e => { const value = e.target.checked; void updateWatch(() => api.setWatchFolderSubfolders(item.id, value)) }} />包含子文件夹</label>
            <button className="m-setp-link" type="button" onClick={() => void updateWatch(() => api.removeWatchFolder(item.id))}>移除</button>
          </div>)}
          {addingFolder && <form className="m-set-row" onSubmit={e => {
            e.preventDefault()
            if (folder.trim()) void updateWatch(async () => { await api.addWatchFolder(folder.trim()); setFolder(''); setAddingFolder(false) })
          }}>
            <input className="m-set-input" aria-label="文件夹路径" value={folder} onChange={e => setFolder(e.target.value)} />
            <button type="submit" className="m-setp-link">添加</button>
          </form>}
          <button type="button" className="m-set-add" onClick={() => setAddingFolder(!addingFolder)}>
            <Icon icon={FolderPlus} size={13} /> 添加文件夹
          </button>
        </section>

        <section {...group('analysis', 3)}>
          {heading('analysis', 3)}
          <div className="m-set-row"><span>本地分析</span>
            <button type="button" className={`m-switch ${watchStatus?.auto_analysis_enabled ? 'is-on' : ''}`}
              aria-label="自动分析" aria-pressed={watchStatus?.auto_analysis_enabled ?? false}
              disabled={!watchStatus} onClick={() => void updateWatch(() => api.setAutoAnalysis(!watchStatus?.auto_analysis_enabled))} />
          </div>
          <div className="m-set-row">
            <label htmlFor="m-daily-limit-page">每天自动分析上限</label>
            <input id="m-daily-limit-page" className="m-set-input" type="number" min={0} max={500} value={limit ?? watchStatus?.daily_limit ?? ''}
              onChange={e => setLimit(Number(e.target.value))} />
          </div>
        </section>

        <section {...group('about', 4)}>
          {heading('about', 4)}
          <div className="m-set-row"><span>素材在库</span><b>{items.length}</b></div>
          <div className="m-set-row"><span>平台</span><b>{pad2(platformCount)}</b></div>
          <div className="m-set-row">
            <span>减少动效</span>
            <button type="button" className={`m-switch ${reduce ? 'is-on' : ''}`} aria-label="减少动效" aria-pressed={reduce}
              onClick={() => onReduce(!reduce)} />
          </div>
          <div className="m-set-row"><span>演示模式</span>
            <button type="button" className={`m-switch ${demo ? 'is-on' : ''}`} aria-label="演示模式"
              aria-pressed={demo} disabled={saving} onClick={changeMode} />
          </div>
          <div className="m-set-row m-setp-ds">
            <span>设计系统<small className="ds-help" style={{ display: 'block', marginTop: 2 }}>颜色与组件规范</small></span>
            <a className="m-setp-link" href="/design-system">打开 ↗</a>
          </div>
          <button type="button" className="m-set-save" disabled={saving} aria-busy={saving || undefined} onClick={() => void save()}>{saving ? '保存中…' : '保存设置'}</button>
        </section>
      </div>
    </section>
  )
}

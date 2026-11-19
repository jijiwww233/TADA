import { useState } from 'react'
import { FolderPlus, X } from 'lucide'
import { Icon } from '../lab/Icon'

/**
 * 平台状态（抽屉和设置页共用同一份）。
 * 只有真实知道的状态才写；不知道的平台不写状态，界面上只显示平台名。
 */
export const SETTINGS_PLATFORMS: { name: string; status?: string; cool?: boolean }[] = [
  // 待接锁文件：小红书账号确实在冷却（用户确认），这里先写死；后端接口接好以后改成读锁文件里的真实状态
  { name: '小红书', status: '冷却中', cool: true },
  { name: 'Pinterest' },
  { name: '站酷' },
  { name: '花瓣' },
]

/** 设置：从右边滑出的面板（第五轮起导航改开 SettingsPage 独立页，这个抽屉保留不删）。排版照 MONOLITH 那种：编号小标 + 左标签右数值的细行。 */
export function SettingsDrawer({ open, onClose, onToast, reduce, onReduce }: {
  open: boolean; onClose: () => void; onToast: (s: string) => void; reduce: boolean; onReduce: (v: boolean) => void
}) {
  const [limit, setLimit] = useState(50)
  return (
    <aside className={`m-drawer ${open ? 'is-on' : ''}`} aria-hidden={!open}>
      <div className="m-drawer-head">
        <h2 className="m-set-title">设置</h2><span className="m-spacer" />
        <button type="button" className="m-close" style={{ position: 'static' }} aria-label="关闭设置" onClick={onClose}><Icon icon={X} size={16} /></button>
      </div>

      <div className="m-drawer-body m-set">
        <section className="m-set-group">
          <p className="m-set-kicker">Index</p>
          <div className="m-set-row"><span>素材在库</span><b>697</b></div>
          <div className="m-set-row"><span>平台</span><b>04</b></div>
          <div className="m-set-row"><span>本地分析</span><b>开</b></div>
        </section>

        <section className="m-set-group">
          <p className="m-set-kicker">01 — 外观</p>
          <div className="m-set-row">
            <span>减少动效</span>
            <button type="button" className={`m-switch ${reduce ? 'is-on' : ''}`} aria-label="减少动效" onClick={() => onReduce(!reduce)} />
          </div>
          <div className="m-set-row"><span>主题</span><b>米 / 陶土</b></div>
        </section>

        <section className="m-set-group">
          <p className="m-set-kicker">02 — 接口</p>
          <div className="m-set-row">
            <label htmlFor="m-api-key">API Key</label>
            <input id="m-api-key" className="m-set-input" type="password" placeholder="粘贴" />
          </div>
          <div className="m-set-row">
            <label htmlFor="m-daily-limit">每天自动分析上限</label>
            <input id="m-daily-limit" className="m-set-input" type="number" min={0} max={500} value={limit}
              onChange={e => setLimit(Number(e.target.value))} />
          </div>
        </section>

        <section className="m-set-group">
          <p className="m-set-kicker">03 — 导入</p>
          <div className="m-set-row"><span>自动导入文件夹</span><b>未添加</b></div>
          <button type="button" className="m-set-add" onClick={() => onToast('还没接上')}>
            <Icon icon={FolderPlus} size={13} /> 添加文件夹
          </button>
        </section>

        <section className="m-set-group">
          <p className="m-set-kicker">04 — 平台</p>
          {SETTINGS_PLATFORMS.map(p => (
            <div className="m-set-row" key={p.name}>
              <span>{p.name}</span>
              {p.status && <i className={`m-set-tag ${p.cool ? 'is-cool' : ''}`}>{p.status}</i>}
            </div>
          ))}
        </section>

        <button type="button" className="m-set-save" onClick={() => onToast('还没接上')}>保存设置</button>
      </div>
    </aside>
  )
}

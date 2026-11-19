import { useState } from 'react'
import { FolderPlus, X } from 'lucide'
import { Icon } from '../lab/Icon'

const PLATFORMS = [
  { name: '小红书', status: '冷却中', cool: true },
  { name: 'Pinterest', status: '已连接', ok: true },
  { name: '站酷', status: '已连接', ok: true },
  { name: '花瓣', status: '未连接' },
]

/** 设置：右侧滑出的面板，不跳页。 */
export function SettingsDrawer({ open, onClose, onToast, reduce, onReduce }: {
  open: boolean; onClose: () => void; onToast: (s: string) => void; reduce: boolean; onReduce: (v: boolean) => void
}) {
  const [limit, setLimit] = useState(50)
  const [paused, setPaused] = useState(false)
  return (
    <aside className={`m-drawer ${open ? 'is-on' : ''}`} aria-hidden={!open}>
      <div className="m-drawer-head">
        <h2>设置</h2><span className="m-spacer" />
        <button type="button" className="m-close" style={{ position: 'static' }} aria-label="关闭设置" onClick={onClose}><Icon icon={X} size={16} /></button>
      </div>
      <div className="m-drawer-body">
        <div className="m-field">
          <label>外观</label>
          <div className="m-row">
            <div className="m-row-main"><b>减少动效</b><span>关掉滑动和弹出，只保留淡入</span></div>
            <button type="button" className={`m-switch ${reduce ? 'is-on' : ''}`} aria-label="减少动效" onClick={() => onReduce(!reduce)} />
          </div>
        </div>

        <div className="m-field">
          <label>API Key</label>
          <input type="password" defaultValue="sk-demo-0000000000000000" aria-label="API Key" />
          <p className="m-hint">用量计入你自己的账号，数据只保存在本机。验证状态：<span className="m-status is-ok">可用</span></p>
        </div>

        <div className="m-field">
          <label>自动导入文件夹</label>
          <div className="m-row">
            <div className="m-row-main"><b>D:\Pictures\小红书同步</b><span>上次导入 12 分钟前 · 累计 231 张</span></div>
            <button type="button" className={`m-switch ${paused ? '' : 'is-on'}`} aria-label="暂停监听" onClick={() => { setPaused(p => !p); onToast(paused ? '恢复监听（演示）' : '已暂停监听（演示）') }} />
          </div>
          <div className="m-row">
            <div className="m-row-main"><b>D:\CC-Work\给克的\参考\主界面</b><span>上次导入 2 小时前 · 累计 48 张</span></div>
            <button type="button" className="m-switch is-on" aria-label="暂停监听" />
          </div>
          <button type="button" className="m-btn m-ghost" style={{ alignSelf: 'flex-start', marginTop: 4 }} onClick={() => onToast('演示：这里会打开选文件夹对话框')}><Icon icon={FolderPlus} size={15} /> 添加文件夹</button>
          <p className="m-hint">只监听你明确添加的文件夹；新图片会走本地导入，不删除、不移动源文件。</p>
        </div>

        <div className="m-field">
          <label>每天自动分析上限</label>
          <input type="number" min={0} max={500} value={limit} aria-label="每天自动分析上限"
            onChange={e => setLimit(Number(e.target.value))} style={{ width: 120 }} />
          <p className="m-hint">超过上限的图仍然会导入，只是不自动分析；第二天恢复，或在素材库点“现在分析”。</p>
        </div>

        <div className="m-field">
          <label>平台连接</label>
          {PLATFORMS.map(p => (
            <div className="m-row" key={p.name}>
              <div className="m-row-main"><b>{p.name}</b><span>{p.ok ? '可同步' : p.cool ? '同步冷却中，约 2 小时后恢复' : '未连接'}</span></div>
              <span className={`m-status ${p.ok ? 'is-ok' : p.cool ? 'is-cool' : ''}`}>{p.status}</span>
            </div>
          ))}
        </div>

        <button type="button" className="m-btn m-primary" onClick={() => onToast('设置已保存（演示）')}>保存设置</button>
      </div>
    </aside>
  )
}

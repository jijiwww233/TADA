import { FolderClosed, GalleryVerticalEnd, Palette, Search, Tag, X } from 'lucide'
import { Icon } from '../lab/Icon'

/** 进入主界面的教程：实心卡片，暖色头图，横向四步，底部两个按钮，主按钮用原站点的陶土橙。 */
export function Onboarding({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <div className={`m-onboard ${open ? 'is-on' : ''}`} role="dialog" aria-modal="true" aria-label="使用教程">
      <div className="m-onboard-bg" onClick={onClose} />
      <div className="m-onboard-card">
        <button type="button" className="m-onboard-close" aria-label="关闭教程" onClick={onClose}><Icon icon={X} size={16} /></button>
        <div className="m-onboard-hero">
          <div className="m-onboard-tiles">
            <span className="m-onboard-tile"><Icon icon={Search} size={22} strokeWidth={1.5} /></span>
            <span className="m-onboard-tile"><Icon icon={Palette} size={22} strokeWidth={1.5} /></span>
          </div>
        </div>
        <div className="m-onboard-body">
          <h2 className="m-onboard-title">先看一分钟，再开始找。</h2>
          <p className="m-onboard-sub">四个动作，覆盖平时最常用的用法。</p>
          <div className="m-onboard-steps">
            <div className="m-onboard-step">
              <span className="m-onboard-icon"><Icon icon={Search} size={20} strokeWidth={1.5} /></span>
              <strong>找图</strong>
              <span>描述你记得的样子，或转动色环按颜色找。</span>
            </div>
            <div className="m-onboard-step">
              <span className="m-onboard-icon"><Icon icon={GalleryVerticalEnd} size={20} strokeWidth={1.5} /></span>
              <strong>要参考</strong>
              <span>换成排版 / 配色 / 风格三列，每张图下写着学它什么。</span>
            </div>
            <div className="m-onboard-step">
              <span className="m-onboard-icon"><Icon icon={Tag} size={20} strokeWidth={1.5} /></span>
              <strong>收藏</strong>
              <span>图片拖进页面就导入；点开大图看信息面板，标签随手改。</span>
            </div>
            <div className="m-onboard-step">
              <span className="m-onboard-icon"><Icon icon={FolderClosed} size={20} strokeWidth={1.5} /></span>
              <strong>项目</strong>
              <span>做项目时把图钉进去，从项目页打开参考小窗。</span>
            </div>
          </div>
          <div className="m-onboard-foot">
            <button type="button" className="m-onboard-ghost" onClick={onClose}>跳过</button>
            <button type="button" className="m-onboard-cta" onClick={onClose}>开始找图</button>
          </div>
        </div>
      </div>
    </div>
  )
}

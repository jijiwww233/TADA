import { useState, type CSSProperties, type ReactNode } from 'react'
import { ArrowUp, KeyRound, Search } from 'lucide'
import { Icon } from '../lab/Icon'
import { ImportResult } from './ImportResult'
import './main.css'
import './app-header.css'
import './library-wall.css'
import './mobile.css'
import './refine.css'
import '../styles/design-system.css'
import '../styles/design-system-page.css'
import { installMotionVars } from './motion'

// 和主界面一样把 --mo-* 动效变量装到 <html> 上（老样式里还有地方读它们）
installMotionVars()

/** 颜色令牌：名字、用途、在哪用 */
const COLORS: { group: string; items: { name: string; use: string; text?: boolean; ratio?: string }[] }[] = [
  { group: '三种主色（全站只有这三种颜色 + 黑色系文字）', items: [
    { name: '--brand-cream', use: '米白 #fafaf8：页面底色和卡片里的输入层' },
    { name: '--brand-clay', use: '陶土橙 oklch(0.56 0.112 42) ≈ #AA5C3D：主要按钮、强调、当前项小圆点（最初版的陶土橙）' },
    { name: '--brand-tan', use: '浅棕 #DFD8D3：次要按钮、卡片染色和分隔细线' },
  ] },
  { group: '背景', items: [
    { name: '--color-bg-page', use: '页面底色：米白' },
    { name: '--color-bg-surface', use: '卡片和弹窗：15% 浅棕叠在米白上' },
    { name: '--color-bg-input', use: '卡片内的输入框：米白' },
    { name: '--color-bg-subtle', use: '浅棕兑米白：说明块、报错条、项目台面' },
    { name: '--color-bg-inverse', use: '提示条：接近黑色' },
  ] },
  { group: '文字（黑色系中性色）', items: [
    { name: '--color-text-primary', use: '正文、标题：接近黑色', text: true, ratio: '≈ 14 : 1' },
    { name: '--color-text-secondary', use: '说明、来源、次要信息', text: true, ratio: '≈ 8 : 1' },
    { name: '--color-text-tertiary', use: '最淡的小字、计数、未连接', text: true, ratio: '≈ 5.7 : 1' },
    { name: '--color-text-placeholder', use: '输入框里的提示文字', text: true, ratio: '≈ 4.8 : 1' },
    { name: '--color-text-accent', use: '悬停的幽灵按钮、链接（陶土橙，只放在米白上）', text: true, ratio: '≈ 4.7 : 1' },
  ] },
  { group: '操作', items: [
    { name: '--color-action-primary', use: '主要按钮：陶土橙底、白字 ≈ 4.87 : 1' },
    { name: '--color-action-primary-hover', use: '主要按钮 · 悬停（最初版的 clay-hover）' },
    { name: '--color-action-secondary-bg', use: '次要按钮：浅棕底、黑字，没有框' },
    { name: '--color-action-secondary-hover', use: '次要按钮 · 悬停' },
    { name: '--color-action-disabled-bg', use: '禁用按钮底色' },
  ] },
  { group: '线与状态', items: [
    { name: '--color-border-default', use: '卡片、弹窗、输入框的极细浅棕线' },
    { name: '--color-border-subtle', use: '列表里的分隔细线' },
    { name: '--color-status-success', use: '已连接、导入完成：浅棕小圆点（不用绿色）' },
    { name: '--color-status-info', use: '「分析中」、当前项、有失败：陶土橙小圆点' },
  ] },
  { group: '数据（收藏活动，从少到多：陶土橙叠在米白上）', items: [
    { name: '--color-data-0', use: '没有收藏' },
    { name: '--color-data-1', use: '少（25%）' },
    { name: '--color-data-2', use: '一些（50%）' },
    { name: '--color-data-3', use: '较多（75%）' },
    { name: '--color-data-4', use: '最多（陶土橙本色）' },
  ] },
]

const TYPE = [
  { name: '--font-size-display', px: 48, use: '衬线斜体大标题（搜索行）', sample: 'Tada! I remember it…', italic: true },
  { name: '--font-size-title', px: 36, use: '页面标题、详情标题', sample: '原来你收藏了这么多' },
  { name: '--font-size-title-sm', px: 24, use: '手机上的页面标题', sample: '收藏活动' },
  { name: '--font-size-subtitle', px: 20, use: '小节标题', sample: '每日灵感' },
  { name: '--font-size-body-lg', px: 16, use: '品牌字、导入结果', sample: '已收藏 3 张 · 1 张已在库里' },
  { name: '--font-size-body', px: 14, use: '正文、按钮、卡片标题', sample: '淡黄色的牛仔海报' },
  { name: '--font-size-caption', px: 12, use: '来源、计数、辅助说明', sample: '小红书 · 10月12日' },
]

const SPACE = [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 16, 24]
const RADII = [['--radius-button', '8px · 文字按钮'], ['--radius-icon-button', '圆形 · 纯图标按钮'], ['--radius-card', '16px · 卡片和弹窗'], ['--radius-input', '8px · 输入框']]

/** 用词对照：同一个动作全站只用一个说法 */
export const WORDING: { action: string; word: string; before: string }[] = [
  { action: '把图片放进素材库', word: '导入', before: '选择图片 · 上传 · 收藏（拖图导入）· 选择图片导入' },
  { action: '打开相册 / 文件选图', word: '选图片', before: '选择图片 · 选择图片导入' },
  { action: '导入后的结果', word: '已收藏 N 张 · N 张已在库里 · N 张失败：原因', before: '已导入 N 张 · N 张已收藏 · N 张未导入' },
  { action: '看这次导入的图', word: '查看刚导入的', before: '（原来没有）' },
  { action: '离开搜索结果', word: '返回全部收藏', before: '清除 · 返回全部' },
  { action: '清掉筛选条件', word: '清除筛选', before: '清除' },
  { action: '提交搜索', word: '搜索', before: '发送' },
  { action: '新建一个项目', word: '新建 / 新建项目', before: '创建 · 新建项目' },
  { action: '把选中的参考存成项目', word: '存为新项目', before: '存为项目 · 存成一个项目 · 保存项目' },
  { action: '把图放进已有项目', word: '加入项目', before: '加入项目（不变）' },
  { action: '用颜色找图', word: '按颜色找', before: '找同色 · 按颜色 · 按颜色找图 · 颜色查找' },
  { action: '把原图存到本机', word: '下载原图（图片详情页照最初版，写「保存 ↓」）', before: '保存 ↓' },
  { action: '置顶的对照画板', word: '参考画板', before: '小窗 · 参考小窗' },
  { action: '关掉弹窗 / 面板', word: '关闭', before: '关闭 · 关闭导入' },
  { action: '详情里「收藏」一栏没有数据', word: '未记录收藏夹（下面一行小字说明原因）', before: '收藏：未知' },
]

function Section({ no, title, note, children }: { no: number; title: string; note?: string; children: ReactNode }) {
  return (
    <section className="dsp-section" aria-labelledby={`dsp-${no}`}>
      <header className="dsp-section-head">
        <i>({String(no).padStart(2, '0')})</i>
        <h2 id={`dsp-${no}`}>{title}</h2>
        {note && <p>{note}</p>}
      </header>
      <div className="dsp-section-body">{children}</div>
    </section>
  )
}

/** 设计系统页里的“样式示例”不是真控件：明确标成静态展示，避免看起来能点、点了没反应。 */
function StaticDemo({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <figure className="dsp-demo">
      <figcaption><span>静态展示</span>{label}</figcaption>
      <div className="dsp-demo-canvas" aria-hidden="true" inert>{children}</div>
    </figure>
  )
}

/** /design-system：这个网站的样式说明书。和网站同一套纸、墨、衬线。 */
export default function DesignSystem() {
  const [on, setOn] = useState(true)
  const [mode, setMode] = useState<'find' | 'ref'>('find')
  const [sample, setSample] = useState('')
  return (
    <div className="m-app m-refine dsp">
      <header className="dsp-top">
        <a className="m-brand dsp-brand" href="/main">TADA</a>
        <a className="ds-btn ds-btn--ghost ds-btn--sm" href="/main">← 回到素材库</a>
      </header>

      <div className="dsp-hero">
        <p className="dsp-kicker">Design System · 设计系统</p>
        <h1 className="dsp-title">米白、<em>陶土橙</em>、浅棕，和黑色的字。</h1>
        <p className="dsp-lead">这个网站的样式说明书。全站只有三种颜色：米白做底、陶土橙做主要按钮和强调、浅棕做次要按钮和菜单，
          文字使用黑色系。卡片和弹窗用淡浅棕分层，常用操作保持扁平。</p>
      </div>

      <Section no={1} title="颜色" note="米白、陶土橙和浅棕搭配黑色系文字。搜索大字占位沿用最初版浅灰，示例词和说明使用更易读的灰。">
        {COLORS.map(group => (
          <div className="dsp-color-group" key={group.group}>
            <h3>{group.group}</h3>
            <ul className="dsp-swatches">
              {group.items.map(item => (
                <li key={item.name}>
                  <span className="dsp-swatch" style={{ background: `var(${item.name})` } as CSSProperties}>
                    {item.text && <b style={{ color: `var(${item.name})` }}>Aa 字</b>}
                  </span>
                  <code>{item.name}</code>
                  <span>{item.use}</span>
                  {item.ratio && <small>对比度 {item.ratio}</small>}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </Section>

      <Section no={2} title="字号" note="7 级。中文永远正体，英文和数字可以用 Newsreader 斜体。">
        <ul className="dsp-type">
          {TYPE.map(t => (
            <li key={t.name}>
              <span className="dsp-type-meta"><code>{t.name}</code><small>{t.px}px · {t.use}</small></span>
              <span className="dsp-type-sample" style={{ fontSize: `var(${t.name})`, fontStyle: t.italic ? 'italic' : undefined, fontWeight: t.italic ? 300 : undefined }}>{t.sample}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section no={3} title="间距、圆角、阴影" note="文字按钮和输入框 8px，表单卡片与弹窗 16px，纯图标按钮是圆形。素材库切换和筛选展开时用半透明底加模糊。">
        <div className="dsp-grid-2">
          <ul className="dsp-space">
            {SPACE.map(n => (
              <li key={n}><code>--space-{n}</code><i style={{ width: `var(--space-${n})` }} /><small>{n * 4}px</small></li>
            ))}
          </ul>
          <ul className="dsp-radii">
            {RADII.map(([name, px]) => (
              <li key={name}><span style={{ borderRadius: `var(${name})` }} /><code>{name}</code><small>{px}</small></li>
            ))}
          </ul>
        </div>
        <ul className="dsp-shadows">
          <li><code>卡片 / 弹窗</code><small>浅棕叠在米白上，16px 圆角，扁平</small></li>
          <li><code>按钮</code><small>普通和大号两档，同档等高</small></li>
        </ul>
        <p className="ds-help">动效：短反馈（悬停、按下、选中）一律 <code>--motion-duration-fast</code> 150ms + <code>--motion-ease-standard</code>；
          浮层用 240ms，页面切换和出场用 <code>--motion-ease-emphasized</code>。</p>
      </Section>

      <Section no={4} title="按钮" note="文字按钮用 8px 圆角、约 500 字重。主要按钮陶土橙底白字，次要按钮浅棕底黑字，幽灵按钮仅文字。三类按钮都采用扁平样式。">
        <StaticDemo label="按钮、菜单、下拉和详情按钮的尺寸/状态，不会触发操作">
        <div className="dsp-table-wrap">
          <table className="dsp-buttons">
            <thead><tr><th scope="col">种类</th><th scope="col">正常</th><th scope="col">悬停</th><th scope="col">按下</th><th scope="col">禁用</th><th scope="col">加载中</th></tr></thead>
            <tbody>
              {([['primary', '主要', '导入'], ['secondary', '次要', '换个说法'], ['ghost', '幽灵', '关闭']] as const).map(([kind, label, text]) => (
                <tr key={kind}>
                  <th scope="row">{label}<small>{kind === 'primary' ? '这一步最该点的' : kind === 'secondary' ? '可选的下一步' : '取消、关闭、次要链接'}</small></th>
                  <td><button type="button" className={`ds-btn ds-btn--${kind}`}>{text}</button></td>
                  <td><button type="button" className={`ds-btn ds-btn--${kind} is-hover`} tabIndex={-1}>{text}</button></td>
                  <td><button type="button" className={`ds-btn ds-btn--${kind} is-active`} tabIndex={-1}>{text}</button></td>
                  <td><button type="button" className={`ds-btn ds-btn--${kind}`} disabled>{text}</button></td>
                  <td><button type="button" className={`ds-btn ds-btn--${kind}`} aria-busy="true" tabIndex={-1}>{text}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="dsp-row">
          <span className="dsp-label">纯图标 · 圆形</span>
          <button type="button" className="ds-btn ds-btn--primary ds-icon-btn" aria-label="搜索图标示例"><Icon icon={ArrowUp} size={18} /></button>
          <button type="button" className="ds-btn ds-btn--secondary ds-icon-btn" aria-label="钥匙图标示例"><Icon icon={KeyRound} size={18} /></button>
          <button type="button" className="ds-btn ds-btn--primary ds-btn--lg">主要操作（大）</button>
        </div>
        <div className="dsp-row">
          <span className="dsp-label">顶栏页面切换 · 一整块毛玻璃，分组用浅线，当前页用陶土橙圆点</span>
          <span className="m-gm m-gm-word"><button type="button" className="m-gm-word-head">筛选</button></span>
          <span className="m-gm m-gm-word is-open"><button type="button" className="m-gm-word-head">排序（展开时）</button></span>
          <select className="ds-select" aria-label="下拉示例" defaultValue=""><option value="">选择项目</option><option>夏日音乐节</option></select>
        </div>
        <div className="dsp-row">
          <span className="dsp-label">图片详情页</span>
          <div className="m-ld dsp-ld">
            <div className="m-ld-finds">
              <button type="button" className="m-ld-similar">找相似</button>
              <button type="button" className="m-ld-similar m-ld-samecolor">
                <span className="m-ld-samecolor-dot" style={{ background: 'var(--color-data-3)' }} aria-hidden="true" />按颜色找
              </button>
            </div>
          </div>
        </div>
        </StaticDemo>
      </Section>

      <Section no={5} title="标签与状态" note="状态不画框，只用小圆点 + 文字：陶土橙点 = 当前 / 正在进行，浅棕点 = 已连接 / 已完成，没有点的浅色字 = 未连接。">
        <div className="dsp-row">
          <span className="ds-tag">海报</span>
          <span className="ds-tag is-on">已选中</span>
          <span className="ds-analyzing">分析中</span>
        </div>
        <div className="dsp-row dsp-states">
          <span className="dsp-state is-current"><i />当前 · 未连接</span>
          <span className="dsp-state is-connected"><i />已连接</span>
          <span className="dsp-state">未连接</span>
        </div>
      </Section>

      <Section no={6} title="搜索栏" note="浅灰斜体是输入提示，底下是浅棕细线。示例和模式说明退到小字，陶土橙光标提示输入状态。">
        <div className="m-sl-anchor dsp-search">
          <div className="m-searchline m-sl-v2">
            <div className="m-sl-in">
              <div className="m-sl-row">
                <span className="m-sl-icon"><Icon icon={Search} size={22} aria-hidden="true" /></span>
                <div className="m-sl-field">
                  <input className="m-sl-input" placeholder="Tada! I remember it…" aria-label="搜索栏示例" value={sample} onChange={event => setSample(event.target.value)} />
                </div>
                <div className="m-sl-tools">
                  <div className="m-sl-modes" role="group" aria-label="搜索方式">
                    <button type="button" className={`m-sl-mode${mode === 'find' ? ' is-on' : ''}`} aria-pressed={mode === 'find'} onClick={() => setMode('find')}>找图</button>
                    <span className="m-sl-dot" aria-hidden="true">·</span>
                    <button type="button" className={`m-sl-mode${mode === 'ref' ? ' is-on' : ''}`} aria-pressed={mode === 'ref'} onClick={() => setMode('ref')}>要参考</button>
                  </div>
                </div>
              </div>
              <div className="m-sl-rule" />
              <div className="m-sl-sub">
                <div className="m-sl-tries">
                  <span className="m-sl-tries-lead">试试：</span>
                  <button type="button" className="m-sl-try" onClick={() => setSample('淡黄色的牛仔海报')}>淡黄色的牛仔海报</button>
                  <span className="m-sl-try-sep" aria-hidden="true">·</span>
                  <button type="button" className="m-sl-try" onClick={() => setSample('黑白粗颗粒')}>黑白粗颗粒</button>
                  <span className="m-sl-try-sep" aria-hidden="true">·</span>
                  <button type="button" className="m-sl-try" onClick={() => setSample('手写字的菜单')}>手写字的菜单</button>
                </div>
                <p className="m-sl-modehint"><b>{mode === 'find' ? '找图' : '要参考'}</b>：{mode === 'find' ? '描述画面或颜色，找回收藏过的图' : '按排版、配色、风格找参考'}</p>
              </div>
            </div>
          </div>
        </div>
      </Section>

      <Section no={7} title="开关与输入框" note="输入框为米白底、8px 圆角，放在淡浅棕卡片里形成两层。">
        <div className="dsp-row">
          <button type="button" className={`m-switch ${on ? 'is-on' : ''}`} aria-label="开关示例" aria-pressed={on} onClick={() => setOn(!on)} />
          <span className="dsp-label">{on ? '开' : '关'}</span>
          <button type="button" className="m-switch" aria-label="禁用的开关" disabled />
          <span className="dsp-label">禁用</span>
        </div>
        <StaticDemo label="Key 卡片">
        <div className="dsp-field-card">
          <label htmlFor="dsp-key-example">API Key</label>
          <input id="dsp-key-example" className="ds-input" placeholder="粘贴示例 Key" readOnly />
          <button type="button" className="ds-btn ds-btn--primary">验证</button>
        </div>
        </StaticDemo>
      </Section>

      <Section no={8} title="卡片" note="弹窗和表单卡片用淡浅棕底、大圆角。素材墙上的图片用直角，图注直接排在页面上。">
        <StaticDemo label="素材卡片">
        <div className="dsp-cards">
          {[
            { title: '淡黄色的牛仔海报', src: '小红书', busy: false, tint: 'var(--color-bg-subtle)' },
            { title: '正在分析的参考', src: '小红书', busy: true, tint: 'var(--brand-tan)' },
            { title: '手写字的菜单', src: 'Pinterest', busy: false, tint: 'var(--color-data-1)' },
          ].map((card, i) => (
            <button type="button" className="m-tile" key={i}>
              <span className="dsp-card-img" style={{ background: card.tint }} />
              <span className="m-tile-cap">
                <span className={`m-tile-title${card.title ? '' : ' is-empty'}`}>{card.title || '正在读图…'}</span>
                <span className="m-tile-src"><span>{card.src}</span>{card.busy && <span className="ds-analyzing">分析中</span>}</span>
              </span>
            </button>
          ))}
        </div>
        </StaticDemo>
      </Section>

      <Section no={9} title="导入结果提示" note="导入完不自动消失：一行汇总 + 每种失败的原因和改法 + 「查看刚导入的」。">
        <StaticDemo label="导入完成的两种状态">
        <div className="dsp-static">
          <ImportResult status="done" cancelled={false} error={null} onView={() => {}} onClose={() => {}} onCancel={() => {}}
            progress={{ total: 5, processed: 5, added: 3, duplicates: 1, failed: [{ name: 'IMG_2041.HEIC', reason: '文件超过 50MB' }] }} />
          <ImportResult status="importing" cancelled={false} error={null} onView={() => {}} onClose={() => {}} onCancel={() => {}}
            progress={{ total: 12, processed: 8, added: 7, duplicates: 1, failed: [] }} />
        </div>
        </StaticDemo>
      </Section>

      <Section no={10} title="空结果与报错" note="报错说清楚：哪里错了、为什么、怎么改。">
        <StaticDemo label="空结果与报错">
        <div className="ds-empty">
          <p className="ds-empty-title">没找到「蓝色的猫」</p>
          <button type="button" className="ds-btn ds-btn--primary">返回全部收藏</button>
        </div>
        <p className="ds-alert">读不到素材库：后台服务没响应。确认服务已启动、网络正常后，点「重试」。
          <button type="button" className="ds-btn ds-btn--secondary ds-btn--sm">重试</button></p>
        </StaticDemo>
      </Section>

      <Section no={11} title="用词对照表" note="同一个意思只说一次。一个按钮下面最多一行辅助小字。能用三个字说清楚的，不写一句话。">
        <div className="dsp-table-wrap">
          <table className="dsp-words">
            <thead><tr><th scope="col">动作</th><th scope="col">统一叫法</th><th scope="col">原来的叫法</th></tr></thead>
            <tbody>
              {WORDING.map(row => (
                <tr key={row.action}><td>{row.action}</td><td><b>{row.word}</b></td><td>{row.before}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <footer className="dsp-foot">
        <a className="ds-btn ds-btn--primary" href="/main">回到素材库</a>
      </footer>
    </div>
  )
}

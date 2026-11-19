import { REFINE } from './refine'

const STEPS = [
  ['找图', '描述画面或颜色，找回收藏过的图。'],
  ['要参考', '按排版、配色、风格找参考。'],
  ['导入', '点「导入 +」或把图拖进窗口，点开图片改标签。'],
  ['项目', REFINE ? '把图加进项目，打开参考画板对照。' : '把图加进项目，用小窗查看。'],
] as const

export function Onboarding() {
  return (
    <ol className="m-help-steps">
      {STEPS.map(([title, description], index) => (
        <li className="m-gm-row m-help-row" key={title} style={{ '--i': index } as React.CSSProperties}>
          <span className="m-gm-mask">
            <span className="m-gm-text">
              <span><i>(0{index + 1})</i> {title}</span>
              <span className="m-help-description">{description}</span>
            </span>
          </span>
        </li>
      ))}
    </ol>
  )
}

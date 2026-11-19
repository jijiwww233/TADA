import type { DemoMeta } from './Demo'
import type { DemoKey } from './Lab'

export type DemoEntry = { component: DemoKey; meta: Omit<DemoMeta, 'id'> & { id: string }; props?: Record<string, unknown>; tall?: boolean; bleed?: boolean }
type Group = { id: string; short: string; title: string; target: string; demos: DemoEntry[] }

const ACE = 'Aceternity License（可用于自己的成品/商用，不可单独再分发源码或做模板售卖）'
const RB = 'MIT + Commons Clause（可用于产品含商用，不可单独转售/再分发组件本身）'
const MUI = 'MIT'
const OWN = '自写（无第三方代码）'
const OWN_GL = '自写（原生 WebGL2，无第三方代码）'
const MORPH = 'morphicons MIT · lucide 图标数据 ISC'

export const GROUPS: Group[] = [
  {
    id: 'scatter', short: '第一屏', title: '连续演示：展示 → 填钥匙 → 连平台',
    target: '勾选演示模式自动播放，或从第二页开始；同一页面和同一个胶囊，不接真后端。默认上图下字，按用户中文/英文两张阶梯稿排标题：I know / 我明明 / 收藏过 / I saved it；复用克最新的半调出场与出场后近焦；首页文字统一用旧纸 B / 油墨变量。墙采用纯 B 的 7 / 6 / 7 错列，收集沿用 A，固定第 2 版“接住下沉”。',
    demos: [
      {
        component: 'scatter', bleed: true,
        meta: {
          id: 'scatter', name: '同一胶囊贯穿三页 · 演示模式 / 从第二页开始', source: '本仓库 · scatter/ + intro/（本地演示状态机）', url: 'https://developer.mozilla.org/docs/Web/API/WebGL2RenderingContext',
          license: OWN_GL, deps: '无第三方依赖；胶囊另计',
          gap: '首屏为 9bc3f96 的原 16 张散卡，数量、位置和大小不变。标题仅在旧版大小和位置内采用楼梯错位；中文为主默认，可切英文为主，出场后才启用近焦。仅下滑后补齐 20 张，并过渡到纯 B 的墙坐标、116×155 卡片与 58/34 间距；窄屏保留错列并折为六排。依次下落路径与 55ms 错开沿用 A，胶囊与文字出场组件内部不改。',
        },
      },
      {
        component: 'hero', tall: true,
        meta: {
          id: 'hero', name: 'Hero Parallax（不动）', source: 'ui.aceternity.com/components/hero-parallax', url: 'https://ui.aceternity.com/components/hero-parallax',
          license: ACE, deps: 'motion',
          gap: '动效没改；图片去掉了番茄和人脸，其余照旧。',
        },
      },
    ],
  },
  {
    id: 'ring', short: '一圈图片', title: '一圈图片慢慢转（不动）',
    target: '绑定完成后，M 张视觉参考绕成一圈慢慢转，转完收进胶囊。',
    demos: [
      {
        component: 'circular',
        meta: {
          id: 'circular', name: 'Circular Gallery', source: 'reactbits.dev/components/circular-gallery', url: 'https://reactbits.dev/components/circular-gallery',
          license: RB, deps: '无额外依赖（原生 WebGL2）',
          gap: '未改；图片去掉了番茄和人脸。',
        },
      },
      {
        component: 'cssring',
        meta: {
          id: 'cssring', name: 'CSS 3D 环（手搓对照）', source: '本仓库 · 纯 CSS', url: 'https://developer.mozilla.org/docs/Web/CSS/transform-function/rotateY',
          license: '自写', deps: '无',
          gap: '未改；图片去掉了番茄和人脸。',
        },
      },
    ],
  },
  {
    id: 'type', short: '文字出场', title: '文字出场：半调出场 → 近焦 + Blur Fade',
    target: '出场用第四轮的半调（墨滴）；出场结束后，鼠标停在大标题上时有一片圆形雾片跟着走。只用在大标题上；触屏或减少动效不启用。',
    demos: [
      {
        component: 'reveal',
        meta: {
          id: 'reveal', name: '大标题 · 半调出场（第四轮）→ 近焦雾片', source: '本仓库 · reveal.tsx（HalftoneText + Fog）', url: 'https://developer.mozilla.org/docs/Web/CSS/mask-image',
          license: OWN, deps: '无（出场：一个 SVG 滤镜；近焦：一个 backdrop-filter 圆片）',
          gap: '出场恢复成第四轮原样（2.1 秒），只去掉了淡入时透明度到 1 那一帧的合成闪动；闪烁的主因是每帧改模糊半径，一修就变样，所以没修。近焦：直径约 1.5 个字宽的圆片，backdrop-filter: blur(7px)，径向渐变遮罩做柔边，跟手带一点弹簧延迟，进出标题 0.2 秒淡入淡出。',
        },
      },
      {
        component: 'blurfade',
        meta: {
          id: 'blurfade', name: 'Blur Fade · 卡片和面板出现（放慢 1.3 倍）', source: 'magicui.design/docs/components/blur-fade', url: 'https://magicui.design/docs/components/blur-fade',
          license: MUI, deps: 'motion',
          gap: '同第三轮：每张 0.65 秒，错开 91ms，面板 0.85 秒出现；色环面板淡入 286ms。',
        },
      },
    ],
  },
  {
    id: 'numbers', short: '数字', title: '数字出场：只用半调',
    target: '胶囊里“N 张收藏 → M 张视觉参考”。只用半调出场，不加近焦。tabular-nums，不抖宽度。',
    demos: [
      {
        component: 'numbers',
        meta: {
          id: 'numbers-halftone', name: '数字 · 半调', source: '本仓库 · reveal.tsx（HalftoneNumber）', url: 'https://developer.mozilla.org/docs/Web/SVG/Element/feComposite',
          license: OWN, deps: '无（和文字同一个半调滤镜，网点 3px）',
          gap: '从 0 数上去（1.8 秒，先快后慢），数得越快网点晕得越开；落定后 0.35 秒收紧成清楚的数字（第四轮原样）。数字 697 / 112 是演示库的真实统计。',
        },
      },
    ],
  },
  {
    id: 'colors', short: '文字颜色', title: '文字颜色：一套变量统一管理（定稿：油墨 + 次重点 B 旧纸）',
    target: '标题、正文 / 副标题、小字和占位符、胶囊里的字、陶土橙重点色、次重点色，全用同一套变量。文字色从用户的图里取（旧照片的深棕、油墨色），同一个色相、不同深浅；不要纯黑，不要偏红。',
    demos: [
      {
        component: 'colors',
        meta: {
          id: 'text-colors', name: '文字色 · 油墨 + 次重点 B 旧纸（定稿）', source: '本仓库 · lab.css（--wm-* 变量）', url: 'https://developer.mozilla.org/docs/Web/CSS/color_value/oklch',
          license: OWN, deps: '无（CSS 变量）',
          gap: '定稿：标题 --wm-title oklch(0.255 0.016 75)、正文/副标题 --wm-body oklch(0.42 0.014 75)、小字/占位符 --wm-muted oklch(0.62 0.011 78)；胶囊里输入的字 --wm-input = 标题色。重点色 --wm-accent 陶土橙；次重点 --wm-soft-* 用 B 旧纸。大标题（含第一屏）已改用这套变量。',
        },
      },
    ],
  },
  {
    id: 'glass', short: '胶囊', title: '胶囊：透明本体 + 陶土橙发送 + 次重点 3 个候选',
    target: '本体透明、发送按钮陶土橙不变。次重点（“找图 / 要参考”的选中项）饱和度明显降下来，色相往黄橙偏、不偏红，从芥末色、旧纸色里取。背后不垫任何颜色。小图标统一用候选 3（一叠）。',
    demos: [
      {
        component: 'capsule',
        meta: {
          id: 'capsule', name: '胶囊 · 次重点 B 旧纸', source: '本仓库 · Capsule.tsx + lab.css；图标变形 morphicons', url: 'https://www.morphicons.com',
          license: MORPH, deps: 'motion（滑块）+ morphicons + lucide 图标数据',
          gap: '次重点定为 B 旧纸：oklch(0.935 0.022 84) 底 + oklch(0.42 0.035 72) 字。流动色、白灰玻璃和它们的开关都删了。“找参考”图标只留候选 3（一叠，lucide GalleryVerticalEnd），搜索图标会变形成它。',
        },
      },
    ],
  },
  {
    id: 'fonts', short: '字体', title: '字体：第一套 + 第四轮排版规则',
    target: '思源宋体 + Newsreader（不换字体）。超大衬线标题当主角：细字重、行距压紧；“明明”粗、其余细；英文小标题小一级。按钮、输入框、正文仍是黑体。可切换英文当主标题。',
    demos: [
      {
        component: 'fonts',
        meta: {
          id: 'fonts', name: '套 1 · 思源宋体 Light / Bold + Newsreader Light', source: 'fonts.google.com · Noto Serif SC / Newsreader', url: 'https://github.com/google/fonts',
          license: 'SIL OFL 1.1（全部字体，可商用、可自托管）',
          deps: '标题：思源宋体子集（300–700 可变，只含标题用字）5.9 KB + Newsreader（300–600，保留光学尺寸轴）112.7 KB；界面：Inter 62.3 KB + 思源黑体子集；等宽小字 IBM Plex Mono 14.2 KB',
          gap: 'Newsreader 这轮保留了光学尺寸轴，大字号自动用展示版（笔画更细、对比更高），所以文件从 37.7 KB 变成 112.7 KB；Source Serif 4 这轮去掉了。字色仍是暖黑 oklch(0.25 0.02 55)。',
        },
      },
    ],
  },
]

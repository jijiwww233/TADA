import { REFINE } from '../../main/refine'

/** All copy for the three-stage demo lives here, including debug controls and accessible labels. */
const BASE_COPY = {
  homeIntro: '给设计学生的灵感收藏夹。整理跨平台收藏，挑出用得上的设计参考。',
  keyTitle: { prefix: '先给它一把', emphasis: '钥匙', second: '打开这扇门' },
  keyLabel: '粘贴你的 API key', keyPlaceholder: 'sk-', keyAria: 'API key（演示，不联网）',
  keyError: '这把钥匙暂时没能验证，请检查后再试。', emptyKey: '先粘贴一把钥匙，或选择稍后再填。',
  verifyAction: '验证钥匙', verify: '验证', verifying: '验证中', keySkipped: '之后再填', or: '或',
  showKey: '显示 API key', hideKey: '隐藏 API key', change: '更换', continue: '继续', advanced: '高级设置',
  endpoint: '接口地址', endpointAria: '接口地址（演示）', endpointPlaceholder: 'https://api.example.invalid/v1',
  model: '模型名', modelAria: '模型名（演示）', modelPlaceholder: '填写你使用的模型', advancedNote: '仅演示表单，不发送请求。',
  progressLabel: '引导进度',
  progressSteps: ['配置 API Key', '连接收藏来源', '查看整理结果'],
  platformHint: '你都收藏在哪？', platformAria: '连平台', sourceAria: '收藏来源',
  waiting: '等你在弹出的窗口里登录……', localWaiting: '把文件夹拖进来', connect: '连接', skip: '跳过',
  skipped: '已跳过', cooldown: '冷却中', all: '下一步', ready: '进入主界面', readyTitle: '原来你收藏了这么多', readyPrefix: '原来你', readyEmphasis: '收藏', readySuffix: '了这么多',
  status: '已连接状态', connected: '已连接', collections: '张收藏', references: '张视觉参考',
  totalsNote: '按画面内容筛选，与设计参考无关的暂不计入',
  page: '页面选择', pages: ['第一页','第二页','第三页','第四页'], topbar: '顶栏对比', topbarA: '字色反色', topbarB: '透明底栏', topbarC: '半透明底栏', align: '显示对齐线', alignCenter: '中线', demo: '演示模式', debugKey: '从第二页开始', replay: '重播',
  demoResult: '演示验证结果', success: '成功', failure: '失败', demoKey: '演示钥匙', demoTail: '0426',
  cooldownOn: '演示小红书：切换冷却中', cooldownOff: '演示小红书：解除冷却', start: '开始',
  cardField:'卡片排法',classicField:'原版',starField:'星空',
  autoplay: '自动播放', skipIntro: '跳过',
  library: '收藏', projects: '项目', libraryDemo: '你的视觉参考', keyPanelAria: '填钥匙',
  platformCount: (n: number) => `已连接 · ${n} 张`,
} as const

/**
 * 引导版文案（?refine=0 回到上面的原文案）。原则：第一次来的人每一步都知道「这是干嘛的、不做会怎样、下一步去哪」；
 * 比喻可以留，但后半句要落到实处；按钮和提示用同一个说法（API Key、先跳过、素材库）。
 */
const REFINED_COPY = {
  ...BASE_COPY,
  homeIntro: '给设计学生的收藏夹。各平台的收藏放一起，记得画面就能找回来。',
  keyTitle: { prefix: '先给它一把', emphasis: '钥匙', second: '让它看懂你的图' },
  keyLabel: '粘贴你的 API Key',
  keyError: '这个 API Key 没验证通过，检查一下有没有复制完整。',
  emptyKey: '先粘贴 API Key，或者点「先跳过」。',
  keySkipped: '之后在设置里填',
  advancedToggle: '高级设置', advancedHide: '收起高级设置',
  progressSteps: ['接上 AI 读图', '连接收藏来源', '看看整理结果'],
  localWaiting: '把图片文件夹拖到这里',
  totalsNote: '只把用得上的设计参考挑出来，其余照样留在收藏里',
  ready: '进入素材库',
} as const

/** 两套文案的字面量类型不同，统一放宽成 string，结构不变 */
type Widen<T> = T extends string ? string
  : T extends (...args: never[]) => unknown ? T
  : T extends readonly (infer U)[] ? readonly Widen<U>[]
  : T extends object ? { readonly [K in keyof T]: Widen<T[K]> }
  : T
export const INTRO_COPY: Widen<typeof BASE_COPY> & { advancedToggle?: string; advancedHide?: string } = REFINE ? REFINED_COPY : BASE_COPY
export const PLATFORMS = [
  { name: '小红书', count: 213 }, { name: 'Pinterest', count: 186 }, { name: '本地相册', count: 148 },
  { name: '站酷', count: 92 }, { name: '花瓣', count: 58 },
] as const
/** Critically/over-damped: no page-level bounce. Only home's original catch keeps its old spring. */
export const INTRO_SPRING = { stiffness: 210, damping: 30 } as const
/** Fable：整体压短，去掉干等；每一段只留“看清楚发生了什么”需要的时间。 */
export const PHASE_MS = { expand: 650, keyTitle: 500, clear: 550, close: 280, contract: 650, platformTitle: 750, platformNames: 900, ejectDuration: 2100, ejectWait: 0, verify: 1200, check: 650, connect: 1200,
  // 引导版：数字滚完要 1.9 秒，原来只剩 1.3 秒读下面那行说明就自动翻页；多给 1 秒
  reveal: REFINE ? 4200 : 3200, gather: 1300 }
export const DEMO_MS = {
  homeTitleHold: 600,
  homeArrange: 3600,
  matrixHold: 700,
  homeCatch: 900,
  autoKey: 2600,
  verify: 800,
  platformTitle: 700,
  ejectDuration: 1900,
  sourcePause: 380,
  connect: 800,
  allPause: 500,
  reveal: 3200,
} as const
/** 手机自动播放：每一页的停留（等待）时间 × 0.7；动画本身的时长不变。电脑版不用 */
export const PHONE_DEMO_DWELL = .7
export const LIBRARY_COUNTS = { collections: 697, references: 112 }

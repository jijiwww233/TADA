import { useEffect } from 'react'
// 截图从 public/guides/key-steps 直接 import：构建时会带哈希打进 /assets/（公网服务只放行 /assets，不放行 /guides/key-steps）
import consoleImg from '../../public/guides/key-steps/console.png'
import createImg from '../../public/guides/key-steps/create.png'
import permissionsImg from '../../public/guides/key-steps/permissions.png'
import copyImg from '../../public/guides/key-steps/copy.png'

/**
 * 「自己部署时怎么申请 API Key」网页教程（内容和 public/guides/api-key.html 一样）。
 * 公网体验版的服务只单独放行了 /guides/api-key.pdf，/guides/api-key.html 会被兜底路由换成 index.html，
 * 原来点开就是一个空白的后台页；现在 index.html 认出这个地址就渲染这一页（main.tsx 里的 isGuide）。
 * 本机 vite dev 时 public 里的静态 html 优先，内容相同。
 */
const STEPS = [
  { title: '登录阿里云百炼', text: '用自己的阿里云账号登录，在首页找到「API Key」入口，打开密钥管理页。', img: consoleImg, alt: '百炼首页的密钥入口，已用橙框标出' },
  { title: '确认地域，创建密钥', text: '先选择项目使用的地域，再点击「创建 API-KEY」。截图用的是北京。密钥、接入地址和模型须与项目所用地域相符。', img: createImg, alt: '密钥管理页的地域和创建按钮' },
  { title: '填写创建信息', text: '业务空间可先用「默认业务空间」，描述可填「TADA 本机测试」。教程使用「全部」权限。团队有访问范围要求时，按要求选「自定义」，再点「确定」。', img: permissionsImg, alt: '创建窗口里的业务空间、描述、权限和确定按钮' },
  { title: '复制密钥，回到 TADA', text: '关闭窗口前点「复制」或「下载」，把密钥存在自己的密码管理器里。回到引导页粘贴并验证。密钥像密码一样保管，别发到群里或放进截图。', img: copyImg, alt: '创建成功后的复制和下载位置，示例密钥已经遮挡' },
]

const CSS = `
html, body { background: #faf8f3; }
.api-guide { --paper: #faf8f3; --line: #dfd3c7; --brown: #685348; --clay: oklch(0.56 0.112 42); --ink: #2b2522;
  max-width: 640px; margin: 0 auto; padding: 28px 20px 48px; background: var(--paper); color: var(--ink);
  font: 15px/1.7 system-ui, -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif; }
.api-guide h1 { margin: 0 0 8px; font: 600 24px/1.35 'Songti SC', 'STSong', serif; }
.api-guide .lead { margin: 0 0 20px; color: var(--brown); font-size: 14px; }
.api-guide h2 { margin: 0 0 4px; font-size: 16px; }
.api-guide ol { margin: 0; padding-inline-start: 22px; }
.api-guide li { margin-bottom: 28px; }
.api-guide p { margin: 0; }
.api-guide img { display: block; width: 100%; height: auto; margin-top: 10px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
.api-guide a { color: var(--clay); text-underline-offset: 3px; }
.api-guide .open { display: inline-flex; align-items: center; min-height: 44px; margin-bottom: 20px; padding: 0 18px; border-radius: 22px; background: var(--clay); color: #fff; text-decoration: none; font-size: 14px; }
.api-guide .note { padding-top: 16px; border-top: 1px solid var(--line); color: var(--brown); font-size: 14px; }
.api-guide .links { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 12px; font-size: 13px; }
.api-guide .links a { display: inline-flex; align-items: center; min-height: 44px; }
`

export default function ApiKeyGuide() {
  useEffect(() => { document.title = '自己部署时怎么申请 API Key · TADA' }, [])
  return <div className="api-guide" lang="zh-CN">
    <style>{CSS}</style>
    <h1>自己部署时怎么申请 API Key</h1>
    <p className="lead">公网体验版已经接好了 AI，不用填。下面的步骤是给在自己电脑上装 TADA 的人看的：用阿里云百炼（千问）申请一把 API Key，粘贴到引导页或设置里。</p>
    <a className="open" href="https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key" target="_blank" rel="noreferrer">打开百炼密钥管理 →</a>
    <ol>
      {STEPS.map(step => <li key={step.title}>
        <h2>{step.title}</h2>
        <p>{step.text}</p>
        <img src={step.img} alt={step.alt} loading="lazy" />
      </li>)}
    </ol>
    <div className="note">
      <h2>验证失败怎么办？</h2>
      <p>检查密钥是否复制完整、地域和接入地址是否对应，并确认所选模型可用且还有额度。如果忘记保存完整密钥，可以去管理页重置或重新创建。</p>
      <p className="links">
        <a href="https://help.aliyun.com/zh/model-studio/get-api-key" target="_blank" rel="noreferrer">百炼官方注册说明</a>
        <a href="https://help.aliyun.com/zh/model-studio/rag-api-authentication" target="_blank" rel="noreferrer">认证与安全说明</a>
      </p>
    </div>
  </div>
}

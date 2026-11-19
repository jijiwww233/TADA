import type { RefObject } from 'react'

const CONSOLE = 'https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key'
const HELP = 'https://help.aliyun.com/zh/model-studio/get-api-key'
const SECURITY = 'https://help.aliyun.com/zh/model-studio/rag-api-authentication'

const steps = [
  {
    title: '登录阿里云百炼',
    text: '用自己的阿里云账号登录，在首页找到「API Key」入口，打开密钥管理页。',
    image: 'console',
    alt: '百炼首页的密钥入口，已用橙框标出',
  },
  {
    title: '确认地域，创建密钥',
    text: '先选择项目使用的地域，再点击「创建 API-KEY」。截图用的是北京。密钥、接入地址和模型须与项目所用地域相符。',
    image: 'create',
    alt: '密钥管理页的地域和创建按钮',
  },
  {
    title: '填写创建信息',
    text: '业务空间可先用「默认业务空间」，描述可填「图然想起本机测试」。教程使用「全部」权限。团队有访问范围要求时，按要求选「自定义」，再点「确定」。',
    image: 'permissions',
    alt: '创建窗口里的业务空间、描述、权限和确定按钮',
  },
  {
    title: '复制密钥，回到引导页',
    text: '关闭窗口前点「复制」或「下载」，把密钥存在自己的密码管理器里。回到本站粘贴并验证。密钥像密码一样保管，别发到群里或放进截图。',
    image: 'copy',
    alt: '创建成功后的复制和下载位置，示例密钥已经遮挡',
  },
]

export function KeyGuide({ dialog }: { dialog: RefObject<HTMLDialogElement | null> }) {
  return <dialog className="intro-key-guide" ref={dialog} aria-labelledby="intro-key-guide-title">
    <header>
      <h2 id="intro-key-guide-title">注册千问密钥</h2>
      <button type="button" onClick={() => dialog.current?.close()}>关闭</button>
    </header>
    <div className="intro-key-guide-body">
      <p><a href={CONSOLE} target="_blank" rel="noreferrer">打开百炼密钥管理</a></p>
      <ol>
        {steps.map(step => <li key={step.image}>
          <h3>{step.title}</h3>
          <p>{step.text}</p>
          <img src={`/guides/key-steps/${step.image}.png`} alt={step.alt} loading="lazy" />
        </li>)}
      </ol>
      <h3>验证失败怎么办？</h3>
      <p>检查密钥是否复制完整、地域和接入地址是否对应，并确认所选模型可用且还有额度。如果忘记保存完整密钥，可以去管理页重置或重新创建。</p>
      <p className="intro-key-guide-links">
        <a href={HELP} target="_blank" rel="noreferrer">百炼官方注册说明</a>
        <a href={SECURITY} target="_blank" rel="noreferrer">认证与安全说明</a>
        <a href="/guides/api-key.pdf" download>下载 PDF</a>
      </p>
    </div>
  </dialog>
}

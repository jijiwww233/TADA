import { useRef, useState } from 'react'
import { usePhoneLayout } from './useMobileLayout'

type ImportOptions = {
  shortcut: { url: string; qr: string } | null
  wechat: boolean
}

/**
 * 「导入 +」：打开导入面板。
 * 「选图片」用的是 <label> 包着真正的 <input type="file">：手指点到的就是文件框本身，
 * 不再用脚本去点一个隐藏的文件框——安卓 / iOS 浏览器都能直接弹出相册，不会被拦。
 */
export function ImportDialog({ demo, busy = false, onFiles }: { demo: boolean; busy?: boolean; onFiles: (files: File[]) => void }) {
  const publicDemo = import.meta.env.VITE_PUBLIC_DEMO === '1'
  const phone = usePhoneLayout()
  const dialog = useRef<HTMLDialogElement>(null)
  const [pairing, setPairing] = useState('')
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState('')
  const [options, setOptions] = useState<ImportOptions | null>(null)
  const open = () => {
    dialog.current?.showModal()
    if (publicDemo) {
      void fetch('/api/demo/import-options', { cache: 'no-store' })
        .then(response => response.ok ? response.json() : null)
        .then(setOptions, () => setOptions(null))
    }
  }
  const pair = async (start: boolean) => {
    setPending(true)
    setMessage('')
    try {
      const response = await fetch('/api/phone-import/pairing', {
        method: start ? 'POST' : 'DELETE',
        headers: { 'X-Library-Client': 'desktop' },
      })
      if (response.status === 409) {
        setMessage('没生成链接：电脑没连上局域网。连上 Wi-Fi 后再试，或者直接点「选图片」。')
        return
      }
      if (!response.ok) throw new Error()
      setPairing(start ? (await response.json()).url : '')
      setMessage(start ? '用手机浏览器打开这个链接，就能从手机相册导入。' : '手机配对已关闭。')
    } catch {
      setMessage('手机配对暂时用不了：服务没响应。稍后再试，或者直接点「选图片」。')
    } finally {
      setPending(false)
    }
  }

  return <>
    <button type="button" className="m-hb" aria-haspopup="dialog" disabled={busy} onClick={open}>
      {busy ? '导入中…' : '导入 +'}
    </button>
    <dialog ref={dialog} className="m-import-dialog" aria-labelledby="m-import-title">
      <header className="m-import-head">
        <h2 id="m-import-title">导入图片</h2>
        <form method="dialog"><button type="submit" className="ds-btn ds-btn--ghost" aria-label="关闭导入面板">关闭</button></form>
      </header>
      {publicDemo && <p className="m-import-notice">这是共用演示库，大家都能看到上传的图，请只上传可以公开的素材。</p>}
      <div className={`m-import-options${publicDemo ? ' m-import-options--public' : ''}`}>
        <section>
          <h3><i aria-hidden="true">(01)</i> 相册 / 文件</h3>
          <p className="ds-help">{demo ? '演示模式：选的图不会真的存下来。' : '可以一次选多张，也能直接把图拖进来。'}</p>
          <label className="ds-btn ds-btn--primary ds-file-btn">
            选图片
            <input type="file" accept="image/*" multiple
              onChange={event => {
                const files = Array.from(event.currentTarget.files ?? [])
                event.currentTarget.value = ''
                dialog.current?.close()
                if (files.length) onFiles(files)
              }} />
          </label>
        </section>
        {publicDemo && <>
          <section>
            <h3><i aria-hidden="true">(02)</i> iPhone / iPad 快捷指令</h3>
            <p className="ds-help">截图或选图后，点分享 →「收进 TADA」。</p>
            {options?.shortcut ? <>
              <a className="ds-btn ds-btn--secondary" href={options.shortcut.url} target="_blank" rel="noreferrer">添加快捷指令</a>
              {/* 手机上就是要装快捷指令的那台设备，扫自己屏幕上的码没意义：只留按钮 */}
              {!phone && <img className="m-import-qr" src={options.shortcut.qr} alt="添加快捷指令二维码" />}
            </> : <p className="m-import-unavailable">请向演示负责人索取</p>}
          </section>
          <section>
            <h3><i aria-hidden="true">(03)</i> 微信</h3>
            <p className="ds-help">扫码关注测试号，把图发给它。</p>
            {options?.wechat ? (
              <div className="m-import-wechat">
                <img className="m-import-qr" src="/api/demo/wechat-qr" alt="微信测试号二维码" />
                {phone && <p className="m-import-wechat-tip">长按保存二维码，再用微信扫一扫</p>}
              </div>
            ) : <p className="m-import-unavailable">请向演示负责人索取</p>}
          </section>
        </>}
        {!demo && !publicDemo && <section>
          <h3><i aria-hidden="true">(02)</i> 局域网配对</h3>
          <p className="ds-help">手机和电脑连在同一个 Wi-Fi 时，用手机打开链接就能从相册导入。</p>
          <button type="button" className="ds-btn ds-btn--secondary" disabled={pending} aria-busy={pending || undefined}
            onClick={() => void pair(!pairing)}>{pairing ? '关闭手机配对' : '生成手机配对链接'}</button>
          {pairing && <input className="m-set-input ds-input" aria-label="手机配对链接" readOnly
            value={pairing} onFocus={event => event.currentTarget.select()} />}
          {message && <p className="ds-help" role="status">{message}</p>}
        </section>}
      </div>
    </dialog>
  </>
}

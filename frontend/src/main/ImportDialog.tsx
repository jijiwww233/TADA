import { useRef, useState } from 'react'
import { usePhoneLayout } from './useMobileLayout'
import { track } from './telemetry'
import './import-tip.css'

type ImportOptions = {
  shortcut: { url: string; qr: string } | null
  wechat: boolean
}

/**
 * 「导入 +」：打开导入面板。电脑、手机同一套文字，只有三块，按这个顺序：
 *   (01) 相册 / 文件 → (02) iPhone / iPad 快捷指令 → (03) 安卓「图图」
 * 「选图片」用的是 <label> 包着真正的 <input type="file">：手指点到的就是文件框本身，
 * 不再用脚本去点一个隐藏的文件框——安卓 / iOS 浏览器都能直接弹出相册，不会被拦。
 */
export function ImportDialog({ busy = false, onFiles }: { demo: boolean; busy?: boolean; onFiles: (files: File[]) => void }) {
  const publicDemo = import.meta.env.VITE_PUBLIC_DEMO === '1'
  const phone = usePhoneLayout()
  const dialog = useRef<HTMLDialogElement>(null)
  const [options, setOptions] = useState<ImportOptions | null>(null)
  const open = () => {
    track('import_open')
    dialog.current?.showModal()
    // 快捷指令的链接：沿用现在的配置（演示站从接口读）
    if (publicDemo) {
      void fetch('/api/demo/import-options', { cache: 'no-store' })
        .then(response => response.ok ? response.json() : null)
        .then(setOptions, () => setOptions(null))
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
      <div className={`m-import-options${publicDemo ? ' m-import-options--public' : ''}`}>
        <section>
          <h3><i aria-hidden="true">(01)</i> 相册 / 文件</h3>
          <p className="ds-help">可以一次选多张，收进来的图不带原链接。</p>
          <label className="ds-btn ds-btn--primary ds-file-btn">
            选图片
            <input type="file" accept="image/*" multiple
              onChange={event => {
                const files = Array.from(event.currentTarget.files ?? [])
                event.currentTarget.value = ''
                dialog.current?.close()
                if (files.length) {
                  track('import', { method: 'picker', count: files.length })
                  onFiles(files)
                }
              }} />
          </label>
        </section>
        <section>
          <h3><i aria-hidden="true">(02)</i> iPhone / iPad：所有 App 都能用</h3>
          <p className="ds-help">
            在任何 App 里分享图片，选「tada」就收进来。<br />
            小红书点「复制链接」后运行 tada，整条笔记和原链接一起收。
          </p>
          {/* 链接沿用现在的配置；还没读到链接（或这个环境没配）时按钮先不能点 */}
          {options?.shortcut ? <>
            <a className="ds-btn ds-btn--secondary" href={options.shortcut.url} target="_blank" rel="noreferrer"
              onClick={() => track('import', { method: 'shortcut' })}>添加快捷指令</a>
            {/* 电脑上照旧带二维码（用手机扫了去装）；手机上就是要装的那台，只留按钮 */}
            {!phone && <img className="m-import-qr" src={options.shortcut.qr} alt="添加快捷指令二维码" />}
          </> : <button type="button" className="ds-btn ds-btn--secondary" disabled>添加快捷指令</button>}
        </section>
        <section>
          <h3><i aria-hidden="true">(03)</i> 安卓：小红书专用</h3>
          <p className="ds-help">
            装上图图，在小红书点「分享」再点图图，整条笔记和原链接一起收。<br />
            其他 App 的图，截图后用上面的「选图片」。
          </p>
          <a className="ds-btn ds-btn--secondary" href="/downloads/tutu.apk" download
            onClick={() => track('import', { method: 'tutu' })}>下载图图</a>
        </section>
        <p style={{ margin: '4px 0 0', fontSize: 12, lineHeight: 1.5, color: 'var(--color-text-tertiary, #8a8178)' }}>
          为了改进体验，会匿名记录操作
        </p>
      </div>
    </dialog>
  </>
}

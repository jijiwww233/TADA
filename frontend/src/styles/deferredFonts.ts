/**
 * 主界面字体延后加载：首屏先用系统字，等页面 load、首屏图片落定之后再把
 * @font-face（deferred-fonts.css）挂进来。
 *
 * 为什么：Main Serif SC 两个 woff2 共约 2.8MB、Newsreader 两张约 0.25MB，
 * 比首屏图片和 JS 加起来还大。字体一进首屏的 CSS，浏览器就把它排在 6 条
 * HTTP/1.1 连接里跟图片抢，手机上首图要等到十几秒。font-display: swap 不变，
 * 字体到了之后是平滑替换，不会白屏。
 */

const loadDeferredFontCss = () => import('./deferred-fonts.css')

let scheduled = false

function whenIdle(run: () => void) {
  const withIdle = window as Window & {
    requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number
  }
  if (typeof withIdle.requestIdleCallback === 'function') {
    withIdle.requestIdleCallback(() => run(), { timeout: 800 })
  } else {
    window.setTimeout(run, 300)
  }
}

function inViewport(img: HTMLImageElement) {
  const box = img.getBoundingClientRect()
  return box.width > 0 && box.height > 0 && box.bottom > 0 && box.top < window.innerHeight
}

/**
 * 等首屏（视口内）那几张图解码完再挂字体，别让 3MB 的 woff2 挤在图片前面。
 *
 * 还在加载时页面上可能一张 img 都还没有（素材库会先把前 4 张图预取、再插进 DOM），
 * 这时候别急着走：先等最多 8 秒，给图片出现的机会。兜底最多等 15 秒。
 */
function whenFirstImagesSettled(run: () => void) {
  const startedAt = performance.now()
  const check = () => {
    const visible = Array.from(document.images).filter(inViewport).slice(0, 8)
    const allLoaded = visible.length > 0 && visible.every((img) => img.complete && img.naturalWidth > 0)
    const waited = performance.now() - startedAt
    if (allLoaded || waited > 15000 || (visible.length === 0 && waited > 8000)) {
      run()
      return
    }
    window.setTimeout(check, 150)
  }
  check()
}

function start() {
  whenFirstImagesSettled(() => whenIdle(() => { void loadDeferredFontCss() }))
}

/** /main、/main-v4、/design-system 都从 main.css 来，用得到这两套字体。 */
export function scheduleDeferredFonts(): void {
  if (scheduled) return
  scheduled = true
  if (document.readyState === 'complete') start()
  else window.addEventListener('load', start, { once: true })
}

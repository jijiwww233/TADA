/**
 * 匿名使用记录：随机设备编号 + 攒一批事件发给 /api/events。
 *
 * 规则：
 * - 只在浏览器本地存一个随机编号，不采集图片内容、不采集搜索框以外的文字；
 * - 发送失败一律静默丢弃，不弹任何提示，也不影响页面；
 * - 每 10 秒或离开页面时批量发一次，单批最多 200 条。
 */
type DetailValue = string | number | boolean | null | undefined
export type TrackDetail = Record<string, DetailValue>

type TrackEvent = { type: string; page?: string; detail?: TrackDetail }

const DEVICE_KEY = 'tada-device-id'
/** 这个浏览器被标记为「我自己」：不再发送任何事件（统计页按钮也写这个键）。 */
const OFF_KEY = 'tada-telemetry-off'
const ENDPOINT = '/api/events'
const FLUSH_MS = 10_000
const MAX_QUEUE = 200

function randomId(): string {
  try {
    const bytes = new Uint8Array(12)
    crypto.getRandomValues(bytes)
    return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  } catch {
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`
  }
}

function readDeviceId(): string {
  try {
    const saved = window.localStorage.getItem(DEVICE_KEY)
    if (saved && /^[A-Za-z0-9_-]{6,64}$/.test(saved)) return saved
    const created = randomId()
    window.localStorage.setItem(DEVICE_KEY, created)
    return created
  } catch {
    // 隐私模式 / 存储被禁用：这次打开用一个临时编号，发不出去也不影响页面。
    return randomId()
  }
}

function readMeFlag(): { excluded: boolean; justMarked: boolean } {
  if (typeof window === 'undefined') return { excluded: false, justMarked: false }
  let me: string | null = null
  try { me = new URLSearchParams(window.location.search).get('me') } catch {}
  try {
    if (me === '1') window.localStorage.setItem(OFF_KEY, '1')
    else if (me === '0') window.localStorage.removeItem(OFF_KEY)
    return { excluded: window.localStorage.getItem(OFF_KEY) === '1', justMarked: me === '1' }
  } catch {
    return { excluded: me === '1', justMarked: me === '1' }
  }
}

/** 2 秒后消失的小提示；样式用设计系统的变量，取不到时用同色兜底。 */
function showMutedNote(text: string) {
  if (typeof document === 'undefined' || !document.body) return
  const note = document.createElement('div')
  note.textContent = text
  note.setAttribute('role', 'status')
  note.style.cssText = [
    'position:fixed', 'top:12px', 'left:50%', 'transform:translateX(-50%)',
    'z-index:2147483000', 'max-width:calc(100vw - 32px)', 'padding:8px 14px',
    'border-radius:999px', 'background:var(--color-bg-surface,#fffdf8)',
    'border:1px solid var(--color-border-default,#e7dfd1)',
    'color:var(--color-text-secondary,#7c7064)',
    'font:12px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif',
    'box-shadow:0 6px 24px rgba(0,0,0,.08)', 'pointer-events:none', 'text-align:center',
  ].join(';')
  document.body.appendChild(note)
  window.setTimeout(() => note.remove(), 2000)
}

const deviceId = typeof window === 'undefined' ? '' : readDeviceId()
const me = typeof window === 'undefined' ? { excluded: false, justMarked: false } : readMeFlag()
const excluded = me.excluded
if (me.justMarked) showMutedNote('这台设备已设为「我自己」，不再记录')

let queue: TrackEvent[] = []
let timer: number | null = null
let started = false
let page = ''
let pageSince = Date.now()
let leftReported = false

function enqueue(event: TrackEvent) {
  if (!deviceId || excluded) return
  queue.push(event)
  if (queue.length >= 40) flush()
}

/** 记录一个事件；detail 只放短字符串和数字，不要放图片地址或用户输入的长文本。 */
export function track(type: string, detail?: TrackDetail, atPage?: string) {
  const target = atPage ?? page
  enqueue({ type, page: target || undefined, detail })
}

/** 切换页面：先报上一个页面的离开时长，再报新页面访问。 */
export function pageView(next: string) {
  if (!deviceId || excluded) return
  if (page && page !== next) {
    enqueue({ type: 'leave', page, detail: { dwell: Math.max(1, Math.round((Date.now() - pageSince) / 1000)) } })
  }
  page = next
  pageSince = Date.now()
  leftReported = false
  enqueue({ type: 'page_view', page: next })
}

function payload(events: TrackEvent[]) {
  return JSON.stringify({ device_id: deviceId, events })
}

function leaveEvent() {
  if (!page || leftReported) return null
  leftReported = true
  return { type: 'leave', page, detail: { dwell: Math.max(1, Math.round((Date.now() - pageSince) / 1000)) } }
}

/** 发一批；失败就丢掉，不重试、不报错。 */
export function flush(useBeacon = false) {
  if (!deviceId || excluded || !queue.length) return
  const batch = queue.slice(0, MAX_QUEUE)
  queue = queue.slice(MAX_QUEUE)
  try {
    const body = payload(batch)
    if (useBeacon && typeof navigator.sendBeacon === 'function') {
      navigator.sendBeacon(ENDPOINT, new Blob([body], { type: 'application/json' }))
      return
    }
    void fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
    }).catch(() => {})
  } catch {
    // 忽略：统计不能影响主流程。
  }
}

/** 在 MainApp 挂载时调用一次。 */
export function startTelemetry(initialPage: string) {
  if (!deviceId || excluded || started) return
  started = true
  page = initialPage
  pageSince = Date.now()
  enqueue({ type: 'app_open', page: initialPage })
  timer = window.setInterval(() => flush(), FLUSH_MS)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      const leaving = leaveEvent()
      if (leaving) queue.push(leaving)
      flush(true)
    } else {
      pageSince = Date.now()
      leftReported = false
    }
  })
  window.addEventListener('pagehide', () => {
    const leaving = leaveEvent()
    if (leaving) queue.push(leaving)
    flush(true)
  })
  // 保持引用，避免打包器认为定时器没用到（同时方便以后停止）。
  void timer
}

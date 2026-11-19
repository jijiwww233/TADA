// 一镜到底（手机引导页）逐帧截图 + 焦点卡尺寸测量。
//
// 用法（在 frontend 目录里）：
//   1. 把 src/lab/intro/oneTake.ts 里的 PHONE_ONE_TAKE 改成 true
//   2. npx vite --port 5190            （另开一个窗口跑着，不碰 18080 的公网演示版）
//   3. node scripts/one-take-shots.mjs [地址] [输出目录]
//      默认地址 http://127.0.0.1:5190/intro?play（自动从头播到最后一页），默认输出 ../dsh-tmp/one-take-shots
//
// 输出：
//   frames/<序号>_<毫秒>_<页面>.png  —— 390×844，从开始约每 100ms 一张，一直截到最后一页转盘转起来；
//                                      文件名里的毫秒是相对开始的真实时间，页面名后面带 -leaving 表示正在换页淡出
//   transitions.md                    —— 每个镜头切换处对应哪几张（切换前 0.3 秒到切换后 2.2 秒）
//   sizes.md / sizes.json            —— 每个队形焦点卡的尺寸：渲染器实际画的宽度 + 截图里按像素量的宽度，和 S 的比值
import { chromium } from 'playwright'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

const url = process.argv[2] ?? 'http://127.0.0.1:5190/intro?play'
const out = path.resolve(process.argv[3] ?? path.join('..', 'dsh-tmp', 'one-take-shots'))
const frames = path.join(out, 'frames')
await mkdir(frames, { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true })
await page.goto(url, { waitUntil: 'networkidle' })
await page.waitForSelector('[data-home-stage]')

const state = () => page.evaluate(() => {
  const stage = document.querySelector('[data-home-stage]')
  const track = document.querySelector('.intro-phone-reel-track')?.getBoundingClientRect()
  const gallery = document.querySelector('.intro-circular-gallery')?.getBoundingClientRect()
  return {
    phase: stage?.getAttribute('data-phase') ?? '?',
    leaving: stage?.hasAttribute('data-leaving') ?? false,
    oneTake: stage?.hasAttribute('data-one-take') ?? false,
    debug: window.__oneTake ?? null,
    reel: track && track.width ? { x: track.left + track.width / 2, y: track.top + track.height / 2 } : null,
    gallery: gallery && gallery.height ? { x: gallery.left + gallery.width / 2, y: gallery.top + gallery.height / 2 } : null,
  }
})

/** 截图里按像素量一张卡的宽：从 (x, y) 往左右走，连续 6 个像素都接近纸色（右下角取样）才算走出卡片 */
const pixelWidth = (png, x, y) => page.evaluate(async ({ data, x, y }) => {
  const img = new Image()
  img.src = 'data:image/png;base64,' + data
  await img.decode()
  const c = document.createElement('canvas')
  c.width = img.width; c.height = img.height
  const g = c.getContext('2d')
  g.drawImage(img, 0, 0)
  const px = (ix, iy) => g.getImageData(ix, iy, 1, 1).data
  const bg = px(img.width - 2, img.height - 2)
  const isBg = (ix) => { const p = px(ix, Math.round(y)); return Math.abs(p[0] - bg[0]) + Math.abs(p[1] - bg[1]) + Math.abs(p[2] - bg[2]) < 24 }
  const walk = (dir) => {
    let ix = Math.round(x), run = 0
    while (ix > 0 && ix < img.width - 1) { ix += dir; run = isBg(ix) ? run + 1 : 0; if (run >= 6) return ix - dir * 6 }
    return ix
  }
  if (isBg(Math.round(x))) return null
  return walk(1) - walk(-1) + 1
}, { data: png.toString('base64'), x, y })

const t0 = Date.now()
const log = []
const sizes = {}
let n = 0, readyAt = 0, measuredGallery = false
const stableAfter = { home: 0, platforms: 1500, reveal: 1500 }
const phaseStart = {}
let lastKey = ''
while (true) {
  const s = await state()
  const t = Date.now() - t0
  if (n === 0 && !s.oneTake) console.warn('注意：舞台上没有 data-one-take，PHONE_ONE_TAKE 可能还是 false')
  const key = s.phase + (s.leaving ? '-leaving' : '')
  if (key !== lastKey) { phaseStart[key] = t; lastKey = key }
  const png = await page.screenshot()
  const name = `${String(n).padStart(4, '0')}_${String(t).padStart(6, '0')}ms_${key}.png`
  await writeFile(path.join(frames, name), png)
  log.push({ n, t, key, phase: s.phase, file: name, debug: s.debug })
  n++

  // 尺寸：每个队形停稳以后量一次（第一页排成墙、选平台页、整理页的环、最后一页转盘）
  const d = s.debug
  const label = s.phase === 'home' && d?.neat >= .999 ? '第一页图片墙'
    : s.phase === 'home' && d && d.neat < .01 && t > 1500 ? '第一页散开'
    : s.phase === 'platforms' && !s.leaving && t - phaseStart[key] > stableAfter.platforms ? '选平台页卷轴'
    : s.phase === 'reveal' && t - phaseStart[key] > stableAfter.reveal ? '整理页的环'
    : null
  if (label && !sizes[label] && d?.focus) {
    const at = label === '选平台页卷轴' && s.reel ? s.reel : d.focus
    sizes[label] = { S: d.S, renderer: d.focus.w, pixel: await pixelWidth(png, at.x, at.y), at, t }
  }
  if (s.phase === 'ready' && !readyAt) readyAt = t
  if (readyAt && !measuredGallery && t - readyAt > 1400 && s.gallery) {
    measuredGallery = true
    const S = log.findLast(row => row.debug?.S)?.debug.S
    sizes['最后一页转盘'] = { S, renderer: null, pixel: await pixelWidth(png, s.gallery.x, s.gallery.y), at: s.gallery, t }
  }
  if (readyAt && t - readyAt > 4500) break
  if (t > 120000) { console.warn('两分钟还没到最后一页，停止'); break }
  await page.waitForTimeout(Math.max(0, 100 - (Date.now() - t0 - t)))
}
await browser.close()

// 镜头切换处：key（页面 + 是否在淡出）变化的地方，取切换前 0.3 秒到切换后 2.2 秒
const cuts = []
for (let i = 1; i < log.length; i++) if (log[i].key !== log[i - 1].key) cuts.push(i)
const lines = ['# 一镜到底：镜头切换处的截图', '', `地址：${url}`, '']
for (const i of cuts) {
  const at = log[i].t
  const group = log.filter(row => row.t >= at - 300 && row.t <= at + 2200)
  lines.push(`## ${log[i - 1].key} → ${log[i].key}（${at}ms）`, '', ...group.map(row => `- ${row.file}`), '')
}
await writeFile(path.join(out, 'transitions.md'), lines.join('\n'))

const fmt = (v, d = 1) => v == null ? '—' : Number(v).toFixed(d)
const table = ['# 一镜到底：焦点卡尺寸（390×844）', '',
  'S = 原版整理页环上正前方那张卡的宽度（oneTake.ts 的 oneTakeS）。渲染器宽 = 画布实际画的宽度；像素宽 = 截图里从卡片中心往左右量到纸色为止（环上左右邻卡有重叠时会偏大）。', '',
  '| 队形 | S (px) | 渲染器宽 | 渲染器 / S | 像素宽 | 像素 / S | 截图时刻 |', '|---|---|---|---|---|---|---|']
for (const [name, v] of Object.entries(sizes)) {
  table.push(`| ${name} | ${fmt(v.S)} | ${fmt(v.renderer)} | ${v.renderer ? fmt(v.renderer / v.S, 3) : '—'} | ${v.pixel ?? '—'} | ${v.pixel && v.S ? fmt(v.pixel / v.S, 3) : '—'} | ${v.t}ms |`)
}
await writeFile(path.join(out, 'sizes.md'), table.join('\n') + '\n')
await writeFile(path.join(out, 'sizes.json'), JSON.stringify({ sizes, log: log.map(({ debug, ...row }) => ({ ...row, focus: debug?.focus?.w, S: debug?.S, formation: debug?.formation })) }, null, 2))
console.log(`截图 ${log.length} 张 → ${frames}`)
console.log(table.join('\n'))

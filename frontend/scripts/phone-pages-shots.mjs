// 手机引导页：390×844 每页一张截图 + 每页停留时长 + 每页能点的东西清单。
//
// 用法（在 frontend 目录里）：
//   1. VITE_PUBLIC_DEMO=1 npx vite --port 5190     （另开一个窗口跑着；体验版流程。不碰 18080 的公网演示版）
//   2. node scripts/phone-pages-shots.mjs [地址] [输出目录]
//      默认地址 http://127.0.0.1:5190/intro?play（自动播放从头到最后一页），默认输出 ../dsh-tmp/phone-pages
//
// 输出：
//   <序号>_<页面>.png  —— 每页停稳后、换页前的最后一帧（最后一页是进来 3 秒后）
//   pages.md           —— 每页停留多久；每页上看得见、能点的按钮/链接（文字、大小、底色），
//                         以及右上角「跳过」的字号和点击区域
import { chromium } from 'playwright'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

const url = process.argv[2] ?? 'http://127.0.0.1:5190/intro?play'
const out = path.resolve(process.argv[3] ?? path.join('..', 'dsh-tmp', 'phone-pages'))
await mkdir(out, { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
await page.goto(url, { waitUntil: 'networkidle' })
await page.waitForSelector('[data-home-stage]')

const PAGES = { home: '第一页', key: 'Key 页', platforms: '选平台页', reveal: '整理结果页', ready: '最后一页' }

const state = () => page.evaluate(() => {
  const stage = document.querySelector('[data-home-stage]')
  return { phase: stage?.getAttribute('data-phase') ?? '?', leaving: stage?.hasAttribute('data-leaving') ?? false }
})

/** 当前看得见、能点的东西：按钮 / 链接 / 输入框（不含 disabled、隐藏、透明、点不到的） */
const clickables = () => page.evaluate(() => {
  const list = []
  for (const el of document.querySelectorAll('button, a[href], input:not([type=hidden]), [role=button]')) {
    const style = getComputedStyle(el), box = el.getBoundingClientRect()
    if (el.disabled || !box.width || !box.height || style.visibility === 'hidden' || style.pointerEvents === 'none') continue
    let o = 1
    for (let n = el; n; n = n.parentElement) o *= Number(getComputedStyle(n).opacity)
    if (o < .05 || box.bottom < 0 || box.top > innerHeight || box.right < 0 || box.left > innerWidth) continue
    list.push({
      text: (el.getAttribute('aria-label') || el.textContent || el.placeholder || el.tagName).trim().replace(/\s+/g, ' ').slice(0, 24),
      w: Math.round(box.width), h: Math.round(box.height), bg: style.backgroundColor, font: style.fontSize,
    })
  }
  return list
})

const skipInfo = () => page.evaluate(() => {
  const el = [...document.querySelectorAll('.intro-clean-tools button')].find(b => b.textContent.trim() === '跳过')
  if (!el) return null
  const box = el.getBoundingClientRect()
  return { font: getComputedStyle(el).fontSize, w: Math.round(box.width), h: Math.round(box.height) }
})

const t0 = Date.now()
const seen = []          // [{ phase, start, end, png, clicks }]
let cur = null, readyAt = 0, skip = null
while (true) {
  const s = await state()
  const t = Date.now() - t0
  if (!skip) skip = await skipInfo()
  if (!cur || cur.phase !== s.phase) {
    if (cur) cur.end = t
    cur = { phase: s.phase, start: t, end: t, png: null, clicks: [] }
    seen.push(cur)
  }
  // 换页淡出时不截（那是半透明的过渡帧），只留这一页停稳时的最后一帧
  if (PAGES[s.phase] && !s.leaving) { cur.png = await page.screenshot(); cur.clicks = await clickables() }
  if (s.phase === 'ready' && !readyAt) readyAt = t
  if (readyAt && t - readyAt > 3000) { cur.end = t; break }
  if (t > 90000) { console.warn('90 秒还没到最后一页，停止'); break }
  await page.waitForTimeout(100)
}
await browser.close()

const lines = ['# 手机引导页：每页截图（390×844，自动播放）', '', `地址：${url}`, '',
  `右上角「跳过」：${skip ? `字号 ${skip.font}，点击区域 ${skip.w}×${skip.h}` : '没找到'}`, '',
  '| 页面 | 停留（含进出场） |', '|---|---|']
for (const p of seen) lines.push(`| ${PAGES[p.phase] ?? p.phase} | ${p.phase === 'ready' ? '（最后一页，不自动翻）' : `${((p.end - p.start) / 1000).toFixed(2)} 秒`} |`)
lines.push('', `从开始到最后一页：${(readyAt / 1000).toFixed(2)} 秒`, '')
let i = 0
for (const p of seen) {
  if (!PAGES[p.phase] || !p.png) continue
  const file = `${++i}_${p.phase}.png`
  await writeFile(path.join(out, file), p.png)
  lines.push(`## ${i}. ${PAGES[p.phase]}（${file}）`, '', '看得见、能点的：', '')
  for (const c of p.clicks) lines.push(`- 「${c.text}」 ${c.w}×${c.h}，字号 ${c.font}，底色 ${c.bg}`)
  if (!p.clicks.length) lines.push('- （没有）')
  lines.push('')
}
await writeFile(path.join(out, 'pages.md'), lines.join('\n'))
console.log(lines.join('\n'))
console.log(`截图 → ${out}`)

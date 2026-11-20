import { useEffect, useMemo, useState, type CSSProperties } from 'react'

import type { WiringApi } from '../api/wiring'
import type { LabImage, Manifest } from '../lab/data'
import { thumbnailSrc } from '../imageThumbnail'
import { shortTitle } from './caption'
import { mergeDetail } from './wiringData'
import './calendar-inspiration.css'
import { Heatmap } from './ActivityGraph'
import type { ContributionDay } from './skyline'
import { sourceLabel } from './meta'
import { usePhoneLayout } from './useMobileLayout'

type Row = { date: string; count: number }
type Source = { platform: string; count: number }
type Grain = 'day' | 'week' | 'total'
const INSPIRATION_DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' })
/** 手机上日期写中文：「11月19日」 */
const INSPIRATION_DAY_ZH = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric' })
/** 手机上热力图只画最近 3 个月（约 13 周），格子才够大、月份字才放得下 */
const PHONE_SPAN_DAYS = 91
const SAVED_DAY = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
// 优先用列表接口带的短标题；有了它，前三张就不用再去取详情
const imageTitle = (item: LabImage) => item.title?.trim() || shortTitle(item.description, item.tags) || shortTitle(item.post_title, [])
function savedDate(value: string | null | undefined) {
  if (!value) return ''
  const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? value + 'T00:00:00' : value)
  return Number.isNaN(date.getTime()) ? '' : SAVED_DAY.format(date)
}
const DAY = 86400000
const NO_DAYS: ContributionDay[] = []
const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10)
const PLATFORM_LABEL: Record<string, string> = { xhs: '小红书', local: '本地导入', zcool: '站酷', pinterest: 'Pinterest', huaban: '花瓣' }
const PLATFORM_ICON: Record<string, string> = { xhs: '/brand/xhs.png', zcool: '/brand/zcool.svg', pinterest: '/brand/pinterest.svg', local: '/brand/local.png', huaban: '/brand/huaban.png' }
/** 图标固定这一排，谁没数据就只显示图标。 */
const SOURCE_ORDER = ['xhs', 'zcool', 'pinterest', 'huaban', 'local']
/** 示意格子：真数据常常挤在一两天里，热力图上就只剩两个格子，动效根本看不见。 */
const DEMO_COUNTS = [0, 0, 1, 0, 3, 0, 0, 0, 6, 0, 9, 0, 2, 0, 0, 12, 1, 0, 4, 0, 0, 0, 7, 0, 5, 0, 11, 0, 2, 0, 0, 3]

/** 少于 12 天真实记录时，在图前面垫一段示意格子——只画图，洞察里的数字仍然只算真的。 */
function withDemo(real: Row[]): Row[] {
  if (real.length >= 12) return real
  const first = Date.parse(real[0]?.date ?? '')
  if (!Number.isFinite(first)) return real
  const demo: Row[] = []
  for (let i = 182; i >= 1; i--) {
    const count = DEMO_COUNTS[(i * 7 + 5) % DEMO_COUNTS.length]
    if (count > 0) demo.push({ date: iso(first - i * DAY), count })
  }
  return [...demo, ...real]
}

/** 收藏日历：每日灵感 / 收藏活动（每天·每周·累计）/ 常用导入软件 / 洞察。数字全是真的。 */
export function ActivityPage({ api, demo, manifest, onOpen, grain }: {
  api: WiringApi
  demo: boolean
  manifest: Manifest
  onOpen: (item: LabImage, list: LabImage[], index: number) => void

  /** 「每天 / 每周 / 累计」挪到顶栏右侧，这里只读当前值 */
  grain: Grain
}) {
  const phone = usePhoneLayout()
  const [days, setDays] = useState<ContributionDay[] | null>(null)
  const [sources, setSources] = useState<Source[] | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'empty' | 'failed'>('loading')

  useEffect(() => {
    let live = true
    api.getDailyCounts()
      .then((rows: Row[]) => {
        if (!live) return
        if (!Array.isArray(rows) || rows.length === 0) { setState('empty'); return }
        setDays(rows.map(r => ({ date: r.date, count: r.count })))
        setState('ready')
      })
      .catch(() => { if (live) setState('failed') })
    api.getPlatformCounts()
      .then((rows: Source[]) => { if (live && Array.isArray(rows)) setSources(rows) })
      .catch(() => {})
    return () => { live = false }
  }, [api])

  const picks = useMemo(() => manifest.cards.slice(0, 4), [manifest.cards])
  const [pickDetails, setPickDetails] = useState<Record<number, LabImage>>({})
  useEffect(() => {
    let live = true
    setPickDetails({})
    picks.forEach((item, index) => {
      // 手机上前三张都可能换到大图位置、要显示描述：没有描述的也去取一次详情
      const needDescription = phone && index < 3 && !item.description?.trim()
      if (!needDescription && imageTitle(item) && (index !== 0 || item.saved_at)) return
      void api.getDetail(item.id).then(detail => {
        if (live && detail) setPickDetails(previous => ({ ...previous, [item.id]: mergeDetail(item, detail) }))
      }).catch(() => {})
    })
    return () => { live = false }
  }, [api, picks, phone])
  /** 手机每日灵感：前三张谁在大图位置。order[0] 是大图，order[1]、order[2] 是右下两张小图；点小图就和大图对调 */
  const [order, setOrder] = useState([0, 1, 2])
  useEffect(() => { setOrder([0, 1, 2]) }, [picks])
  const lead = picks[0]
  const leadInfo = lead && (pickDetails[lead.id] ?? lead)
  const leadTitle = leadInfo ? imageTitle(leadInfo) : ''
  const leadSaved = savedDate(leadInfo?.saved_at)

  const shown = useMemo<ContributionDay[] | null>(() => {
    if (!days) return null
    const base = demo ? withDemo(days) : days
    if (grain === 'day') return base
    if (grain === 'week') {
      const acc = new Map<string, number>()
      for (const d of base) {
        const ms = Date.parse(d.date)
        const monday = ms - ((new Date(ms).getUTCDay() + 6) % 7) * DAY
        acc.set(iso(monday), (acc.get(iso(monday)) ?? 0) + d.count)
      }
      return [...acc.entries()].map(([date, count]) => ({ date, count }))
    }
    let sum = 0
    return [...base].sort((a, b) => (a.date < b.date ? -1 : 1)).map(d => { sum += d.count; return { date: d.date, count: sum } })
  }, [days, grain, demo])

  const stats = useMemo(() => {
    if (!days || !days.length) return null
    const counts = new Map(days.map(d => [d.date, d.count]))
    let total = 0, busiest = { date: '', count: 0 }
    for (const d of days) { total += d.count; if (d.count > busiest.count) busiest = { date: d.date, count: d.count } }
    const last = days[days.length - 1].date
    const end = Date.parse(last)
    let current = 0
    for (let i = 0; i <= 400; i++) { if (counts.has(iso(end - i * DAY))) current += 1; else break }
    const sorted = [...counts.keys()].sort()
    let longest = 0, streak = 0, prev: number | null = null
    for (const k of sorted) {
      const ms = Date.parse(k)
      streak = prev !== null && ms - prev === DAY ? streak + 1 : 1
      longest = Math.max(longest, streak)
      prev = ms
    }
    return { total, busiest, longest, current }
  }, [days])

  const sourceList = SOURCE_ORDER.filter(platform => demo || sources?.some(row => row.platform === platform)).map(platform => ({
    platform,
    count: sources?.find(s => s.platform === platform)?.count ?? 0,
  }))

  return (
    <section className="m-page m-cal">

      <h2 className="m-cal-h" data-reveal>每日灵感</h2>
      {/* 手机：左边一张大图（约 45% 宽，按原比例完整显示，不裁），右边从上到下：日期 + 标题一行、三行描述、两张小图。
          点小图就和大图对调，右边的标题和描述跟着换。电脑照旧是大图 + 右栏 */}
      {phone && lead && leadInfo && (() => {
        const slots = order.filter(at => picks[at])
        const big = picks[slots[0]]
        const bigInfo = pickDetails[big.id] ?? big
        const bigTitle = imageTitle(bigInfo) || sourceLabel(bigInfo)
        const bigText = bigInfo.description?.trim() || [sourceLabel(bigInfo), savedDate(bigInfo.saved_at) && `收藏于 ${savedDate(bigInfo.saved_at)}`].filter(Boolean).join(' · ')
        return (
          <div className="m-cal-daily" data-reveal>
            <button type="button" className="m-cal-daily-big" aria-label={`打开：${bigTitle}`}
              onClick={() => onOpen(big, picks, slots[0])}>
              <img src={thumbnailSrc(big.src)} alt="" width={big.w} height={big.h} />
            </button>
            <div className="m-cal-daily-side">
              <p className="m-cal-daily-head">
                <time>{INSPIRATION_DAY_ZH.format(new Date())}</time>
                <span>{bigTitle}</span>
              </p>
              <p className="m-cal-daily-text">{bigText}</p>
              {slots.length > 1 && (
                <div className="m-cal-daily-thumbs">
                  {slots.slice(1).map(at => {
                    const item = picks[at]
                    const info = pickDetails[item.id] ?? item
                    return (
                      <button type="button" className="m-cal-daily-thumb" key={item.id}
                        aria-label={`换成大图：${imageTitle(info) || sourceLabel(info)}`}
                        onClick={() => setOrder(previous => {
                          const next = previous.slice()
                          const swap = previous.indexOf(at)
                          ;[next[0], next[swap]] = [next[swap], next[0]]
                          return next
                        })}>
                        <img src={thumbnailSrc(item.src)} alt="" width={item.w} height={item.h} loading="lazy" />
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        )
      })()}
      {!phone && lead && leadInfo && (
        <div className="m-cal-gallery m-cal-inspiration" data-reveal
          style={{ '--m-cal-lead-ratio': lead.w / lead.h } as CSSProperties}>
          <button type="button" className="m-tile m-cal-inspiration-lead" aria-label={leadTitle || sourceLabel(leadInfo)}
            onClick={() => onOpen(lead, picks, 0)}>
            <img src={thumbnailSrc(lead.src)} alt="" width={lead.w} height={lead.h} loading="lazy" />
          </button>
          <div className="m-cal-inspiration-right">
            <div className="m-cal-inspiration-info">
              <time>{INSPIRATION_DAY.format(new Date())}</time>
              {leadTitle && <h3>{leadTitle}</h3>}
              <span>{sourceLabel(leadInfo)}</span>
              {leadSaved && <span className="m-cal-inspiration-saved">收藏于 {leadSaved}</span>}
            </div>
            {picks.length > 1 && (
              <div className="m-cal-inspiration-more">
                {picks.slice(1).map((item, index) => {
                  const info = pickDetails[item.id] ?? item
                  const title = imageTitle(info)
                  const source = sourceLabel(info)
                  return (
                    <button type="button" className="m-tile" key={item.id} onClick={() => onOpen(item, picks, index + 1)}>
                      <img src={thumbnailSrc(item.src)} alt="" width={item.w} height={item.h} loading="lazy" />
                      <span className="m-tile-cap">
                        {title && <span className="m-tile-title">{title}</span>}
                        <span className="m-tile-src" data-local={source === '本地' || undefined}>{source}</span>
                      </span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="m-cal-row" data-reveal>
        <h2 className="m-cal-h">收藏活动</h2>
        <span className="m-spacer" />
      </div>
      <p className="ds-help m-cal-note">{phone
        ? '点一下方格，看那天收了几张'
        : <>每个方格是一{grain === 'week' ? '周' : '天'}，颜色越深收藏越多；把鼠标停在方格上（手机上点一下）能看到日期和张数。</>}</p>
      <div className="m-cal-chart" data-reveal>
        {/* 没数据时照样画出整年的空格子，不放解释文字；手机只画最近 3 个月 */}
        <Heatmap days={state === 'ready' && shown ? shown : NO_DAYS} grain={grain} spanDays={phone ? PHONE_SPAN_DAYS : undefined} />
      </div>

      <h2 className="m-cal-h" data-reveal>常用导入软件</h2>
      <ul className="m-cal-sources" data-reveal>
        {sourceList.map(s => (
          <li key={s.platform}>
            <img className="m-cal-brand" src={PLATFORM_ICON[s.platform] ?? '/brand/local.png'} alt={PLATFORM_LABEL[s.platform] ?? s.platform} width={46} height={46} />
            <span className="m-cal-name">{PLATFORM_LABEL[s.platform] ?? s.platform}</span>
          </li>
        ))}
      </ul>

      {/* 洞察只列有数的行；一行都没有就连标题一起不画 */}
      {stats && (
        <>
          <h2 className="m-cal-h" data-reveal>洞察</h2>
          <dl className="m-cal-insights" data-reveal>
            <div><dt>近一年合计</dt><dd>{stats.total} 张</dd></div>
            <div><dt>最多的一天</dt><dd>{stats.busiest.count} 张 · {stats.busiest.date.slice(5, 7)}月{stats.busiest.date.slice(8)}日</dd></div>
            <div><dt>最长连续</dt><dd>{stats.longest} 天</dd></div>
            <div><dt>当前连续</dt><dd>{stats.current} 天</dd></div>
          </dl>
        </>
      )}
    </section>
  )
}

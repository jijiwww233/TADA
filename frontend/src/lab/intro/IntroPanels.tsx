import { useState, useEffect, useLayoutEffect, useMemo, useRef, type ReactNode } from 'react'
import { reelLayers } from './phoneHandoff'
import { ArrowRight, Check, Eye, EyeOff, KeyRound } from 'lucide'
import { ICON_REF, type CapsulePresentation } from '../Capsule'
import { Icon } from '../Icon'
import { Fog, HalftoneNumber, HalftoneText } from '../reveal'
import { INTRO_COPY as C, PHASE_MS, PLATFORMS } from './copy'
import { updateWheel } from './motion'
import { SoftText } from './SoftText'
import { IntroGallery } from './IntroGallery'
import { PHONE_ONE_TAKE } from './oneTake'
import { AlignmentGuides } from './AlignmentGuides'
import type { IntroFlow } from './useIntroFlow'
import { REFINE } from '../../main/refine'
import { KeyGuide } from './KeyGuide'
import { useImages } from '../data'
// intro-refine.css 由 ScatterStage 在 intro.css / fable.css 之后引入；这里再引会让它先加载、被前面的样式压掉

function PlatformActions({ flow, index: i, reduced, namesVisible }: { flow: IntroFlow; index: number; reduced: boolean; namesVisible: boolean }) {
  const s = flow.state
  const status = s.platforms[i], current = s.active === i, count = flow.platformCount(i)
  return <div className="intro-platform-actions" key={status}>
    {current && namesVisible && (s.phase === 'unlock' || s.phase === 'platforms') && status === 'idle' && (
      <div className="intro-platform-choices">
        <button className="intro-connect" type="button" disabled={flow.real && i === 0} onClick={() => flow.connect(i)}>{C.connect}</button>
        <button className="intro-link" type="button" onClick={() => flow.skip(i)}>{C.skip}</button>
      </div>
    )}
    {current && i === 2 && flow.importError && <SoftText>{flow.importError}</SoftText>}
    {status === 'connecting' && <SoftText>{i === 2 ? C.localWaiting : C.waiting}</SoftText>}
    {status === 'connected' && (
      <span className="intro-platform-success" role="status">
        <span className="sr-only">{count === undefined ? C.connected : C.platformCount(count)}</span>
        <Icon icon={Check} size={12} className="intro-key-check" />
        <span aria-hidden="true">
          {C.connected}{count !== undefined && <> · <HalftoneNumber value={count} off={reduced} duration={750} /> 张</>}
        </span>
      </span>
    )}
    {status === 'skipped' && <SoftText>{C.skipped}</SoftText>}{status === 'cooldown' && <SoftText>{C.cooldown}</SoftText>}
  </div>
}

/** 手机选平台页右侧的竖向胶片卷：每个平台 3 张卡；位置、景深、模糊都由 updateWheel 按转盘的 current 每帧算 */
function PhoneReel() {
  const { items, cards: gallery } = useImages()
  // 一份 18 张（6 个平台 × 3）。排法见 phoneHandoff.ts：停在「下一步」时露出来的那 11 张，
  // 正好是整理页环上 / 最后一页转盘上的前 11 张图，后面两段动画交接时图对得上。
  const layers = useMemo(() => reelLayers(items, gallery), [items, gallery])
  if (!items.length) return null
  // 一镜到底：右栏只留一个空的定位框（ScatterStage 量它的中心，画布上那一叠卡排成卷轴就排在这里），不再加载另一批图
  if (PHONE_ONE_TAKE) return <div className="intro-phone-reel" aria-hidden="true"><div className="intro-phone-reel-track" /></div>
  const cards = layers.map(layer => items[layer])
  // 放三份，从任何一个平台滚到另一个时都有卡片经过（像老虎机）；停的位置永远在中间那一份里
  return (
    <div className="intro-phone-reel" aria-hidden="true">
      <div className="intro-phone-reel-track">
        {[...cards, ...cards, ...cards].map((item, i) => <img key={i} data-reel-index={i} src={item.src} alt="" draggable={false} />)}
      </div>
    </div>
  )
}

function KeyCard({ flow, phone }: { flow: IntroFlow; phone: boolean }) {
  const guide = useRef<HTMLDialogElement>(null)
  const [visible, setVisible] = useState(false)
  const s = flow.state
  return <><form id="intro-key-form" className={`intro-key-form ${s.keyStatus === 'closing' ? 'is-closing' : ''}`} aria-label={C.keyPanelAria} onSubmit={e => { e.preventDefault(); flow.verify() }}>
    <label className="intro-key-label" htmlFor="intro-api-key"><SoftText>{C.keyLabel}</SoftText></label>
    <div className="intro-key-input">
      <input id="intro-api-key" type={visible && !flow.real ? 'text' : 'password'} aria-label={C.keyAria} autoComplete="off" spellCheck={false} value={flow.draft} placeholder={C.keyPlaceholder} disabled={s.keyStatus === 'closing'}
        // 引导版：点进输入框不再自动展开「接口地址 / 模型名」——第一次来的人看到会以为都得填；改成下面的「高级设置」手动展开
        onFocus={() => { if (!REFINE) flow.setAdvanced(true) }} onChange={e => flow.setDraft(e.target.value)} />
      <button type="button" aria-label={visible ? C.hideKey : C.showKey} aria-pressed={visible && !flow.real} disabled={flow.real} onClick={() => setVisible(v => !v)}><Icon icon={visible ? EyeOff : Eye} size={18} /></button>
    </div>
    <div className={`intro-advanced ${flow.advanced ? 'is-open' : ''}`}><div>
      <label><SoftText key={String(flow.advanced)} delay={600}>{C.endpoint}</SoftText><input aria-label={C.endpointAria} placeholder={C.endpointPlaceholder} disabled={flow.real} /></label>
      <label><SoftText key={String(flow.advanced)} delay={600}>{C.model}</SoftText><input aria-label={C.modelAria} placeholder={C.modelPlaceholder} disabled={flow.real} /></label>
    </div></div>
    {s.error && <p className={s.error === C.emptyKey ? 'intro-key-error is-immediate' : 'intro-key-error'} role="status"><SoftText key={s.error}>{s.error}</SoftText></p>}
    <button type="submit" className="intro-key-verify" disabled={s.keyStatus === 'closing'}><SoftText>{C.verify}</SoftText></button>
    <button type="button" className="intro-key-skip" onClick={flow.skipKey}><SoftText>先跳过</SoftText></button>
    <p className="intro-key-footer intro-key-links">
      <span>{phone ? '还没有密钥？' : '还没有 Key？'}{phone
        ? <button type="button" onClick={() => guide.current?.showModal()}>看注册教程</button>
        : <a href="/guides/api-key.pdf" target="_blank" rel="noreferrer">看注册教程</a>}</span>
      <span aria-hidden>·</span>
      <button type="button" aria-expanded={flow.advanced} onClick={() => flow.setAdvanced(!flow.advanced)}>
        {flow.advanced ? C.advancedHide ?? '收起高级设置' : C.advancedToggle ?? C.advanced}
      </button>
    </p>
  </form>{phone && <KeyGuide dialog={guide} />}</>
}

/** 手机公网体验版的 Key 页：不让填 Key，只说明已经接好；教程是能直接打开的网页（public/guides/api-key.html） */
function PublicKeyCard({ flow }: { flow: IntroFlow }) {
  return <div className="intro-key-form intro-key-public" aria-label={C.keyPanelAria}>
    <p className="intro-key-public-text">体验版已经帮你接好了 AI，不用填 Key。</p>
    <a className="intro-key-public-link" href="/guides/api-key.html" target="_blank" rel="noreferrer">自己部署时怎么申请 Key →</a>
    <button type="button" className="intro-key-verify" disabled={flow.state.keyStatus === 'closing'} onClick={flow.skipKey}><SoftText>下一步</SoftText></button>
  </div>
}

/** The same glass shell, stack icon and clay action remain present in every phase. */
export function introPresentation(flow: IntroFlow, reduced: boolean, phone = false): CapsulePresentation | undefined {
  const s = flow.state
  if (s.phase === 'home' || s.phase === 'ready') return undefined
  const form = s.phase === 'key' && ['idle', 'error', 'closing'].includes(s.keyStatus)
  const connected = s.platforms.filter(p => p === 'connected').length
  const key = s.phase === 'key' || s.phase === 'unlock'
  const checked = s.keyStatus === 'verified'
  const totals = s.phase === 'reveal' || s.phase === 'gather'
  // 手机体验版选平台页：底部胶囊不报「已连接 0/5」（体验版根本连不了），改成一句话 + 箭头直接进下一步
  const publicPick = flow.publicDemo && s.phase === 'platforms'
  return {
    icon: ICON_REF, surface: form ? 'card' : 'status',
    actionLabel: form ? C.verifyAction : publicPick ? C.all : checked ? C.connected : C.continue,
    // 卡片右上角的钥匙只是身份提示，不是按钮；真正的提交是下面的「验证」。
    actionDecorative: form,
    action: <Icon icon={form ? KeyRound : checked && key ? Check : ArrowRight} size={18} strokeWidth={form ? 1.25 : 1.5} className={checked && key ? 'intro-key-check' : undefined} />,
    onAction: publicPick ? () => flow.select(PLATFORMS.length) : !form && s.phase === 'key' && checked ? flow.skipKey : undefined,
    content: form ? (flow.publicDemo ? <PublicKeyCard flow={flow} /> : <KeyCard flow={flow} phone={phone} />) : publicPick ? <SoftText>看看就好，点箭头进下一步</SoftText> : key ?
      (s.keyStatus === 'checking' || s.keyStatus === 'contracting' && s.keyAction === 'verify') ? <span className="intro-checking" aria-label={C.verifying} role="status"><i /><i /><i /></span> :
      checked ? <span className="intro-key-success" role="status">{C.connected}{flow.restartRequired && <><br /><small>重启后生效</small></>}</span> :
      <SoftText>{flow.publicDemo ? '体验版已接好 AI' : C.keySkipped}</SoftText> : totals ?
      flow.totals && <span className="intro-totals">
        <HalftoneNumber value={flow.totals.collections} off={reduced} duration={1500} className="font-semibold" /> {C.collections} →{' '}
        <HalftoneNumber value={flow.totals.references} off={reduced} delay={400} duration={1500} className="font-semibold" /> {C.references}
      </span> :
      <SoftText key={connected} className="intro-status-count">{C.connected} {connected} / {PLATFORMS.length}</SoftText>,
    disabled: !form && !(s.phase === 'key' && checked) && !publicPick,
  }
}

export function IntroPanels({ flow, reduced, topbar, setTopbar, controls, starfield, setStarfield, clean = false, phone = false }: { clean?: boolean; phone?: boolean; flow: IntroFlow; reduced: boolean; topbar:'A'|'B'|'C'; setTopbar:(mode:'A'|'B'|'C')=>void; controls?:ReactNode;starfield:boolean;setStarfield:(value:boolean)=>void }) {
  const s = flow.state
  const topbarRef=useRef<HTMLDivElement>(null)
  const mainPose=useRef<HTMLElement>(null)
  useLayoutEffect(()=>{
    const bar=topbarRef.current,screen=bar?.closest<HTMLElement>('.home-screen'),root=bar?.closest<HTMLElement>('[data-home-stage]')
    if(!bar||!screen||!root)return
    const topline = bar.querySelector('.home-topline')
    const measure=()=>{
      const frame=screen.getBoundingClientRect()
      root.style.setProperty('--intro-topbar-bottom',`${bar.getBoundingClientRect().bottom-frame.top}px`)
      if (topline) {
        const padding = parseFloat(getComputedStyle(bar).paddingRight)
        const inset = Math.max(padding, frame.right - topline.getBoundingClientRect().right)
        root.style.setProperty('--intro-progress-inset', `${inset}px`)
      }
      if(mainPose.current)root.style.setProperty('--intro-ready-cap-y',`${mainPose.current.getBoundingClientRect().top-frame.top-frame.height/2}px`)
    }
    const observer=new ResizeObserver(measure);observer.observe(bar);observer.observe(screen)
    if (topline) observer.observe(topline)
    bar.addEventListener('scroll', measure, { passive: true })
    measure();document.fonts.ready.then(measure)
    return () => {
      observer.disconnect()
      bar.removeEventListener('scroll', measure)
    }
  },[])
  const [guides,setGuides]=useState(false)
  const platformNames=s.platformEntry==='wheel'||s.platformEntry==='cards'
  const platformTitle=s.platformEntry!=='capsule'
  const [readyTitle,setReadyTitle]=useState(false)
  const isReady=s.phase==='ready'
  const progressStep = s.phase === 'key' ? 0
    : s.phase === 'unlock' || s.phase === 'platforms' ? 1
    : 2
  const firstStep = 0
  useEffect(()=>{setReadyTitle(false);if(!isReady)return;const t=setTimeout(()=>setReadyTitle(true),reduced?0:650);return()=>clearTimeout(t)},[isReady,s.run,reduced])
  const [titleDone, setTitleDone] = useState(-1)
  const [keyTitle, setKeyTitle] = useState(false)
  const isKey = s.phase === 'key'
  useEffect(()=>{if(isKey)setKeyTitle(false)},[isKey,s.run])
  useEffect(()=>{
    if(!isKey||keyTitle)return
    const timer=setTimeout(()=>setKeyTitle(true),reduced?0:PHASE_MS.keyTitle)
    return()=>clearTimeout(timer)
  },[isKey,s.run,reduced,keyTitle,flow.advanced])
  const platforms = ['unlock','platforms','clearing','reveal','gather'].includes(s.phase)
  const panel = useRef<HTMLElement>(null)
  useLayoutEffect(() => { if (panel.current) updateWheel(panel.current, s.active, innerWidth, innerHeight) }, [platforms,s.run])
  const progress = (
      <ol className="intro-progress" aria-label={C.progressLabel}>
        {C.progressSteps.slice(firstStep).map((label, index) => (
          <li key={label} tabIndex={0} aria-current={index + firstStep === progressStep ? 'step' : undefined}>
            <span className="intro-progress-dot" aria-hidden="true" />
            <span className="intro-progress-label">第 {index + 1} 步 · {label}</span>
          </li>
        ))}
      </ol>
  )
  const filePicker = flow.real && <input ref={flow.filePicker} type="file" hidden multiple
        accept="image/*,.heic,.heif" onChange={event => {
          if (event.currentTarget.files) flow.chooseFiles(event.currentTarget.files)
          event.currentTarget.value = ''
        }} />
  return <>
    <div ref={topbarRef} className="intro-topbar">
      <header className="intro-masthead" aria-hidden />
      {clean ? <>
        <span className="intro-brand">TADA</span>
        <div className="intro-clean-tools">
          <button type="button" aria-pressed={s.demo} onClick={() => flow.setDemo(!s.demo)}>{phone && s.demo ? '暂停' : C.autoplay}</button>
          <span aria-hidden>·</span>
          <button type="button" onClick={flow.restart}>{C.replay}</button>
          <span aria-hidden>·</span>
          <button type="button" onClick={() => { window.location.href = REFINE ? '/main' : '/main?refine=0' }}>{C.skipIntro}</button>
        </div>
      </> : <>
      <div className="home-topline" aria-hidden><SoftText>小红书</SoftText><SoftText>Pinterest</SoftText><SoftText>站酷</SoftText><SoftText>花瓣</SoftText><SoftText>Visual ref</SoftText></div>
      {controls&&<div className="home-switches">{controls}</div>}
    <div className="intro-tools">
      <div className="intro-console-main">
        <label><input type="checkbox" checked={s.demo} onChange={e=>flow.setDemo(e.target.checked)} /><SoftText>{C.demo}</SoftText></label>
        <button type="button" onClick={flow.debugKey}><SoftText>{C.debugKey}</SoftText></button>
        <button type="button" onClick={flow.restart}><SoftText>{C.replay}</SoftText></button>
        <label>{C.page}<select aria-label={C.page} value={s.phase==='home'?'home':s.phase==='key'?'key':s.phase==='ready'?'ready':'platforms'} onChange={e=>flow.jump(e.target.value as 'home'|'key'|'platforms'|'ready')}>{(['home','key','platforms','ready'] as const).map((phase,i)=><option key={phase} value={phase}>{C.pages[i]}</option>)}</select></label>
      </div>
      <div className="intro-console-aux">
        <label><input type="checkbox" checked={guides} onChange={e=>setGuides(e.target.checked)} />{C.align}</label>
        <label>{C.topbar}<select aria-label={C.topbar} value={topbar} onChange={e=>setTopbar(e.target.value as 'A'|'B'|'C')}><option value="A">{C.topbarA}</option><option value="B">{C.topbarB}</option><option value="C">{C.topbarC}</option></select></label>
        {s.phase==='key'&&<label>{C.demoResult}<select aria-label={C.demoResult} value={flow.fail?'fail':'success'} onChange={e=>flow.setFail(e.target.value==='fail')}><option value="success">{C.success}</option><option value="fail">{C.failure}</option></select></label>}
        {s.phase==='key'&&s.keyStatus==='verified'&&<><button type="button" onClick={flow.changeKey}>{C.change}</button><button type="button" onClick={flow.skipKey}>{C.continue}</button></>}
        {s.phase==='platforms'&&<label>{C.cardField}<select aria-label={C.cardField} value={starfield?'stars':'classic'} onChange={e=>setStarfield(e.target.value==='stars')}><option value="classic">{C.classicField}</option><option value="stars">{C.starField}</option></select></label>}
        {s.phase==='platforms'&&flow.platformSyncAvailable&&!flow.real&&<button type="button" onClick={flow.cooldown}>{s.platforms[0]==='cooldown'?C.cooldownOff:C.cooldownOn}</button>}
      </div>
    </div>
      </>}
    </div>
    {s.phase !== 'home' && progress}
    <i ref={mainPose} className="intro-main-pose" aria-hidden />
    {keyTitle && (s.phase === 'key' || s.phase === 'unlock') && <div className={`intro-key-title intro-center-title ${s.phase === 'unlock' ? 'is-leaving' : ''}`}>
      <HalftoneText key={s.run} off={reduced} ms={1500} onDone={() => setTitleDone(s.run)}><h2><Fog on={titleDone === s.run && !reduced}><span className="intro-title-line">{C.keyTitle.prefix}<strong>{C.keyTitle.emphasis}</strong></span><span className="intro-title-line">{C.keyTitle.second}</span></Fog></h2></HalftoneText>
    </div>}
    {phone && s.phase === 'platforms' && (
      <section className="intro-phone-platform" aria-labelledby="intro-phone-platform-title">
        <div className="intro-phone-platform-body">
          <div className="intro-phone-platform-content">
            <h2 id="intro-phone-platform-title">{C.platformHint}</h2>
            <p>{flow.platformSyncAvailable
              ? '选一个收藏来源，登录自己的账号或从本地导入。'
              : '体验版连不了你的平台账号，这一页看看就好，直接点下一步。想同步自己的收藏，要在自己电脑上装 TADA。'}</p>
            {filePicker}
            {/* 左：电脑版那种竖着转的平台名转盘（滚动/拖动切换，带惯性和吸附）；右：有景深的卡片带 */}
            <div className="intro-phone-platform-cols">
              <div className="intro-phone-wheel">
                <div className="intro-phone-wheel-list" role="navigation" aria-label={C.sourceAria}>
                  {[...PLATFORMS.map(({ name }) => name), C.all].map((name, i) => {
                    const current = s.active === i
                    const status = s.platforms[i]
                    // 体验版：不出现「连接」，任何一行都不带连接状态。
                    // 点「下一步」也只是转过去（不直接进整理页）：卷轴先停到最后一格，1 秒后再卷进整理页（见 useIntroFlow）
                    return <div key={name} data-platform-index={i} className={`intro-phone-wheel-row${current ? ' is-current' : ''}${status && !flow.publicDemo ? ' is-' + status : ''}`}>
                      <button className="intro-phone-source" type="button" aria-pressed={current}
                        onClick={() => flow.select(i)}>
                        <span className="intro-current-arrow" aria-hidden>→</span>
                        <span className="intro-phone-wheel-name">{name}</span>
                      </button>
                      {current && i < PLATFORMS.length && !flow.publicDemo && <PlatformActions flow={flow} index={i} reduced={true} namesVisible />}
                    </div>
                  })}
                </div>
                <div className="intro-phone-wheel-ruler" aria-hidden="true">
                  {Array.from({ length: 31 }, (_, i) => <i key={i} data-ruler-index={i} />)}
                </div>
              </div>
              <PhoneReel />
            </div>
          </div>
        </div>
      </section>
    )}
    {!phone && platforms && <section ref={panel} key={s.run} data-entry={s.platformEntry} className={`intro-platform-panel ${['clearing','reveal','gather'].includes(s.phase) ? 'is-clearing' : ''}`} aria-label={C.platformAria} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); if (s.active === 2) flow.dropLocal(e.dataTransfer) }}>
      {filePicker}
      <div className="intro-platform-group">
      <div className="intro-platform-hint">
        {platformTitle && <HalftoneText off={reduced} ms={900} onDone={flow.platformTitleDone}><h2>{C.platformHint}</h2></HalftoneText>}
        {s.phase === 'platforms' && <p className="intro-platform-note">{flow.platformSyncAvailable
          ? '平台同步需要登录你自己的账号，请在本机版使用。'
          : '这里是大家共用的体验版，连不了你的小红书。想把收藏一键搬过来，请在自己电脑上打开 TADA。'}</p>}
      </div>
      <div className="intro-ruler" aria-hidden>{Array.from({ length: 31 }, (_, i) => <i key={i} data-ruler-index={i} className={Math.abs(i - s.active * 6) < 2 ? 'is-current' : ''} />)}</div>
      <div className="intro-platform-list" role="navigation" aria-label={C.sourceAria} onKeyDown={e => {
        if (s.phase !== 'platforms') return
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); flow.select(s.active + (e.key === 'ArrowDown' ? 1 : -1)) }
      }}>
        {[...PLATFORMS.map(p => p.name), C.all].map((name, i) => {
          const status = s.platforms[i], current = s.active === i
          // 8a：每个平台名后面写清楚状态——未连接 / 连接中 / 已连接 / 已跳过 / 冷却中；当前选中的前面加「当前」
          // 不用框，只用小圆点 + 文字区分：当前 = 陶土橙点 + 黑字；已连接 = 浅棕点 + 「已连接」；未连接 = 只有浅色字
          const stateLabel = i === PLATFORMS.length || (!flow.platformSyncAvailable && i !== 2) ? null
            : status === 'connected' ? '已连接' : status === 'connecting' ? '连接中' : status === 'skipped' ? '已跳过'
            : status === 'cooldown' ? '冷却中' : '未连接'
          const stateDot = current || status === 'connected'
          return <div key={name} data-platform-index={i} className={`intro-platform-row ${current ? 'is-current' : ''} ${status ?? 'all'}`}>
            <button type="button" className="intro-platform-pick" tabIndex={current ? 0 : -1} aria-current={current ? 'step' : undefined} disabled={s.phase !== 'platforms'} onClick={() => flow.select(i,i===5)}
              aria-label={stateLabel ? `${name}，${stateLabel}${current ? '，当前选中' : ''}` : undefined}>
              <span className="intro-current-arrow" aria-hidden>→</span>
              <span className="intro-platform-name">{platformNames && (i===0 ? <HalftoneText off={reduced} ms={900}><span>{name}</span></HalftoneText> : <SoftText>{name}</SoftText>)}</span>
              {REFINE && platformNames && stateLabel && (
                <span className={`intro-platform-state is-${status ?? 'idle'}${current ? ' is-current' : ''}`} aria-hidden="true">
                  {stateDot && <i className="intro-platform-dot" />}
                  {current ? `当前 · ${stateLabel}` : stateLabel}
                </span>
              )}
            </button>

          </div>
        })}
      </div>
      <div className="intro-status-rail">{PLATFORMS.map((_,i)=>{if(!flow.platformSyncAvailable && i !== 2)return null
        return <div key={i} data-status-index={i} className="intro-status-row"><PlatformActions flow={flow} index={i} reduced={reduced} namesVisible={platformNames} /></div>})}</div>
      </div>
    </section>}
    {(s.phase === 'reveal' || s.phase === 'gather') && (
      <p className={`intro-totals-caption ${s.phase === 'gather' ? 'is-leaving' : ''}`}>
        <SoftText>{C.totalsNote}</SoftText>
      </p>
    )}
    {(s.phase === 'gather' || s.phase === 'ready') && <div className={`intro-main ${s.phase === 'ready' ? 'is-ready' : ''}`}>
      {s.phase==='ready'&&<IntroGallery reduced={reduced} phone={phone} />}
      {(readyTitle || (phone && isReady)) && <div className="intro-ready intro-center-title"><HalftoneText off={reduced || phone} ms={1100}><h2>{C.readyPrefix}<strong>{C.readyEmphasis}</strong>{C.readySuffix}</h2></HalftoneText></div>}

    </div>}
    {/* 8b：「进入素材库」是这一页唯一的主要按钮：实心深陶土色、白字、带箭头，不再跟着文字慢慢淡入 */}
    {s.phase==='ready'&&<button type="button" className="intro-enter ds-intro-primary" onClick={() => { window.location.href = REFINE ? '/main' : '/main?refine=0' }}>{C.ready}<span aria-hidden="true"> →</span></button>}
    {guides && <AlignmentGuides phase={s.phase} />}
  </>
}

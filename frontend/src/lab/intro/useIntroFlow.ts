import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { useDemoMode } from '../../api/mode'
import { useWiringApi, useWiringImport } from '../../api/useWiring'
import type { LibraryCounts, PlatformCount } from '../../api/wiring'
import { getSyncStatus, startSync } from '../../api'
import { collectDroppedFiles, collectInputFiles } from '../../localImport/collect'
import type { DropData } from '../../localImport/collect'
import { DEMO_MS, INTRO_COPY, LIBRARY_COUNTS, PHASE_MS, PLATFORMS } from './copy'
import { PHONE_GATHER_MS, PHONE_LEAVE_MS } from './phoneHandoff'

export type IntroPhase = 'home' | 'key' | 'unlock' | 'platforms' | 'clearing' | 'reveal' | 'gather' | 'ready'
export type PlatformStatus = 'idle' | 'connecting' | 'connected' | 'skipped' | 'cooldown'
type KeyStatus = 'idle' | 'closing' | 'contracting' | 'checking' | 'error' | 'verified'
export type IntroState = { phase: IntroPhase; platformEntry: PlatformEntry; demo: boolean; run: number; active: number; platforms: PlatformStatus[]; keyStatus: KeyStatus; keyAction: 'verify' | 'skip'; last4: string; error: string }
export type PlatformEntry = 'capsule' | 'title' | 'wheel' | 'cards'
type IntroPage = 'home'|'key'|'platforms'|'ready'
type Action = {type:'entry';step:PlatformEntry} | {type:'jump';phase:IntroPage} | { type: 'reset'; demo: boolean } | { type: 'phase'; phase: IntroPhase } | { type: 'demo'; value: boolean } |
  { type: 'select'; index: number; activate?: boolean } | { type: 'platform'; index: number; status: PlatformStatus } |
  { type: 'key'; status: KeyStatus; intent?: 'verify' | 'skip'; last4?: string; error?: string }
const initial = (): IntroState => ({ phase: 'home', platformEntry: 'capsule', demo: false, run: 0, active: 0, platforms: Array(5).fill('idle'), keyStatus: 'idle', keyAction: 'verify', last4: '', error: '' })
export function introReducer(s: IntroState, a: Action): IntroState {
  if (a.type === 'reset') return { ...initial(), demo: a.demo, run: s.run + 1, last4: s.keyStatus === 'verified' ? s.last4 : '', keyStatus: s.keyStatus === 'verified' ? 'verified' : 'idle' }
  if(a.type==='jump')return {...initial(),phase:a.phase==='platforms'?'unlock':a.phase,run:s.run+1}
  if (a.type === 'demo') return { ...s, demo: a.value }
  if(a.type==='entry')return {...s,platformEntry:a.step}
  if (a.type === 'phase') return { ...s, phase: a.phase }
  if (a.type === 'key') return { ...s, keyStatus: a.status, keyAction: a.intent ?? s.keyAction, last4: a.last4 ?? s.last4, error: a.error ?? '' }
  if (a.type === 'select') return { ...s, active: ((a.index%6)+6)%6, phase: ((a.index%6)+6)%6 === 5 && a.activate ? 'clearing' : s.phase }
  if (a.type === 'platform') return { ...s, platforms: s.platforms.map((p, i) => i === a.index ? a.status : p) }
  return s
}

const remotePlatforms = ['xhs', 'pinterest', null, 'zcool', 'huaban'] as const
const platformSyncAvailable = import.meta.env.VITE_PUBLIC_DEMO !== '1'

/** Keep the same animation phases while supplying either rehearsal or local backend data. */
export function useIntroFlow(reduced: boolean, phone = false) {
  /** 手机 + 公网体验版：Key 页照样出现（和电脑版流程一致），但只说明「已经接好 AI」，不让填；选平台页不出现「连接」 */
  const publicDemo = phone && !platformSyncAvailable
  const [state, dispatch] = useReducer(introReducer, undefined, initial)
  const [draft, setDraft] = useState('')
  const [fail, setFail] = useState(false)
  const [advanced, setAdvanced] = useState(false)
  const [demoMode] = useDemoMode()
  const real = !demoMode && !state.demo
  const api = useWiringApi(!real)
  const [counts, setCounts] = useState<LibraryCounts | null>(null)
  const [platformCounts, setPlatformCounts] = useState<PlatformCount[]>([])
  const [restartRequired, setRestartRequired] = useState(false)
  const [importError, setImportError] = useState('')
  const submittedKey = useRef('')
  const filePicker = useRef<HTMLInputElement>(null)
  const localImport = useWiringImport(api)
  useEffect(() => {
    if (!real || !['unlock', 'platforms', 'clearing', 'reveal'].includes(state.phase)) return
    let live = true
    api.getLibraryCounts().then(value => { if (live) setCounts(value) }, () => { if (live) setCounts(null) })
    api.getPlatformCounts().then(value => { if (live) setPlatformCounts(value) }, () => { if (live) setPlatformCounts([]) })
    return () => { live = false }
  }, [api, real, state.phase, localImport.progress.added])
  useEffect(() => {
    if (real && localImport.status === 'done') {
      dispatch({ type: 'platform', index: 2, status: localImport.progress.added || localImport.progress.duplicates ? 'connected' : 'idle' })
      if (!localImport.progress.added && !localImport.progress.duplicates) setImportError('没有可导入的图片')
    }
    if (real && localImport.status === 'error') dispatch({ type: 'platform', index: 2, status: 'idle' })
  }, [real, localImport.status, localImport.progress.added, localImport.progress.duplicates])
  useEffect(() => {
    if (publicDemo || !real || state.phase !== 'key') return
    let live = true
    api.getKeyStatus().then(result => {
      if (live && result.configured && stateRef.current.keyStatus === 'idle') {
        setRestartRequired(!!result.restart_required)
        dispatch({ type: 'key', status: 'verified', last4: result.last4 ?? '' })
      }
    }, () => {})
    return () => { live = false }
  }, [api, real, state.phase, state.run, publicDemo])
  useEffect(() => {
    if (!platformSyncAvailable || !real || !['unlock', 'platforms'].includes(state.phase)) return
    let live = true
    const refresh = async () => {
      const lock = await api.getCooldown()
      if (!live) return
      if (lock.blocked) dispatch({ type: 'platform', index: 0, status: 'cooldown' })
      await Promise.all(remotePlatforms.map(async (platform, index) => {
        if (!platform || (platform === 'xhs' && lock.blocked)) return
        const value = await getSyncStatus(platform).catch(() => null)
        if (!live || !value || stateRef.current.platforms[index] === 'skipped') return
        if (value.connection_state === 'connected') dispatch({ type: 'platform', index, status: 'connected' })
        else if (!value.busy) dispatch({ type: 'platform', index, status: 'idle' })
      }))
    }
    void refresh()
    const timer = setInterval(refresh, 2000)
    return () => { live = false; clearInterval(timer) }
  }, [api, real, state.phase, state.run])
  const pending = useRef(new Map<number, ReturnType<typeof setTimeout>>())
  const stateRef = useRef(state); stateRef.current = state
  /**
   * 手机换页（第一页 → Key 页、Key 页 → 选平台页）的柔和转场：先把 leaving 置上，旧页面淡出并轻微上移
   * （intro-phone.css 的 [data-leaving]，PHONE_LEAVE_MS），到时间再真正换页；新页面由 CSS 淡入并从下方轻微上移。
   * leaving 归零和换页在同一个定时器里一起提交，中间不会露出一帧旧页面。电脑版、减少动效时直接换页。
   */
  const [leaving, setLeaving] = useState(false)
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** 已经发起换页、新页面还没提交：舞台每帧都会调 enterKey，这期间不能再发起第二次（否则新页面一出来又被淡出） */
  const leavePending = useRef(false)
  const go = useCallback((action: Action) => {
    if (!phone || reduced) { dispatch(action); return }
    if (leavePending.current) return
    leavePending.current = true
    setLeaving(true)
    leaveTimer.current = setTimeout(() => { leaveTimer.current = null; setLeaving(false); dispatch(action) }, PHONE_LEAVE_MS)
  }, [phone, reduced])
  useEffect(() => { if (!leaveTimer.current) leavePending.current = false }, [state.phase, state.run])
  const cancelLeave = () => { if (leaveTimer.current) clearTimeout(leaveTimer.current); leaveTimer.current = null; leavePending.current = false; setLeaving(false) }
  useEffect(() => () => { if (leaveTimer.current) clearTimeout(leaveTimer.current) }, [])
  const enterKey = useCallback(() => go({ type: 'phase', phase: 'key' }), [go])
  const select = useCallback((index: number, activate=false) => dispatch({ type: 'select', index, activate }), [])
  useEffect(()=>{
    if(state.phase!=='platforms'||state.active!==5)return
    const id=setTimeout(()=>dispatch({type:'phase',phase:'clearing'}),1000)
    return()=>clearTimeout(id)
  },[state.phase,state.active,state.run])
  const connect = useCallback((index: number) => {
    if (!platformSyncAvailable && index !== 2) return
    if (stateRef.current.platforms[index] !== 'idle') return
    if (real) {
      if (index === 2) { filePicker.current?.click(); return }
      const platform = remotePlatforms[index]
      if (!platform || platform === 'xhs') return
      dispatch({ type: 'platform', index, status: 'connecting' })
      void startSync(platform, 'login').catch(() => dispatch({ type: 'platform', index, status: 'idle' }))
      return
    }
    dispatch({ type: 'platform', index, status: 'connecting' })
  }, [real])
  const startLocal = (files: File[]) => {
    setImportError('')
    dispatch({ type: 'platform', index: 2, status: 'connecting' })
    void localImport.start(files)
  }
  const dropLocal = (data: DropData) => {
    if (!real) { connect(2); return }
    void collectDroppedFiles(data).then(files => startLocal(files.map(item => item.file)), () => setImportError('无法读取文件夹'))
  }
  const verify = useCallback(() => {
    if (draft.trim().length < 8) { dispatch({ type: 'key', status: 'error', error: INTRO_COPY.emptyKey }); return }
    setRestartRequired(false)
    if (real) submittedKey.current = draft.trim()
    dispatch({ type: 'key', status: 'closing', intent: 'verify', last4: real ? '' : draft.slice(-4) })
  }, [draft, real])
  useEffect(() => {
    if (state.phase !== 'key' || state.keyStatus !== 'closing') return
    const id = setTimeout(() => dispatch({ type: 'key', status: 'contracting' }), reduced ? 0 : PHASE_MS.close)
    return () => clearTimeout(id)
  }, [state.phase, state.keyStatus, state.keyAction, reduced])
  useEffect(() => {
    if (state.phase !== 'key' || state.keyStatus !== 'contracting') return
    const id = setTimeout(() => state.keyAction === 'skip' ? go({ type: 'phase', phase: 'unlock' }) : dispatch({ type: 'key', status: 'checking' }), reduced ? 0 : PHASE_MS.contract)
    return () => clearTimeout(id)
  }, [state.phase, state.keyStatus, state.keyAction, reduced, go])
  useEffect(() => {
    if (state.phase !== 'key' || state.keyStatus !== 'checking') return
    if (real) {
      let live = true
      const candidate = submittedKey.current
      submittedKey.current = ''
      setDraft('')
      api.verifyAndSaveKey(candidate).then(result => {
        if (!live) return
        setRestartRequired(!!result.restart_required)
        dispatch({ type: 'key', status: 'verified', last4: result.last4 ?? '' })
      }, () => { if (live) dispatch({ type: 'key', status: 'error', last4: '', error: INTRO_COPY.keyError }) })
      return () => { live = false }
    }
    const id = setTimeout(() => {
      dispatch(fail ? { type: 'key', status: 'error', last4: '', error: INTRO_COPY.keyError } : { type: 'key', status: 'verified' })
      if (!fail) setDraft('')
    }, state.demo ? DEMO_MS.verify : PHASE_MS.verify)
    return () => clearTimeout(id)
  }, [state.phase, state.keyStatus, state.demo, fail, real, api])
  // Manually validated keys also continue after the check. Previously entered keys wait for “继续”.
  const checkingBefore = useRef(false)
  useEffect(() => {
    if (state.keyStatus === 'checking') checkingBefore.current = true
    if (state.keyStatus === 'error' || state.keyStatus === 'idle') checkingBefore.current = false
    if (state.phase === 'key' && state.keyStatus === 'verified' && (checkingBefore.current || state.demo)) {
      checkingBefore.current = false
      const id = setTimeout(() => go({ type: 'phase', phase: 'unlock' }), PHASE_MS.check)
      return () => clearTimeout(id)
    }
  }, [state.phase, state.keyStatus, state.demo, go])
  useEffect(() => {
    const next = { clearing: 'reveal', reveal: 'gather', gather: 'ready' } as const
    if (!(state.phase in next)) return
    const phase = state.phase as keyof typeof next
    // 手机：没有电脑版那块平台面板要退场，clearing 直接过；卷轴在 reveal 里卷成环。gather 是「环滑下去变转盘」，0.56 秒
    const delay = phase === 'clearing' ? (phone ? 0 : PHASE_MS.clear)
      : phase === 'gather' && phone ? PHONE_GATHER_MS
      : phase === 'reveal' && state.demo ? DEMO_MS.reveal
      : PHASE_MS[phase]
    const id = setTimeout(() => dispatch({ type: 'phase', phase: next[phase] }), reduced && !(phone && phase === 'reveal') ? 0 : delay)
    return () => clearTimeout(id)
  }, [state.phase, state.demo, reduced, phone])
  // Entry is sequenced from the title's actual ink completion, not overlapping clocks.
  useEffect(()=>{
    if(state.phase!=='unlock')return
    if (phone) {
      // 进来就停在第一个平台（以前体验版会先选到「本地相册」，转盘一进页就自己滑两格）
      dispatch({type:'entry', step:'cards'})
      dispatch({type:'phase', phase:'platforms'})
      return
    }
    const delay = state.platformEntry === 'capsule' ? (state.demo ? DEMO_MS.platformTitle : PHASE_MS.platformTitle)
      : state.platformEntry === 'wheel' ? PHASE_MS.platformNames
      : state.platformEntry === 'cards' ? (state.demo ? DEMO_MS.ejectDuration : PHASE_MS.ejectDuration)
      : null
    if(delay===null)return
    const id=setTimeout(()=>dispatch(state.platformEntry==='cards'?{type:'phase',phase:'platforms'}:{type:'entry',step:state.platformEntry==='capsule'?'title':'cards'}),reduced?0:delay)
    return()=>clearTimeout(id)
  },[state.phase,state.platformEntry,state.demo,reduced,state.run,phone])
  useEffect(() => {
    if (real) return
    state.platforms.forEach((status, i) => {
      if (status === 'connecting' && !pending.current.has(i)) pending.current.set(i, setTimeout(() => {
        pending.current.delete(i); dispatch({ type: 'platform', index: i, status: 'connected' })
      }, state.demo ? DEMO_MS.connect : PHASE_MS.connect))
      else if (status !== 'connecting' && pending.current.has(i)) { clearTimeout(pending.current.get(i)); pending.current.delete(i) }
    })
  }, [state.platforms, state.demo, real])
  useEffect(() => () => { for (const id of pending.current.values()) clearTimeout(id) }, [])
  useEffect(() => {
    if (!state.demo) return
    let id: ReturnType<typeof setTimeout> | undefined
    // 手机体验版的 Key 页没有输入框：停同样久，然后等于点了「下一步」
    if (publicDemo && state.phase === 'key' && state.keyStatus === 'idle') id = setTimeout(() => {
      go({ type: 'phase', phase: 'unlock' })
    }, DEMO_MS.autoKey)
    else if (state.phase === 'key' && (state.keyStatus === 'idle' || state.keyStatus === 'error')) id = setTimeout(() => {
      setFail(false); setDraft(INTRO_COPY.demoKey); dispatch({ type: 'key', status: 'closing', intent: 'verify', last4: INTRO_COPY.demoTail })
    }, DEMO_MS.autoKey)
    // 手机体验版的选平台页不连接：一格一格往下转，每格停的时间 = 电脑版每个平台「停一下 + 连接」的时长，转到「下一步」后照常进整理页
    if (publicDemo && state.phase === 'platforms') {
      if (state.active < 5) id = setTimeout(() => select(state.active + 1), DEMO_MS.sourcePause + DEMO_MS.connect)
    } else if (state.phase === 'platforms' && !state.platforms.includes('connecting')) {
      const i = state.platforms.findIndex((p, index) => p === 'idle' && (platformSyncAvailable || index === 2))
      id = setTimeout(() => {
        if (i < 0) select(5)
        else { select(i); connect(i) }
      }, i < 0 ? DEMO_MS.allPause : DEMO_MS.sourcePause)
    }
    return () => clearTimeout(id)
  }, [state.demo, state.phase, state.keyStatus, state.platforms, publicDemo ? state.active : -1, publicDemo, select, connect, go])
  const jump = (phase: IntroPage) => { cancelLeave();setDraft('');setAdvanced(false);setFail(false);dispatch({type:'jump', phase}) }
  return { real, platformSyncAvailable, publicDemo, filePicker, dropLocal, leaving,
    chooseFiles: (files: FileList) => startLocal(collectInputFiles(files).map(item => item.file)),
    importError: importError || localImport.error, restartRequired,
    totals: real ? counts : LIBRARY_COUNTS,
    platformCount: (index: number) => real
      ? platformCounts.find(row => row.platform === (remotePlatforms[index] ?? 'local'))?.count
      : PLATFORMS[index].count,
    platformTitleDone: () => dispatch({type:'entry',step:'wheel'}), jump, state, draft, setDraft, fail, setFail, advanced, setAdvanced, enterKey, select, connect,
    setDemo: (value: boolean) => { if (value) { cancelLeave(); setDraft(''); setFail(false); setAdvanced(false); dispatch({ type: 'reset', demo: true }) } else dispatch({ type: 'demo', value }) },
    restart: () => { cancelLeave(); setDraft(''); setAdvanced(false); dispatch({ type: 'reset', demo: false }) }, verify,
    debugKey: () => { cancelLeave(); setDraft(''); setAdvanced(false); dispatch({ type: 'reset', demo: false }); dispatch({ type: 'phase', phase: 'key' }) },
    // 手机：「下一步 / 跳过」不再走卡片收拢那两段（280 + 650 毫秒），直接用统一的淡出上移换到选平台页
    skipKey: () => phone ? go({ type: 'phase', phase: 'unlock' })
      : dispatch(state.keyStatus === 'verified' ? { type: 'phase', phase: 'unlock' } : { type: 'key', status: 'closing', intent: 'skip' }),
    changeKey: () => { setDraft(''); setRestartRequired(false); dispatch({ type: 'key', status: 'idle', last4: '' }) },
    skip: (index: number) => dispatch({ type: 'platform', index, status: 'skipped' }),
    cooldown: () => dispatch({ type: 'platform', index: 0, status: state.platforms[0] === 'cooldown' ? 'idle' : 'cooldown' }),
  }
}
export type IntroFlow = ReturnType<typeof useIntroFlow>

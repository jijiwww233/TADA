import { useMemo, useState } from 'react'
import ScatterStage from '../scatter/ScatterStage'
import { BOUNCES } from '../scatter/choreo'
import { homePlan, type HomeDirection } from '../scatter/homePlans'
import '../scatter/home.css'

const DIRECTIONS = [{ key: 'cover', label: '杂志封面' }, { key: 'premotio', label: '上图下字' }, { key: 'book', label: '书页' }] as const

export default function ScatterDemo({ reduced }: { reduced: boolean }) {
  const [direction, setDirection] = useState<HomeDirection>('premotio')
  const [english, setEnglish] = useState(false)
  const plan = useMemo(() => homePlan(direction), [direction])
  const compare = (change: () => void) => {
    const stage = document.querySelector('[data-home-stage]')
    if (stage) window.scrollTo(0, stage.getBoundingClientRect().top + window.scrollY)
    change()
  }
  const controls = (
    <div className="home-controls" data-controls>
      <div role="group" aria-label="首页排版方向">
        {DIRECTIONS.map(d => <button key={d.key} type="button" aria-pressed={direction === d.key} onClick={() => compare(() => setDirection(d.key))}>{d.label}</button>)}
      </div>
      <div role="group" aria-label="主标题语言">
        <button type="button" aria-pressed={!english} onClick={() => compare(() => setEnglish(false))}>中文为主</button>
        <button type="button" aria-pressed={english} onClick={() => compare(() => setEnglish(true))}>英文为主</button>
      </div>
    </div>
  )
  return <ScatterStage plan={plan} bounce={BOUNCES[1]} direction={direction} english={english} reduced={reduced} controls={controls} />
}

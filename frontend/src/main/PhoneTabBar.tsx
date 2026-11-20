import type { ComponentType } from 'react'
import type { AppPage } from './AppHeader'
import { CalendarIcon, LibraryIcon, ProjectsIcon, SettingsIcon } from './tabIcons'

/** 手机底部导航的四页（参考画板不进导航） */
const TABS: { k: AppPage; label: string; icon: ComponentType }[] = [
  { k: 'library', label: '素材库', icon: LibraryIcon },
  { k: 'projects', label: '项目', icon: ProjectsIcon },
  { k: 'activity', label: '日历', icon: CalendarIcon },
  { k: 'settings', label: '设置', icon: SettingsIcon },
]

/**
 * 手机底部导航栏：固定在屏幕底部，米白毛玻璃（压在黑图上也看得清）；iOS 风格图标 + 一行 12px 小字，
 * 未选中深灰，当前页图标和文字是陶土橙。只有手机渲染（MainApp 里按 usePhoneLayout 判断），电脑仍用顶栏正中的切换胶囊。
 * 样式见 styles/phone.css「手机底部导航」。
 */
export function PhoneTabBar({ current, onGo }: { current: AppPage | null; onGo: (page: AppPage) => void }) {
  return (
    <nav className="m-tabbar" aria-label="切换页面">
      {TABS.map(tab => {
        const on = tab.k === current
        const TabIcon = tab.icon
        return (
          // data-nav：「存为项目」飞行动画的目标（电脑上是顶栏胶囊，手机上飞向底部的「项目」）
          <button type="button" key={tab.k} className={`m-tabbar-item${on ? ' is-on' : ''}`}
            aria-current={on ? 'page' : undefined} data-nav={tab.k === 'projects' ? 'projects' : undefined}
            onClick={() => onGo(tab.k)}>
            <TabIcon />
            <span className="m-tabbar-label">{tab.label}</span>
          </button>
        )
      })}
    </nav>
  )
}

import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/tokens.css'
import './index.css'
import App from './App.tsx'
import ReferenceReview from './ReferenceReview'
import { scheduleDeferredFonts } from './styles/deferredFonts'
const Root=location.pathname==='/reference-review'?ReferenceReview:App

// /lab: component sample page (ui-intro). Loaded on its own so other pages stay untouched.
const Lab = lazy(() => import('./lab/Lab.tsx'))
// /intro: 只有引导页本身的干净入口（课堂演示用），内容和 /lab#scatter 是同一套。
const Intro = lazy(() => import('./lab/IntroPage.tsx'))
// /main: 主界面 demo（注册页之后）。独立路由，不改动现有 App。
const MainApp = lazy(() => import('./main/MainApp.tsx'))
// /main-v4: 上一版快照，留着对照。
const MainV4 = lazy(() => import('./main-v4/MainApp.tsx'))
// /design-system: 设计系统说明页（令牌、按钮、用词对照），入口在设置 → 关于
const DesignSystem = lazy(() => import('./main/DesignSystem.tsx'))

// 路由判断：支持 pathname (/lab, /main) 和 hash (#/lab, #/main)，后者用于 file:// 协议
const getRoute = () => {
  const path = location.pathname.replace(/\/+$/, '')
  const hash = location.hash.replace(/^#/, '').replace(/\/+$/, '').replace(/\*+$/, '')
  return {
    isLab: path === '/lab' || hash === '/lab',
    isIntro: path === '/intro' || hash === '/intro',
    isMain: path.replace(/\*+$/, '') === '/main' || hash === '/main',
    isMainV4: path === '/main-v4' || hash === '/main-v4',
    isDesignSystem: path === '/design-system' || hash === '/design-system',
  }
}

const route = getRoute()

// 主界面字体（Main Serif SC / Main Newsreader，约 3MB）延后到首屏图片之后加载，见 styles/deferredFonts.ts
if (route.isMain || route.isMainV4 || route.isDesignSystem) scheduleDeferredFonts()

createRoot(document.getElementById('root')!).render(
  // /lab stays outside StrictMode: its dev-only double mount runs every effect's cleanup, and the WebGL demos
  // release their context there (CircularGallery calls WEBGL_lose_context). The remounted canvas then keeps a dead
  // context and never draws — dev only, the production build has no double mount. Every other page keeps StrictMode.
  route.isLab || route.isIntro || route.isMain || route.isMainV4 || route.isDesignSystem ? (
    <Suspense fallback={null}>
      {route.isLab ? <Lab /> : route.isIntro ? <Intro /> : route.isMainV4 ? <MainV4 />
        : route.isDesignSystem ? <DesignSystem /> : <MainApp />}
    </Suspense>
  ) : (
    <StrictMode><Root /></StrictMode>
  ),
)

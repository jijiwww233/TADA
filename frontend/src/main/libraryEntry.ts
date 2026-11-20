import { libraryCounts, libraryImages } from '../api/libraryRead'
import { readDemoMode } from '../api/mode'

let loadingMainApp: Promise<typeof import('./MainApp.tsx')> | undefined
export function loadMainApp() {
  return loadingMainApp ??= import('./MainApp.tsx').catch(error => {
    loadingMainApp = undefined
    throw error
  })
}

export function preloadLibrary(href: string) {
  void loadMainApp().catch(() => {})
  if (readDemoMode(new URL(href, window.location.href).search)) return
  libraryImages.preload()
  libraryCounts.preload()
}

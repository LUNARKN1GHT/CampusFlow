// 在样式生效前写入主题，避免首屏闪回浅色；逻辑与 composables/useTheme.ts 保持一致。
// 独立为外部脚本而不是 index.html 内联脚本，桌面模式的 CSP（script-src 'self'）才能
// 正常执行它；本地开发（Vite）与打包产物都从同源加载。
;(function () {
  try {
    var stored = localStorage.getItem('campusflow-theme')
    var mode = stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'light'
    var dark =
      mode === 'system'
        ? window.matchMedia('(prefers-color-scheme: dark)').matches
        : mode === 'dark'
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  } catch (error) {
    document.documentElement.dataset.theme = 'light'
  }
})()

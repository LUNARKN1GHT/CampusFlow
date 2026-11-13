// 在样式生效前写入深浅与色系，避免首屏闪回默认外观；逻辑与 composables/useTheme.ts 保持一致。
// 独立为外部脚本而不是 index.html 内联脚本，桌面模式的 CSP（script-src 'self'）才能
// 正常执行它；本地开发（Vite）与打包产物都从同源加载。
;(function () {
  var root = document.documentElement
  try {
    var storedTheme = localStorage.getItem('campusflow-theme')
    var mode = storedTheme === 'light' || storedTheme === 'dark' || storedTheme === 'system' ? storedTheme : 'light'
    var dark =
      mode === 'system'
        ? window.matchMedia('(prefers-color-scheme: dark)').matches
        : mode === 'dark'
    root.dataset.theme = dark ? 'dark' : 'light'
  } catch (error) {
    root.dataset.theme = 'light'
  }
  try {
    var storedPalette = localStorage.getItem('campusflow-palette')
    root.dataset.palette =
      storedPalette === 'green' || storedPalette === 'blue' || storedPalette === 'white'
        ? storedPalette
        : 'green'
  } catch (error) {
    root.dataset.palette = 'green'
  }
})()

import { computed, ref, watch } from 'vue'

// 主题：浅色／深色／跟随系统，解析后写入 documentElement[data-theme]；选择存 localStorage，只影响本机。
// 首屏由 index.html 内联脚本按同一键预先写入；未保存或存储不可用时回退浅色。

export type ThemeMode = 'light' | 'dark' | 'system'

export const THEME_STORAGE_KEY = 'campusflow-theme'

const MODES: readonly ThemeMode[] = ['light', 'dark', 'system']

function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === 'string' && (MODES as readonly string[]).includes(value)
}

// 模块级单例：系统主题监听只注册一次，跟随系统时随系统设置变化立即重算。
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)')
const systemDark = ref(darkQuery.matches)
darkQuery.addEventListener('change', (event) => {
  systemDark.value = event.matches
})

function readStoredMode(): ThemeMode {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
    return isThemeMode(stored) ? stored : 'light'
  } catch {
    return 'light'
  }
}

const mode = ref<ThemeMode>(readStoredMode())
const resolved = computed<'light' | 'dark'>(() =>
  mode.value === 'system' ? (systemDark.value ? 'dark' : 'light') : mode.value,
)

// immediate 保证模块首次导入就写入 data-theme，与首屏内联脚本的解析结果一致。
watch(resolved, (value) => {
  document.documentElement.dataset.theme = value
}, { immediate: true })

export function setThemeMode(value: ThemeMode) {
  mode.value = value
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, value)
  } catch {
    // 存储被禁用（隐私模式等）时仅保留当前会话内的切换，不回显为错误。
  }
}

export function useTheme() {
  return {
    options: [
      { value: 'light' as ThemeMode, label: '浅色' },
      { value: 'dark' as ThemeMode, label: '深色' },
      { value: 'system' as ThemeMode, label: '跟随系统' },
    ],
    mode,
    resolved,
    setThemeMode,
  }
}

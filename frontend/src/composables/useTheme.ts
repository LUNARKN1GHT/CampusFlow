import { computed, ref, watch } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'

export const THEME_STORAGE_KEY = 'campusflow-theme'

const MODES: readonly ThemeMode[] = ['light', 'dark', 'system']

function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === 'string' && (MODES as readonly string[]).includes(value)
}

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

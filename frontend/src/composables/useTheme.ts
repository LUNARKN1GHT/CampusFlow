import { computed, ref, watch } from 'vue'

// 主题：浅色／深色／跟随系统写入 data-theme；色系（绿色／蓝色／白色）写入 data-palette，两者独立叠加。
// 选择存 localStorage，只影响本机；首屏由 public/theme-boot.js（外部同源脚本，兼容桌面 CSP）按同一键预先写入。

export type ThemeMode = 'light' | 'dark' | 'system'
export type ColorPalette = 'green' | 'blue' | 'white'

export const THEME_STORAGE_KEY = 'campusflow-theme'
export const PALETTE_STORAGE_KEY = 'campusflow-palette'

const MODES: readonly ThemeMode[] = ['light', 'dark', 'system']
const PALETTES: readonly ColorPalette[] = ['green', 'blue', 'white']

function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === 'string' && (MODES as readonly string[]).includes(value)
}

function isColorPalette(value: unknown): value is ColorPalette {
  return typeof value === 'string' && (PALETTES as readonly string[]).includes(value)
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

function readStoredPalette(): ColorPalette {
  try {
    const stored = window.localStorage.getItem(PALETTE_STORAGE_KEY)
    return isColorPalette(stored) ? stored : 'green'
  } catch {
    return 'green'
  }
}

const mode = ref<ThemeMode>(readStoredMode())
const palette = ref<ColorPalette>(readStoredPalette())
const resolved = computed<'light' | 'dark'>(() =>
  mode.value === 'system' ? (systemDark.value ? 'dark' : 'light') : mode.value,
)

// immediate 保证模块首次导入就写入两个属性，与首屏外部脚本的解析结果一致。
watch(resolved, (value) => {
  document.documentElement.dataset.theme = value
}, { immediate: true })

watch(palette, (value) => {
  document.documentElement.dataset.palette = value
}, { immediate: true })

export function setThemeMode(value: ThemeMode) {
  mode.value = value
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, value)
  } catch {
    // 存储被禁用（隐私模式等）时仅保留当前会话内的切换，不回显为错误。
  }
}

export function setColorPalette(value: ColorPalette) {
  palette.value = value
  try {
    window.localStorage.setItem(PALETTE_STORAGE_KEY, value)
  } catch {
    // 同上：存储不可用时只保留当前会话内的选择。
  }
}

export function useTheme() {
  return {
    options: [
      { value: 'light' as ThemeMode, label: '浅色' },
      { value: 'dark' as ThemeMode, label: '深色' },
      { value: 'system' as ThemeMode, label: '跟随系统' },
    ],
    paletteOptions: [
      { value: 'green' as ColorPalette, label: '绿色' },
      { value: 'blue' as ColorPalette, label: '蓝色' },
      { value: 'white' as ColorPalette, label: '白色' },
    ],
    mode,
    resolved,
    palette,
    setThemeMode,
    setColorPalette,
  }
}

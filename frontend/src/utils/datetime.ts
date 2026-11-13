// 日期与时间展示统一从这里取：Intl formatter 预建复用，列表逐行格式化时不再每次新建实例。
// 不带时区的函数按浏览器本地时区输出；带时区的函数按 timeZone 缓存 formatter，并保留各页原有格式。
const dateFormatter = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' })
const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
const timeFormatter = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' })
const weekdayDateFormatter = new Intl.DateTimeFormat('zh-CN', { weekday: 'short', month: 'numeric', day: 'numeric' })

const zonedPartsFormatters = new Map<string, Intl.DateTimeFormat>()
const zonedTimeFormatters = new Map<string, Intl.DateTimeFormat>()
const zonedShortDateTimeFormatters = new Map<string, Intl.DateTimeFormat>()
const zonedMonthDayWeekdayFormatters = new Map<string, Intl.DateTimeFormat>()

function cached(
  cache: Map<string, Intl.DateTimeFormat>,
  timeZone: string,
  build: () => Intl.DateTimeFormat,
): Intl.DateTimeFormat {
  let formatter = cache.get(timeZone)
  if (!formatter) {
    formatter = build()
    cache.set(timeZone, formatter)
  }
  return formatter
}

/** 2026年1月5日（dateStyle: medium）。 */
export function formatDate(value: string | Date): string {
  return dateFormatter.format(new Date(value))
}

/** 2026年1月5日 08:05（dateStyle: medium / timeStyle: short）。 */
export function formatDateTime(value: string | Date): string {
  return dateTimeFormatter.format(new Date(value))
}

/** 08:05：按浏览器本地时区的时刻。 */
export function formatTime(value: string | Date): string {
  return timeFormatter.format(new Date(value))
}

/** 1/5周一：周视图按本地日期展示。 */
export function formatWeekdayDate(value: string | Date): string {
  return weekdayDateFormatter.format(new Date(value))
}

/** 指定时区的墙上时间各部分（YYYY-MM-DDTHH:mm 的来源），未识别部分为 undefined。 */
export function zonedParts(value: string | Date, timeZone: string): Record<string, string> {
  const formatter = cached(zonedPartsFormatters, timeZone, () => new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }))
  const values: Record<string, string> = {}
  for (const part of formatter.formatToParts(new Date(value))) values[part.type] = part.value
  return values
}

/** 指定时区的自然日 YYYY-MM-DD，用于按日分组与日期比较。 */
export function zonedDayKey(value: string | Date, timeZone: string): string {
  const parts = zonedParts(value, timeZone)
  return `${parts.year}-${parts.month}-${parts.day}`
}

/** 指定时区的墙上时间 YYYY-MM-DDTHH:mm，供 datetime-local 与按日切分使用。 */
export function zonedInputValue(value: string | Date, timeZone: string): string {
  const parts = zonedParts(value, timeZone)
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
}

/** 08:05：按指定时区（计划／工作空间时区）展示时刻。 */
export function formatZonedTime(value: string | Date, timeZone: string): string {
  const formatter = cached(zonedTimeFormatters, timeZone, () => new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }))
  return formatter.format(new Date(value))
}

/** 2026/1/5 08:05：按指定时区展示日期时间（dateStyle: short / timeStyle: short）。 */
export function formatZonedShortDateTime(value: string | Date, timeZone: string): string {
  const formatter = cached(zonedShortDateTimeFormatters, timeZone, () => new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    dateStyle: 'short',
    timeStyle: 'short',
  }))
  return formatter.format(new Date(value))
}

/** 1月5日周一：按指定时区展示分组标题。 */
export function formatZonedMonthDayWeekday(value: string | Date, timeZone: string): string {
  const formatter = cached(zonedMonthDayWeekdayFormatters, timeZone, () => new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  }))
  return formatter.format(new Date(value))
}

/**
 * DDL 展示：有时刻 → `YYYY-MM-DD HH:mm`；仅日期 → 标注（仅日期）；无日期 → 未设置 DDL。
 * 不补造时刻，几处页面共用同一文案。
 */
export function formatDueDate(dueDate: string | null, dueTime: string | null): string {
  if (!dueDate) return '未设置 DDL'
  return dueTime ? `${dueDate} ${dueTime.slice(0, 5)}` : `${dueDate}（仅日期）`
}

/** 本地日期键 YYYY-MM-DD，用于周视图按天切分。 */
export function localDateKey(value: Date): string {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

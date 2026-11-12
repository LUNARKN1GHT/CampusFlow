/**
 * 拼查询串：只省略 null／undefined／空串；false 与 0 仍会编码（例如 include_archived=false 也会发送）。
 * 没有有效参数时返回空串，调用方直接拼在路径后面。
 */
export function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

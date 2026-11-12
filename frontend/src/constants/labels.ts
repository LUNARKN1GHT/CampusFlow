// 跨页面共用的枚举文案；新增枚举值时只改这里。页面特有映射仍留在各自页面内。
export const progressLabels: Record<string, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  blocked: '受阻',
  done: '已完成',
  cancelled: '已取消',
}

export const priorityLabels: Record<string, string> = { low: '低', medium: '中', high: '高' }

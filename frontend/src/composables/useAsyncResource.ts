import { getCurrentScope, onScopeDispose, ref, type Ref } from 'vue'

import { errorMessage } from '../api/client'

// 通用异步加载包装：暴露 data／loading／error 与可重复调用的 load。
// 每次 load 递增请求号，只有最后一次请求能写回数据、错误与 loading；
// 组件卸载（作用域销毁）后旧响应一律丢弃，不会把过期结果写给已关闭的界面。
export type AsyncResource<T> = {
  data: Ref<T | null>
  loading: Ref<boolean>
  error: Ref<string>
  load: () => Promise<void>
}

export type AsyncResourceOptions<T> = {
  /** 起始数据；未提供时为 null。 */
  initial?: T | null
  /** 发起新加载时立即清空旧数据，默认 false（保留旧数据直到新请求成功或失败）。 */
  resetOnLoad?: boolean
}

export function useAsyncResource<T>(
  loader: () => Promise<T>,
  options: AsyncResourceOptions<T> = {},
): AsyncResource<T> {
  const data = ref<T | null>(options.initial ?? null) as Ref<T | null>
  const loading = ref(false)
  const error = ref('')
  let requestId = 0
  let disposed = false

  async function load() {
    const current = ++requestId
    loading.value = true
    error.value = ''
    if (options.resetOnLoad) data.value = null
    try {
      const result = await loader()
      // 已被更新的 load 取代或组件已卸载：旧响应直接丢弃，不能覆盖新范围的数据。
      if (disposed || current !== requestId) return
      data.value = result
    } catch (caught) {
      if (disposed || current !== requestId) return
      // 失败时保留上一次成功的数据，只写错误；是否继续展示旧数据由调用方决定。
      error.value = errorMessage(caught)
    } finally {
      if (!disposed && current === requestId) loading.value = false
    }
  }

  if (getCurrentScope()) {
    onScopeDispose(() => {
      disposed = true
      // 递增请求号，让仍在途的响应在比对时被判定为过期。
      requestId += 1
    })
  }

  return { data, loading, error, load }
}

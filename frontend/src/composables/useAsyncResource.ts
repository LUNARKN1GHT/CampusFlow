import { ref, type Ref } from 'vue'

import { errorMessage } from '../api/client'

// 通用异步加载包装：暴露 data／loading／error 与可重复调用的 load。
// 不做请求取消或竞态保护（并发 load 可能乱序完成），也不在卸载后丢弃结果，需要时由调用方自行处理。
export type AsyncResource<T> = {
  data: Ref<T | null>
  loading: Ref<boolean>
  error: Ref<string>
  load: () => Promise<void>
}

export function useAsyncResource<T>(loader: () => Promise<T>): AsyncResource<T> {
  const data = ref<T | null>(null) as Ref<T | null>
  const loading = ref(false)
  const error = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      data.value = await loader()
    } catch (caught) {
      // 失败时保留上一次成功的数据，只写错误；是否继续展示旧数据由调用方决定。
      error.value = errorMessage(caught)
    } finally {
      loading.value = false
    }
  }

  return { data, loading, error, load }
}

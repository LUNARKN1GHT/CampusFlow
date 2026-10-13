import { ref, type Ref } from 'vue'

import { errorMessage } from '../api/client'

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
      error.value = errorMessage(caught)
    } finally {
      loading.value = false
    }
  }

  return { data, loading, error, load }
}

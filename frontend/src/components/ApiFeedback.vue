<script setup lang="ts">
// 统一失败提示：role=alert 负责播报，重试按钮的加载状态由调用方通过 busy 传入。
defineProps<{ message: string; busy?: boolean; title?: string }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="api-feedback" role="alert">
    <div>
      <strong>{{ title ?? '加载失败' }}</strong>
      <p>{{ message }}</p>
    </div>
    <button type="button" class="button-secondary" :disabled="busy" @click="$emit('retry')">
      {{ busy ? '正在重试…' : '重新加载' }}
    </button>
  </div>
</template>

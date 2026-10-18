<script setup lang="ts">
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

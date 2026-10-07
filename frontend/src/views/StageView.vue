<script setup lang="ts">
defineProps<{ title: string; description: string }>()
</script>

<template>
  <main class="page-content">
    <div class="page-heading">
      <p class="eyebrow">功能接入中</p>
      <h1>{{ title }}</h1>
      <p class="intro">{{ description }}</p>
    </div>
    <section class="empty-state" aria-labelledby="stage-status">
      <span class="empty-icon" aria-hidden="true">↗</span>
      <div>
        <h2 id="stage-status">入口已就位，功能尚未开放</h2>
        <p>此页面暂不提供保存或编辑操作，避免把演示数据误认为真实业务数据。</p>
      </div>
    </section>
  </main>
</template>

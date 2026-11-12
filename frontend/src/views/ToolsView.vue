<script setup lang="ts">
/*
 * 工具箱：集中承载使用频率较低、但功能完整的工具，不占用左侧主导航。
 * 课程路径按培养方案版本维护课程关系、关系图、拓扑顺序与学期试排，属于低频操作，
 * 因此从一级导航移到这里；页面本身与后端能力保持不变，仍可直接访问 /course-paths。
 */
import { RouterLink } from 'vue-router'
</script>

<template>
  <main class="page-content">
    <div class="page-heading">
      <p class="eyebrow">低频工具</p>
      <h1>工具箱</h1>
      <p class="intro">这里放使用频率较低、但功能完整的能力。它们不占用左侧主导航，需要时从工具箱进入。</p>
    </div>

    <section class="panel" aria-label="工具箱内容">
      <div class="section-heading">
        <div><p class="eyebrow">培养方案与课程关系</p><h2>低频工具</h2></div>
        <span class="count-badge">1 项</span>
      </div>

      <RouterLink class="tool-card" to="/course-paths">
        <span class="tool-eyebrow">课程路径</span>
        <strong class="tool-title">培养方案、课程关系与学期试排</strong>
        <span class="tool-desc">
          按培养方案版本维护课程、强制先修／并修／建议先学与修读记录，查看关系图、拓扑层次与下一步建议。
          成环、缺项或来源矛盾时只展示阻塞原因，不输出伪造顺序；学期试排仅按已知开课与学分上限试排，不构成选课或毕业审核。
        </span>
        <span class="tool-tags">
          <span>培养方案档案</span>
          <span>课程关系</span>
          <span>关系图</span>
          <span>拓扑排序</span>
          <span>学期试排</span>
        </span>
        <span class="tool-enter">进入课程路径</span>
      </RouterLink>

      <p class="form-help">工具箱只改变入口位置，不改变已保存的培养方案、课程关系或计划。课程路径仍可通过地址 /course-paths 直接访问。</p>
    </section>

    <p class="tool-back"><RouterLink to="/settings">返回设置</RouterLink></p>
  </main>
</template>

<style scoped>
.tool-card { display: grid; gap: 8px; padding: 20px; border: 1px solid var(--line); border-radius: 14px; background: var(--paper-strong); text-decoration: none; }
.tool-card:hover { border-color: var(--brand-border); background: var(--brand-selected); }
.tool-eyebrow { color: var(--muted-strong); font-size: 11px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.tool-title { font-size: 18px; }
.tool-desc { color: var(--muted); font-size: 14px; line-height: 1.7; }
.tool-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tool-tags span { padding: 3px 9px; border-radius: 999px; color: var(--muted); background: var(--surface-soft); font-size: 11px; font-weight: 700; }
.tool-enter { color: var(--brand); font-size: 13px; font-weight: 800; }
.tool-back { margin: 18px 0 0; color: var(--muted); font-size: 13px; }
.tool-back a { display: inline-flex; padding: 7px 10px; border-radius: 10px; color: var(--muted); text-decoration: none; }
.tool-back a:hover { color: var(--ink); background: var(--surface-soft); }
</style>

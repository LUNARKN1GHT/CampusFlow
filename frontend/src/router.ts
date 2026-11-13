import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory, useRoute } from 'vue-router'

import AppShell from './components/AppShell.vue'
import { setSessionExpiredHandler, useAuth } from './composables/useAuth'

// 路由表与登录守卫：受保护页面统一挂在 AppShell 下，未登录跳登录页并保留 redirect 目标。
// 视图按路由动态加载，避免首屏一次性下载全部页面；AppShell 保持同步以稳定主布局。
// 会话检查复用 useAuth 的缓存与并发去重；已登录访问登录页回到总览，未知路径回总览并给出显式提示。
const SITE_TITLE = 'CampusFlow · 校园学习事务智能助理'
const NOT_FOUND_HINT = '页面不存在：该地址可能已经下线，已回到总览。请从顶部导航进入需要的页面。'

/**
 * 未知路径不做静默跳转：catch-all 仍回退到总览（URL 不变），但由该布局在页面顶部给出“页面不存在”提示，
 * 避免已下线深链（如 /review、/qa）被误认为数据丢失。提示用渲染函数就地定义，不新增视图文件。
 */
const AppLayout = defineComponent({
  name: 'AppLayout',
  setup() {
    const route = useRoute()
    return () => [
      route.query.notFound === '1'
        ? h('p', {
            key: 'not-found',
            class: 'inline-notice',
            role: 'status',
            // 复用语义色 token，只为标题区补齐外边距，不写死色值。
            style: 'margin: 12px clamp(24px, 5vw, 72px) 0;',
          }, NOT_FOUND_HINT)
        : null,
      h(AppShell, { key: 'shell' }),
    ]
  },
})

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: AppLayout,
      meta: { requiresAuth: true, title: '总览' },
      children: [
        { path: '', name: 'overview', component: () => import('./views/OverviewView.vue'), meta: { title: '总览' } },
        { path: 'health', name: 'health', component: () => import('./views/HealthView.vue'), meta: { title: '系统状态' } },
        { path: 'plan', name: 'plan', component: () => import('./views/PlanView.vue'), meta: { title: '学习计划' } },
        {
          path: 'courses',
          name: 'courses',
          component: () => import('./views/CoursesView.vue'),
          meta: { title: '课程' },
        },
        {
          path: 'course-paths',
          name: 'course-paths',
          component: () => import('./views/CoursePathsView.vue'),
          meta: { title: '课程路径' },
        },
        {
          path: 'library',
          name: 'library',
          component: () => import('./views/LibraryView.vue'),
          meta: { title: '资料库' },
        },
        {
          path: 'tasks',
          name: 'tasks',
          component: () => import('./views/TasksView.vue'),
          meta: { title: '任务' },
        },
        {
          path: 'reminders',
          name: 'reminders',
          component: () => import('./views/RemindersView.vue'),
          meta: { title: '提醒' },
        },
        {
          path: 'export',
          name: 'export',
          component: () => import('./views/ExportView.vue'),
          meta: { title: '导出' },
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('./views/SettingsView.vue'),
          meta: { title: '设置' },
        },
        {
          // 低频工具集中入口：课程路径等不占用主导航，原路由仍可直接访问。
          path: 'tools',
          name: 'tools',
          component: () => import('./views/ToolsView.vue'),
          meta: { title: '工具箱' },
        },
      ],
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('./views/LoginView.vue'),
      meta: { title: '登录' },
    },
    // 未知路径：带查询标记回到总览，由 AppLayout 显示“页面不存在”，不再无提示跳转。
    { path: '/:pathMatch(.*)*', redirect: { path: '/', query: { notFound: '1' } } },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

// 会话失效改用路由跳转并携带当前路径：重新登录后回到原页面，且只发生一次跳转（并发 401 由 useAuth 去抖）。
setSessionExpiredHandler(() => {
  const current = router.currentRoute.value
  if (current.name === 'login') return
  return router.replace({ name: 'login', query: { expired: '1', redirect: current.fullPath } })
})

// 子路由不重复声明 requiresAuth，由父级记录合并到 to.meta。
router.beforeEach(async (to) => {
  const { state, checkSession } = useAuth()
  if (to.meta.requiresAuth) {
    const authenticated = state.session !== null || await checkSession()
    if (!authenticated) {
      // 只有 401 才能断定未登录；连接失败时保留 checked = false 并放行，让页面显示真实的连接失败原因，
      // 后端恢复后再次导航即可重新确认，不需要刷新浏览器。
      if (state.error) return true
      return { name: 'login', query: { redirect: to.fullPath } }
    }
  }
  if (to.name === 'login' && (state.session !== null || await checkSession())) return { name: 'overview' }
})

// 标签页标题跟随路由：未声明 title 的地址回落到站点标题，浏览器历史与标签页可以区分页面。
router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' && to.meta.title ? `${to.meta.title} · CampusFlow` : SITE_TITLE
  document.title = title
})

export default router

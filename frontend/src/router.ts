import { createRouter, createWebHistory } from 'vue-router'

import AppShell from './components/AppShell.vue'
import { useAuth } from './composables/useAuth'

// 路由表与登录守卫：受保护页面统一挂在 AppShell 下，未登录跳登录页并保留 redirect 目标。
// 视图按路由动态加载，避免首屏一次性下载全部页面；AppShell 保持同步以稳定主布局。
// 会话检查复用 useAuth 的缓存与并发去重；已登录访问登录页回到总览，未知路径回首页。
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: AppShell,
      meta: { requiresAuth: true },
      children: [
        { path: '', name: 'overview', component: () => import('./views/OverviewView.vue') },
        { path: 'health', name: 'health', component: () => import('./views/HealthView.vue') },
        { path: 'plan', name: 'plan', component: () => import('./views/PlanView.vue') },
        {
          path: 'courses',
          name: 'courses',
          component: () => import('./views/CoursesView.vue'),
        },
        {
          path: 'course-paths',
          name: 'course-paths',
          component: () => import('./views/CoursePathsView.vue'),
        },
        {
          path: 'library',
          name: 'library',
          component: () => import('./views/LibraryView.vue'),
        },
        {
          path: 'tasks',
          name: 'tasks',
          component: () => import('./views/TasksView.vue'),
        },
        {
          path: 'reminders',
          name: 'reminders',
          component: () => import('./views/RemindersView.vue'),
        },
        {
          path: 'export',
          name: 'export',
          component: () => import('./views/ExportView.vue'),
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('./views/SettingsView.vue'),
        },
        {
          // 低频工具集中入口：课程路径等不占用主导航，原路由仍可直接访问。
          path: 'tools',
          name: 'tools',
          component: () => import('./views/ToolsView.vue'),
        },
      ],
    },
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

// 子路由不重复声明 meta，requiresAuth 由父级记录合并到 to.meta。
router.beforeEach(async (to) => {
  const { state, checkSession } = useAuth()
  if (to.meta.requiresAuth) {
    const authenticated = state.session !== null || await checkSession()
    if (!authenticated) return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && (state.session !== null || await checkSession())) return { name: 'overview' }
})

export default router

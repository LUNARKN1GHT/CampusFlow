import { createRouter, createWebHistory } from 'vue-router'

import AppShell from './components/AppShell.vue'
import { useAuth } from './composables/useAuth'
import CoursesView from './views/CoursesView.vue'
import HealthView from './views/HealthView.vue'
import LoginView from './views/LoginView.vue'
import StageView from './views/StageView.vue'
import TasksView from './views/TasksView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: AppShell,
      meta: { requiresAuth: true },
      children: [
        { path: '', name: 'overview', component: HealthView },
        {
          path: 'courses',
          name: 'courses',
          component: CoursesView,
        },
        {
          path: 'tasks',
          name: 'tasks',
          component: TasksView,
        },
        {
          path: 'settings',
          name: 'settings',
          component: StageView,
          props: { title: '设置', description: '时区与可用时间将在本阶段接入。' },
        },
      ],
    },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const { state, checkSession } = useAuth()
  if (to.meta.requiresAuth) {
    const authenticated = state.session !== null || await checkSession()
    if (!authenticated) return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && (state.session !== null || await checkSession())) return { name: 'overview' }
})

export default router

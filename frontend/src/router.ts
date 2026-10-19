import { createRouter, createWebHistory } from 'vue-router'

import AppShell from './components/AppShell.vue'
import { useAuth } from './composables/useAuth'
import CalendarView from './views/CalendarView.vue'
import CoursePathsView from './views/CoursePathsView.vue'
import CoursesView from './views/CoursesView.vue'
import ExportView from './views/ExportView.vue'
import HealthView from './views/HealthView.vue'
import LibraryView from './views/LibraryView.vue'
import LoginView from './views/LoginView.vue'
import OverviewView from './views/OverviewView.vue'
import PlanView from './views/PlanView.vue'
import QaView from './views/QaView.vue'
import RemindersView from './views/RemindersView.vue'
import ReviewView from './views/ReviewView.vue'
import SettingsView from './views/SettingsView.vue'
import TasksView from './views/TasksView.vue'
import VersionChangeView from './views/VersionChangeView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: AppShell,
      meta: { requiresAuth: true },
      children: [
        { path: '', name: 'overview', component: OverviewView },
        { path: 'health', name: 'health', component: HealthView },
        { path: 'plan', name: 'plan', component: PlanView },
        {
          path: 'courses',
          name: 'courses',
          component: CoursesView,
        },
        {
          path: 'course-paths',
          name: 'course-paths',
          component: CoursePathsView,
        },
        {
          path: 'library',
          name: 'library',
          component: LibraryView,
        },
        {
          path: 'review',
          name: 'review',
          component: ReviewView,
        },
        {
          path: 'review/changes',
          name: 'review-changes',
          component: VersionChangeView,
        },
        {
          path: 'qa',
          name: 'qa',
          component: QaView,
        },
        {
          path: 'tasks',
          name: 'tasks',
          component: TasksView,
        },
        {
          path: 'reminders',
          name: 'reminders',
          component: RemindersView,
        },
        {
          path: 'export',
          name: 'export',
          component: ExportView,
        },
        {
          path: 'calendar',
          name: 'calendar',
          component: CalendarView,
        },
        {
          path: 'settings',
          name: 'settings',
          component: SettingsView,
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

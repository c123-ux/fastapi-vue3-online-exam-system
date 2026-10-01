import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import Layout from '../components/Layout.vue'

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    { path: '/login', name: 'Login', component: () => import('../views/Login.vue'), meta: { title: '登录', requiresAuth: false } },
    { path: '/register', name: 'Register', component: () => import('../views/Register.vue'), meta: { title: '注册', requiresAuth: false } },

    {
      path: '/',
      component: Layout,
      meta: { requiresAuth: true },
      children: [
        { path: '', redirect: '/dashboard' },
        { path: 'dashboard', name: 'Dashboard', component: () => import('../views/Dashboard.vue'), meta: { title: '工作台', roles: ['teacher', 'student'] } },

        // 学生
        { path: 'exams', name: 'ExamList', component: () => import('../views/student/ExamList.vue'), meta: { title: '我的考试', roles: ['student'] } },
        { path: 'exams/:recordId/take', name: 'ExamTake', component: () => import('../views/student/ExamTake.vue'), meta: { title: '参加考试', roles: ['student'] } },
        { path: 'exams/:recordId/score', name: 'ExamScore', component: () => import('../views/student/ExamScore.vue'), meta: { title: '考试成绩', roles: ['student'] } },

        // 教师
        { path: 'papers', name: 'PaperList', component: () => import('../views/teacher/PaperList.vue'), meta: { title: '试卷管理', roles: ['teacher'] } },
        { path: 'papers/create', name: 'PaperCreate', component: () => import('../views/teacher/PaperCreate.vue'), meta: { title: '创建试卷', roles: ['teacher'] } },
        { path: 'papers/:paperId', name: 'PaperDetail', component: () => import('../views/teacher/PaperDetail.vue'), meta: { title: '试卷详情', roles: ['teacher'] } },
        { path: 'papers/:paperId/stats', name: 'PaperStats', component: () => import('../views/teacher/PaperStats.vue'), meta: { title: '考试统计', roles: ['teacher'] } },
        { path: 'records/:recordId/review', name: 'ReviewPage', component: () => import('../views/teacher/ReviewPage.vue'), meta: { title: '批改答卷', roles: ['teacher'] } },
        { path: 'questions', name: 'QuestionBank', component: () => import('../views/teacher/QuestionBank.vue'), meta: { title: '题库管理', roles: ['teacher'] } },
      ],
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  const requiresAuth = to.meta.requiresAuth !== false
  if (requiresAuth && !auth.token) {
    return { name: 'Login', query: { redirect: to.fullPath } }
  }
  if (to.meta.roles && auth.role && !((to.meta.roles as string[]).includes(auth.role))) {
    return { name: 'Dashboard' }
  }
})

export default router

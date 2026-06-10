import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'CustomerService',
    component: () => import('../views/CustomerService.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router 
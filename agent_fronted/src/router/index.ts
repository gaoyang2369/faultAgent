import { createRouter, createWebHistory } from 'vue-router'
import CustomerServiceView from '../views/CustomerService.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'chat',
      alias: '/customer-service',
      component: CustomerServiceView
    },
  ],
})

export default router

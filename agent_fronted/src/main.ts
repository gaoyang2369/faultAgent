import { createApp } from 'vue'
import App from './App.vue'
import router from './router'

import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'// 引入样式
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import {createPinia} from "pinia";


const pinia = createPinia()
const app = createApp(App)

app.use(router).use(ElementPlus,{locale: zhCn}).use(pinia).mount('#app')
for(const [key, component] of Object.entries(ElementPlusIconsVue)){
    app.component(key, component)
}
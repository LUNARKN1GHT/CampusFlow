// 应用入口：全局样式提供语义色 token，主题由 index.html 首屏脚本与 useTheme 写入 data-theme。
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './styles.css'

createApp(App).use(router).mount('#app')

import {defineConfig} from 'vite'
import vue from '@vitejs/plugin-vue'
// Keep the browser's Host and Origin together for the API's same-origin checks.
export default defineConfig({plugins:[vue()],server:{proxy:{
 '/api':{target:'http://127.0.0.1:18996',changeOrigin:false},
 '/play':{target:'http://127.0.0.1:18996',changeOrigin:false}
}}})

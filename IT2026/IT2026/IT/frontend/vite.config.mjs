import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

const __dirname = dirname(fileURLToPath(import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const assetsTarget = env.VITE_PROXY_ASSETS_TARGET || 'http://127.0.0.1:8080'
  const softwareTarget = env.VITE_PROXY_SOFTWARE_TARGET || 'http://127.0.0.1:8081'
  const policyTarget = env.VITE_PROXY_POLICY_TARGET || 'http://127.0.0.1:8082'

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src')
      }
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
      proxy: {
        '/api/v1/discovery': {
          target: assetsTarget,
          changeOrigin: true
        },
        '/software-api': {
          target: softwareTarget,
          changeOrigin: true,
          rewrite: path => path.replace(/^\/software-api/, '')
        },
        '/policy-api': {
          target: policyTarget,
          changeOrigin: true,
          rewrite: path => path.replace(/^\/policy-api/, '')
        },
        '/api': {
          target: assetsTarget,
          changeOrigin: true,
          ws: true
        }
      }
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes('node_modules')) {
              if (
                id.includes('/element-plus/') ||
                id.includes('/@element-plus/icons-vue/')
              ) {
                return 'elementPlus'
              }

              if (id.includes('/echarts/') || id.includes('/vue-echarts/')) {
                return 'charts'
              }

              if (id.includes('/axios/') || id.includes('/dayjs/')) {
                return 'utilities'
              }
            }

            return undefined
          }
        }
      }
    }
  }
})

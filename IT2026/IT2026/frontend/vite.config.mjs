import { fileURLToPath } from 'node:url'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import ElementPlus from 'unplugin-element-plus/vite'

const __dirname = dirname(fileURLToPath(import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const assetsTarget = env.VITE_PROXY_ASSETS_TARGET || 'http://127.0.0.1:8080'
  const softwareTarget = env.VITE_PROXY_SOFTWARE_TARGET || 'http://127.0.0.1:8081'
  const policyTarget = env.VITE_PROXY_POLICY_TARGET || 'http://127.0.0.1:8082'
  const proxy = {
    '/api/v1/software/all': {
      target: assetsTarget,
      changeOrigin: true
    },
    '/api/v1/software/stats': {
      target: assetsTarget,
      changeOrigin: true
    },
    '/api/v1/discovery': {
      target: assetsTarget,
      changeOrigin: true
    },
    '/api/v1/software': {
      target: softwareTarget,
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

  return {
    plugins: [
      vue(),
      Components({
        resolvers: [ElementPlusResolver()]
      }),
      ElementPlus()
    ],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src')
      }
    },
    css: {
      preprocessorOptions: {
        scss: {
          api: 'modern-compiler'
        }
      }
    },
    https: {
      key: readFileSync(resolve(__dirname, 'certs/zview-key.pem')),
      cert: readFileSync(resolve(__dirname, 'certs/zview-cert.pem')),
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
      proxy
    },
    preview: {
      https: {
        key: readFileSync(resolve(__dirname, 'certs/zview-key.pem')),
        cert: readFileSync(resolve(__dirname, 'certs/zview-cert.pem')),
      },
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
      proxy
    },
    build: {
      chunkSizeWarningLimit: 1200,
      rollupOptions: {
        onwarn(warning, warn) {
          const message = String(warning?.message || '')
          if (message.includes('contains an annotation that Rollup cannot interpret')) {
            return
          }
          warn(warning)
        },
        output: {
          manualChunks(id) {
            if (!id.includes('node_modules')) return undefined

            if (id.includes('/@element-plus/icons-vue/') || id.includes('/element-plus/')) {
              return 'elementPlus'
            }
            if (id.includes('/echarts/') || id.includes('/vue-echarts/')) return 'echarts'
            if (id.includes('/axios/') || id.includes('/dayjs/')) return 'utilities'

            return undefined
          }
        }
      }
    }
  }
})

import { resolve } from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

const backend = 'http://127.0.0.1:4200'

function trimAssetTrailingWhitespace(): Plugin {
  return {
    name: 'trim-asset-trailing-whitespace',
    generateBundle(_, bundle) {
      Object.values(bundle).forEach((asset) => {
        if (asset.type === 'chunk') {
          asset.code = asset.code.replace(/[ \t]+$/gm, '')
        } else if (typeof asset.source === 'string') {
          asset.source = asset.source.replace(/[ \t]+$/gm, '')
        }
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  base: '/static/',
  plugins: [react(), trimAssetTrailingWhitespace()],
  build: {
    outDir: resolve(__dirname, '../app/static'),
    emptyOutDir: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('/react/') || id.includes('/react-dom/') || id.includes('/react-router-dom/')) {
            return 'vendor-react'
          }
          if (id.includes('/@tanstack/')) {
            return 'vendor-query'
          }
          if (id.includes('/antd/') || id.includes('/@ant-design/') || id.includes('/rc-')) {
            return 'vendor-antd'
          }
          return undefined
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: backend,
        changeOrigin: true,
      },
      '/books': {
        target: backend,
        changeOrigin: true,
      },
      '/voicelines': {
        target: backend,
        changeOrigin: true,
      },
      '/designed_voices': {
        target: backend,
        changeOrigin: true,
      },
      '/clone_voices': {
        target: backend,
        changeOrigin: true,
      },
      '/dataset_builder': {
        target: backend,
        changeOrigin: true,
      },
    },
  },
})

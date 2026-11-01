import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// `npm run dev` proxies API calls to a locally running `newtube serve`.
export default defineConfig({
  plugins: [svelte()],
  server: { proxy: { '/api': 'http://127.0.0.1:8787' } },
  build: { outDir: 'dist', sourcemap: false },
})

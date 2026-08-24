import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    // Budget: <=200 KB gzipped for the first route. A Mini App opens on mobile
    // data, inside another app, mid-conversation.
    chunkSizeWarningLimit: 220,
    rollupOptions: {
      output: { manualChunks: { react: ['react', 'react-dom'] } },
    },
  },
  server: {
    // One origin per app: since 2026-07-20 Telegram blocks Mini App method
    // calls from any other origin, and the failures are silent.
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
});

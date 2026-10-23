/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

// Tauri 需要固定端口与静态产物目录
//
// ★ 2026-09-20（主人反复「手机上还是旧样子」）—— 注入**构建号**：
//   打包时间戳打进出包产物，标题页右下角显示。装到手机上第一眼就能确认
//   「这个包到底是哪次构建的」，不用再靠猜（此前已两次怀疑包没打对/装错）。
const BUILD_STAMP = (() => {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${String(d.getFullYear()).slice(2)}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
})();

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  define: {
    __BUILD_STAMP__: JSON.stringify(BUILD_STAMP),
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    watch: {
      // src-tauri 由 cargo 自己监听，避免 vite 重复扫描
      ignored: ['**/src-tauri/**'],
    },
  },
  build: {
    target: 'esnext',
    sourcemap: true,
    chunkSizeWarningLimit: 1500,
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});

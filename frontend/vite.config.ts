import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5175, strictPort: true },
  // maplibre грузит свой воркер отдельным файлом; после предсборки зависимостей
  // путь к нему ломается и карта молча остаётся без тайлов
  optimizeDeps: { exclude: ['maplibre-gl'] },
})

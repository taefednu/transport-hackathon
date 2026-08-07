import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

const require = createRequire(import.meta.url)

/**
 * Кладёт воркер maplibre рядом с бандлом.
 *
 * maplibre вычисляет адрес воркера в рантайме:
 * `new URL('./maplibre-gl-worker.mjs', import.meta.url)`. Имя файла собирается
 * из переменной, поэтому бандлер эту строку статически не видит и файл в сборку
 * не кладёт. В разработке всё работает — там модуль отдаётся прямо из
 * node_modules, и воркер лежит соседним файлом. В собранном виде соседа нет:
 * браузер получает 404, воркер не поднимается, тайлы некому разбирать, и карта
 * остаётся пустой. Всё остальное при этом работает, поэтому поломка выглядит
 * как «карта не прогрузилась», а не как ошибка.
 *
 * Каталог берётся у самого бандла, а не пишется строкой: воркер обязан лежать
 * ровно там, где его ищет `import.meta.url`, и эта связь не должна зависеть от
 * настройки `build.assetsDir`.
 */
function maplibreWorkerAsset(): Plugin {
  const NAME = 'maplibre-gl-worker.mjs'
  return {
    name: 'maplibre-worker-asset',
    apply: 'build',
    generateBundle(_options, bundle) {
      const entry = Object.values(bundle).find(
        (chunk) => chunk.type === 'chunk' && chunk.isEntry,
      )
      if (!entry) throw new Error('не нашёл входной чанк — некуда класть воркер maplibre')
      const dir = entry.fileName.includes('/')
        ? entry.fileName.slice(0, entry.fileName.lastIndexOf('/') + 1)
        : ''
      this.emitFile({
        type: 'asset',
        fileName: `${dir}${NAME}`,
        source: readFileSync(require.resolve(`maplibre-gl/dist/${NAME}`)),
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), maplibreWorkerAsset()],
  server: { port: 5175, strictPort: true },
  // В разработке модуль должен отдаваться из node_modules как есть: после
  // предсборки зависимостей воркер ищется рядом с файлом предсборки, где его
  // нет, и карта так же молча остаётся без тайлов.
  optimizeDeps: { exclude: ['maplibre-gl'] },
})

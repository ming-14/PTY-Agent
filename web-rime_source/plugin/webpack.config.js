import path from 'node:path'
import { fileURLToPath } from 'node:url'
import CopyPlugin from 'copy-webpack-plugin'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const distDir = path.resolve(__dirname, 'dist')

// AGENTS.md：plugin 产物需同步到的两个目录
const copyTargets = [
  path.resolve(__dirname, '../server/__debug_statics/plugin/dist'),
  path.resolve(__dirname, '../../src/web/static/vendor/rime'),
]

// 浏览器端插件，纯内部模块、无外部 npm 依赖，全部打包进产物
const common = {
  entry: './src/index.ts',
  target: ['web', 'es2020'],
  mode: 'production',
  devtool: 'source-map',
  resolve: {
    extensions: ['.ts', '.js'],
  },
  module: {
    rules: [
      {
        test: /\.ts$/,
        loader: 'esbuild-loader',
        options: { target: 'es2020' },
      },
    ],
  },
  optimization: {
    // 与 rollup 配置保持一致：不压缩
    minimize: false,
  },
}

/** @type {import('webpack').Configuration[]} */
export default [
  // 1) IIFE，挂全局变量 RimePlugin，供静态 HTML 用 <script> 引入
  {
    ...common,
    output: {
      path: distDir,
      filename: 'rime-plugin.js',
      library: {
        name: 'RimePlugin',
        type: 'var',
      },
      compareBeforeEmit: false,
      clean: false,
    },
  },
  // 2) ESM + 复制：ESM 产物写盘后，CopyPlugin 一次性同步 dist/ 到两个目标目录。
  //    因 webpack 顺序执行配置，此时 IIFE 产物已在 dist/ 中，两份文件都会被复制。
  //    无需第三个配置重新编译入口，节省约 1.3 秒构建时间。
  //    注意：两个配置共享同一 output.path，必须显式设置 clean:false，
  //    否则 webpack 5 在第二个配置 emit 时会清理 dist/ 目录，
  //    导致第一个配置已写入的 rime-plugin.js 被截断为 0 字节。
  {
    ...common,
    experiments: {
      outputModule: true,
    },
    output: {
      path: distDir,
      filename: 'rime-plugin.esm.js',
      library: {
        type: 'module',
      },
      compareBeforeEmit: false,
      clean: false,
    },
    plugins: [
      new CopyPlugin({
        patterns: copyTargets.map((to) => ({
          from: distDir,
          to,
        })),
      }),
    ],
  },
]

# RIME Pinyin Plugin

RIME 拼音输入法前端 JS 插件，支持静态 HTML 页面直接引入使用。

## 特性

- 仅支持朙月拼音（`luna_pinyin`）输入方案（五笔画 `stroke` 作为其依赖词库加载）
- 词库懒加载，按需从本地 `dict/` 目录加载（server 端由 `RIME_DICT_DIR` 指定；WASM 端为 `wasm/` 同级目录）
- 两种模式：远程（WebSocket）和本地（WASM）
- 远程模式无 AGPLv3 传染风险
- 单文件 IIFE，可直接 `<script>` 引入

## 项目结构

```
web_rime/
├── plugin/            # 前端 JS 插件（webpack 双产物：IIFE + ESM）
│   ├── src/
│   │   ├── backends/  # 双后端：remote.ts（WebSocket）/ wasm.ts（Worker）
│   │   ├── keyboard/  # 软键盘（dom/layouts/render/theme/touch/viewport，含 tkl/）
│   │   ├── ime.ts     # RimeIME 门面（对外 API 入口）
│   │   └── index.ts manager.ts panel.ts toolbar.ts types.ts
│   └── package.json webpack.config.js tsconfig.json
├── server/            # 远程模式服务器（Node.js + ws）
│   ├── src/           # index.ts rime-engine.ts dict-manager.ts ws-handler.ts
│   ├── wasm/          # rime.js / rime.data（WASM 引擎二进制）
│   └── dict/          # 词库目录（luna-pinyin/、stroke/ 等）
├── rime-config/       # RIME 配置（rime.lua + lua/ 脚本）
└── docs/              # 设计文档
```

## AGPLv3 说明

| 组件 | 协议 | 说明 |
|---|---|---|
| plugin（不含 WASM 后端） | MIT | 无 AGPLv3 代码 |
| plugin WASM 后端 | AGPLv3 | 可选加载，使用时触发 |
| server | AGPLv3 | 独立进程，通过网络通信 |
| RIME WASM 二进制 | GPLv3 | librime 编译产物 |

**远程模式下，前端插件完全不包含 AGPLv3 代码**，通过 WebSocket 网络边界隔离。

## 快速开始

### 远程模式（推荐，无 AGPLv3 风险）

1. 启动服务器：

```bash
cd server
npm install
# 将 rime.js, rime.wasm, rime.data 放入 wasm/ 目录
# 将词库放入 dict/（luna-pinyin/、stroke/ 子目录，缺失会导致 schema 加载失败）
npm run build
npm start
```

2. 在 HTML 中使用：

```html
<script src="rime-plugin.js"></script>
<script>
  const ime = new RimePlugin.RimeIME({
    mode: 'remote',
    serverUrl: 'ws://localhost:3000',
    schema: 'luna_pinyin',
    pageSize: 5
  });

  await ime.init();

  // 处理按键
  const result = await ime.processKey('a');

  // 选词
  await ime.selectCandidate(0);

  // 翻页
  await ime.changePage(false);

  // 监听上屏
  ime.onCommit(text => {
    document.getElementById('input').value += text;
  });

  // 销毁
  ime.destroy();
</script>
```

### WASM 模式（AGPLv3 适用于你的前端代码）

```html
<script src="rime-plugin.js"></script>
<script>
  const ime = new RimePlugin.RimeIME({
    mode: 'wasm',
    wasmUrl: './rime/',  // rime.js, rime.wasm, rime.data 所在目录；同级 ./dict/ 存放词库
    schema: 'luna_pinyin'
  });

  await ime.init();
  // 其余 API 相同
</script>
```

## API

### `new RimeIME(config)`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | `'remote' \| 'wasm'` | 是 | 后端模式 |
| `serverUrl` | `string` | remote 必填 | WebSocket 服务器地址 |
| `wasmUrl` | `string` | wasm 必填 | WASM 文件目录 URL |
| `schema` | `string` | 否 | 输入方案，默认 `luna_pinyin` |
| `pageSize` | `number` | 否 | 候选词数量，默认 5 |

### 方法

| 方法 | 返回 | 说明 |
|---|---|---|
| `init()` | `Promise<void>` | 初始化 |
| `processKey(key)` | `Promise<RimeResult>` | 处理按键 |
| `selectCandidate(index)` | `Promise<RimeResult>` | 选择候选词 |
| `changePage(backward)` | `Promise<RimeResult>` | 翻页 |
| `setOption(option, value)` | `Promise<void>` | 设置选项 |
| `setIME(schema)` | `Promise<RimeResult>` | 切换输入方案 |
| `setPageSize(size)` | `Promise<void>` | 设置候选词数量 |
| `deploy()` | `Promise<void>` | 重新部署配置 |
| `onCommit(cb)` | `void` | 监听上屏 |
| `onOptionChange(cb)` | `void` | 监听选项变化 |
| `onSchemaChange(cb)` | `void` | 监听方案变化 |
| `onDeployStatus(cb)` | `void` | 监听部署状态 |
| `onResultChange(cb)` | `void` | 监听每次结果变化 |
| `onError(cb)` | `void` | 监听错误 |
| `offCommit(cb)` | `void` | 取消监听 |
| `destroy()` | `void` | 销毁实例 |

### RimeResult

```typescript
{
  state: 'committed' | 'accepted' | 'rejected' | 'unhandled'
  composition: { head: string, body: string, tail: string }
  candidates: Array<{ text: string, comment: string }>
  committed: string
  page: number
  isLastPage: boolean
  highlighted: number
  selectLabels: string[]
  updatedOptions: Record<string, boolean>
  updatedSchema: string
}
```

### 支持的拼音方案

| schema ID | 名称 |
|---|---|
| `luna_pinyin` | 朙月拼音 |

`stroke`（五笔画）不作为独立方案，仅在加载 `luna_pinyin` 时作为依赖词库自动加载。

## 服务器配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `RIME_PORT` | `3000` | WebSocket 端口 |
| `RIME_WASM_DIR` | `./wasm` | WASM 文件目录 |
| `RIME_DICT_DIR` | `./dict` | 词库目录（`luna-pinyin/`、`stroke/` 子目录） |

## 构建

```bash
# 插件
cd plugin
npm install
npm run build
# 输出: dist/rime-plugin.js (IIFE) + dist/rime-plugin.esm.js (ESM)
# 构建产物还会同步到主项目 src/web/static/vendor/rime/ 与 server/__debug_statics/plugin/dist/

# 服务器
cd server
npm install
npm run build
```

## WASM 二进制获取

从 [My RIME Releases](https://github.com/LibreService/my_rime/releases) 下载 `my-rime-dist.zip`，解压后提取 `rime.js`、`rime.wasm`、`rime.data` 三个文件。

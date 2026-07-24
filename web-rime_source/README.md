# RIME Pinyin Plugin

RIME 拼音输入法前端 JS 插件，支持静态 HTML 页面直接引入使用。

## 特性

- 仅支持拼音输入（朙月拼音、袖珍简拼、双拼、地球拼音）
- 词库懒加载，按需下载
- 两种模式：远程（WebSocket）和本地（WASM）
- 远程模式无 AGPLv3 传染风险
- 单文件 IIFE，可直接 `<script>` 引入

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
    wasmUrl: './rime/',  // rime.js, rime.wasm, rime.data 所在目录
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
| `onCommit(cb)` | `void` | 监听上屏 |
| `onOptionChange(cb)` | `void` | 监听选项变化 |
| `onSchemaChange(cb)` | `void` | 监听方案变化 |
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
| `pinyin_simp` | 袖珍简拼 |
| `double_pinyin` | 自然码双拼 |
| `terra_pinyin` | 地球拼音 |

## 服务器配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `RIME_PORT` | `3000` | WebSocket 端口 |
| `RIME_WASM_DIR` | `./wasm` | WASM 文件目录 |
| `RIME_CDN_BASE` | jsdelivr CDN | 词库 CDN 地址 |

## 构建

```bash
# 插件
cd plugin
npm install
npm run build
# 输出: dist/rime-plugin.js (IIFE) + dist/rime-plugin.esm.js (ESM)

# 服务器
cd server
npm install
npm run build
```

## WASM 二进制获取

从 [My RIME Releases](https://github.com/LibreService/my_rime/releases) 下载 `my-rime-dist.zip`，解压后提取 `rime.js`、`rime.wasm`、`rime.data` 三个文件。

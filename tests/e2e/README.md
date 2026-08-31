# 前端 e2e 测试

Playwright 驱动真实 `index.html` + 假 WS 后端（`mock_backend.mjs`），无需启动真实 daemon。

覆盖：
- `test_xterm_ime.mjs` — IME 组合回归（组合视图钳制、无横向滚动/位移，回归 xterm 组合 bug）
- `test_xterm_regression.mjs` — 终端集成回归（frame 跟随、窗口 resize、Ctrl+滚轮缩放、主题、会话结束）

## 运行

```bash
cd tests/e2e
npm install          # 安装 ws（playwright 可全局安装或项目内安装）
node mock_backend.mjs            # 终端 1：假后端（http://127.0.0.1:8124）
node test_xterm_ime.mjs          # 终端 2：IME 回归
node test_xterm_regression.mjs   # 终端 2：集成回归
```

## 环境

- 浏览器：自动探测本机 Edge/Chrome；可用 `E2E_BROWSER` 环境变量指定
- Playwright：优先项目内安装，回退全局 `npm root -g`
- 端口：`E2E_PORT` 环境变量（默认 8124）
- 依赖 CDP `Input.imeSetComposition` 驱动真实 IME 组合路径（合成 DOM 事件无法复现
  Chromium 焦点滚动行为）

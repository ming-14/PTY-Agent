# Web 终端架构（终局）— ttyd 式

## 架构

\\\
后端 pywezterm = 终端模型权威
  ├─ 输出流：feed 模型 + 转发前端（不变）
  ├─ resize：模型 reflow → 返回 (snapshot, scrollback)
  │   └─ drop_feed 窗口丢弃 ConPTY repaint（防污染模型 scrollback）
  └─ scrollback：模型累积维护（不 clear），供订阅恢复使用

前端 xterm = 显示终端（ttyd 式）
  ├─ resize：xterm 自身 reflow（scrollback 自动适配宽度）
  │   + 后端 snapshot 更新可见区（\x1b[2J + snapshot）
  │   └─ 不重建 scrollback（保留前端累积）
  ├─ 订阅恢复（刷新/断连）：后端 capture scrollback +
  │   \x1b[3J + scrollback + \r\n + snapshot 一次性重建
  └─ 无 windowsMode、无 trim/去重逻辑
\\\

## 核心设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| scrollback 源 | 前端 xterm 单一源（ttyd 式） | 避免双模型冲突；xterm 自身 reflow 处理宽度适配，瑕疵（行尾空格行偶尔不合并）可接受 |
| resize 重建 | 不重建 | 只更新可见区（snapshot），scrollback 保留前端累积 |
| 订阅恢复 | 重建（一次性） | 刷新/断连后保留历史 |
| repaint | drop_feed 丢弃 | 模型 feed 丢弃旧宽度整屏重画（防污染 scrollback），snapshot 来自模型权威 |
| 去重 | 无（不需要） | resize 不重建则无"推入 scrollback 尾部与可见区重叠"问题 |

## 关键文件

| 文件 | 职责 |
|------|------|
| \	erminal/screen.py\ | TerminalScreen（feed/drop_feed/resize/capture_scrollback） |
| \session/output.py\ | Session.resize（drop_feed 窗口 + 返回 scrollback+snapshot） |
| \infrastructure/terminal/lifecycle.js\ | restoreScrollbackAndSnapshot（订阅重建/ resize 可见区更新） |
| \pplication/messageHandlers.js\ | handleResizeComplete/handleSessionResized（ttyd 式：不重建） |

# Web 终端重构计划

## 1. 现状架构

### 1.1 后端 `src/web/*`（洋葱架构）

```
presentation/          ← FastAPI + uvicorn
  server.py            WebServer 主入口，uvicorn 后台线程
  controllers/         WS 消息路由、认证
application/           ← 用例层
  handlers/            30 种 WS 消息处理器（按领域分组）
  dispatcher.py        消息分发
  ports.py             端口抽象
  services.py          编码器/订阅服务
  adaptive_lock.py     自适应排他锁
domain/                ← 实体层
  entities.py          ActiveSession/HistorySession（含 uid）
  settings_schema.py
infrastructure/        ← 接口适配层
  auth/                登录会话
  repositories/        SQLite 历史仓库、会话仓库适配器
  web/                 FastAPI 传输、事件广播、连接上下文
  system/              Shell/Stats 提供者
  thread_executor.py
  cursor_locator_adapter.py
```

### 1.2 前端 `src/web/static/js/*`（干净架构）

```
domain/                state.js, i18n.js, constants.js, logger.js, settingsSchema.js
application/           messageHandlers.js, ports.js, settingsStore.js
infrastructure/        wsClient.js, terminalAdapter.js, terminal/*, rimeManager.js, auth.js, etc.
presentation/          events.js (controllers), views/* (ui/detail/vnc/fastscreen/settings/sizeSelector/autohide)
vendor/                xterm.js, noVNC, rime, logpanel, rikkajs
```

### 1.3 通信协议

- 单一 WS 端点 `/ws`（~30 消息类型）
- REST: `/api/auth`, `/api/settings`, `/login`
- `/vnc/websockify` WS→TCP 代理
- Screenshare 流媒体端点

### 1.4 会话身份模型

- `session.id` (sid)：用户自定义标识（如 "cmd"），CLI 层使用
- `session.uid`：uuid4，后端自动生成，唯一不变
- 后端 `SessionManager` 按 sid 索引
- 前端 `state.sessions`/`state.history` 按 sid 索引
- 尺寸配置 `state.sessionSizeConfigs` 按 uid 索引
- 历史 SQLite 表主键 = sid

## 2. Bug 分析

### 实证验证（2026 运行中 daemon 实测）

**Bug #1 实测**（WS 直连 daemon，`python -u -i` 会话打印 200 行后）：

```
[3] PRE-resize model:  scrollback_len=2437 lines=174   ← 模型 scrollback 存在
[4] resize_complete:   snapshot_len=778 scrollback_len=0 ← 响应不携带 scrollback
[5] POST-resize model: scrollback_len=0 lines=0        ← 模型 scrollback 被清空
```

**Bug #3 实测**（临时 SQLite，两个同名 "cmd" 会话先后归档）：

```
archive A (uid-A) ok=True, archive B (uid-B) ok=True
total history records after both archived: 1   ← 旧记录被覆盖
record: id='cmd' uid='uid-B'                    ← uid-A 记录已丢失
```

### Bug #1：Resize 清空 scrollback

**严重度**：严重 — 每次 resize（包括自适应模式窗口尺寸变化）丢失全部滚动历史

**根因**（3 重丢失）：
1. `OutputMixin.resize()` (output.py:294) → `clear_scrollback()` 主动清空模型 scrollback
2. `ResizeHandler` (session.py:544) → 再次 `clear_scrollback()` 防 repaint 残余
3. `ResizeHandler` (session.py:547) 硬编码 `scrollback_ansi = ""`，响应不携带 scrollback
4. 前端 `restoreScrollbackAndSnapshot` 无 scrollback 时走模式 B → `\x1b[3J` 清 xterm 滚动

**影响范围**：所有 resize 路径（resize 消息、size_mode fixed/custom、程序 CSI 8 resize）

### Bug #2：鼠标模式时常失效

**严重度**：严重 — vim/htop 等 TUI 应用中鼠标点击/滚轮频繁失效，需刷新页面或重连

**根因**（多个失效路径）：
1. **Resize 后 DECSET 状态丢失**：`restoreScrollbackAndSnapshot` 写入的 snapshot 是终端模型渲染内容（render_ansi），不包含 DECSET 模式序列（如 `\x1b[?1002h`）。前端 `detectAppMouseModeFromOutput` 依赖输出流中的 DECSET 检测，snapshot 重建后 `appMouseModeDecset` 不变（保持旧值或 false），但下一个输出数据可能不含 DECSET → DECSET 检测不会重新触发。若重建前模式已启用，重建后前端仍为 true（侥幸正确）；若重建前已禁用但 TUI 仍启用了（如挂起后恢复），则前端丢失。
   - 修复方向：`resize_complete`/`session_resized` 响应增加 `appMouseMode` 字段，前端 `handleResizeComplete`/`handleSessionResized` 调用 `setAppMouseMode` 同步。
2. **`appMouseModeDaemon` 仅订阅时设置，resize 后未更新**：`setAppMouseMode` 仅由 `handleSubscribed` 调用（messageHandlers.js:307）。resize、session_resized、size_mode_ack 等路径均不更新。若 TUI 在订阅后改变了鼠标模式，resize 后前端仍用订阅时的旧值。
   - 修复方向：同上，让后端在 resize 响应中携带当前模式。
3. **`mouseInputOverride` 用 uid 持久化，`setAppMouseMode` 用 sid 索引**：`_saveMouseOverride(uid, value)`（mouseMode.js:29）vs `setAppMouseMode(sid, enabled)`（mouseMode.js:117）——sid 复用后侧栏存储的 uid 对应错误对象。
   - 修复方向：P1 uid 改造后自然消除。
4. **`trackAppMouseMode` 绑定时机**：`trackAppMouseMode(term, inst)` 在 `ensureTerminal` 中调用（lifecycle.js:107），但若 ensureTerminal 过程中 `term.write` 被替换（确保单次），绑定时序正确。但 `term.write` 的原始引用被缓存，任何后续替换 `term.write` 的代码都会绕过 DECSET 检测。目前无此问题。
5. **`handleResizeComplete` 丢弃 `_resizeBuffer` 时可能包含 DECSET 序列**：丢弃的缓冲输出中包含 DECSET 序列，但这些序列来自旧尺寸、已被 snapshot 取代，丢失不影响模型状态。
6. **后端 `WeztermInputEncoder.mouse()` 返回空 = 前端鼠标事件无响应**：前端发送的原始鼠标事件，后端编码由终端模型状态决定。若模型状态正确（`is_mouse_tracking()=true`），编码应正常。但若模型状态与前端状态不一致（如前端认为已启用、后端模型未启用），鼠标事件静默丢弃。这是 1) 和 2) 的最终结果：前端认为启用了（`appMouseModeDecset` 残留 true），但后端模型在 resize 时由于时序问题可能丢失了模式状态？不——模型状态在 resize 时保留（模型是同一实例，resize 不改变 DECSET 状态）。所以后端模型状态始终正确，问题是前端不知道。

**结论**：核心修复是 resize 响应携带 `appMouseMode` 字段，前端同步更新。P1 uid 改造消除 `mouseInputOverride` 的 sid/uid 不一致。

**子代理 B 调研补充**：
- **根因 3（stale daemon 标志，core bug）**：`syncAppMouseMode` 取 `decset || daemon`。DECSET OFF 只清 `appMouseModeDecset`，`appMouseModeDaemon` 仅在订阅时设置、永不清理。vim 退出后 decset=false 但 daemon 仍 true → `appMouseMode` 恒 true → wheel 永远走 `sendVtWheelEvent` 且 `return true`（events.js:335-342），视口滚动 fallback（events.js:344-347）不可达 → shell 中滚轮、点击选择、右键菜单全失效，直到重连。**这是用户最常遇到的"鼠标模式失效"现象**（退出 TUI 后滚轮锁死）。
- **根因 4（前端 write 拦截无跨块缓冲）**：DECSET 序列跨 PTY read / 跨 WS 消息分片时，前端正则检测不到 → `appMouseMode` 保持 false → 鼠标事件根本不发。后端 wezterm 有 64B `mode_tail` 窗口拼接，前端无。
- **根因 5（resize 丢弃缓冲含 DECSET）**：`_resizeBuffer` 丢弃（messageHandlers.js:710-714）→ 模式状态与后端脱节。
- **根因 6（`setAppMouseMode` 早退竞态）**：`handleSubscribed` 在 `ensureTerminal` 创建 inst 之前调用，inst 不存在时静默 return（mouseMode.js:117-119）。已订阅分支 replay="" 无 DECSET 可恢复，模式彻底丢失。

**修复方向**：
1. 后端成为唯一权威源并主动推送鼠标模式变化事件（`mouse_mode` 推送）
2. 前端 DECSET 检测加跨块尾部缓冲（仿后端 64B 窗口）
3. DECSET OFF 应直接覆盖 daemon 标志（或 daemon 仅作为订阅初始猜测）
4. resize 响应携带 `mode_restore_seq` 或 `appMouseMode`
5. 修复 `setAppMouseMode` 早退（inst 创建后回填）
6. 清理死代码（`appMouseEncoding`、`term._appMouseMode`、不存在的 mouse_mode 事件分支）

### Bug #3：sid/uid 会话识别污染

**严重度**：严重 — 同名会话（如两个 "cmd"）的历史记录互相覆盖、前端内容混写"串台"

**根因**（同名 sid 复用链）：
1. **历史归档按 sid 覆盖删除**：`history_store.py:120-123` `DELETE FROM sessions WHERE id = ?` 后重新 INSERT，主键 = sid。旧同名会话的历史记录被新会话覆盖删除 → 旧历史永久丢失。
2. **前端 `openSessionInTab` 混写**：`ui.js:256-261` 当 `state.sessions["cmd"]`（活跃新会话）与 `state.history["cmd"]`（旧历史）同时存在时，直接把活跃会话对象标记 `history=true/running=false`，随后渲染旧会话内容（history_detail），而新会话输出仍写入同一 `termInstances["cmd"]` → 新旧内容混写。
3. **订阅/回调/广播/锁全部按 sid 键控**：`connection_context.py:28-33`、`event_publisher.py:147`、`adaptive_lock.py:35` —— sid 复用后串扰。
4. **前端状态键控混杂**：`state.sessions`/`state.history`/`termInstances`/`tabOrder`/`sessionFontSizes`/`pendingCreates`/`localAdaptiveOwnerSids` 按 sid（state.js:101-122），而 `sessionSizeConfigs`/frameRatio/鼠标 override 按 uid（state.js:212-251）——不一致导致 `getSessionSizeConfigBySid`（state.js:224-227）依赖可能被污染的 `sessions[sid].uid`。
5. **协议缺口**：`ws_subscribed` 响应不带 uid（response.py:202），前端 uid 依赖 list/session_created 事后补齐（messageHandlers.js:110,124,460）。

### 附加 Bug

- `_resizeBuffer` 丢弃导致 resize 过渡期间输出丢失
- 200ms repaint 等待启发式不精确
- 程序 resize 路径同样清空 scrollback
- 非发起方先收到 repaint 字节再被重建覆盖（短暂闪烁）
- `SetSizeModeHandler` fixed/custom 模式同一 bug（size_mode.py:131）
- 后端 `AdaptiveLockService` 和 `ConnectionContext` 均按 sid 索引，sid 重复时锁/回调可能串

## 3. 目标架构

### 3.1 身份模型：uid 为主键

**原则**：前端、后端内部、数据库均以 uid 为唯一标识，sid 仅作为用户展示名

改动范围：
- 后端 `SessionRepository` 接口：`get_session` 支持 uid 查找
- 后端 `ConnectionContext`：订阅/回调/解码器 key 从 sid 改为 uid
- 后端 `AdaptiveLockService`：key 从 sid 改为 uid
- 后端 `HistoryStore`：主键改为 uid，sid 为普通列
- 前端 `state.sessions`/`state.history`/`termInstances`：key 从 sid 改为 uid
- WS 协议消息：消息体增加 `sessionUid` 字段，旧 `sessionId` 保留用于兼容
- 前端标签/侧边栏渲染按 uid 查找，显示 sid（用户可见名）

### 3.2 Resize 保留 scrollback

**时序**（核心设计）：
```
model.resize → capture_scrollback（干净 reflow 历史）→ pty.resize → 等 repaint → clear_scrollback（仅清污染）→ snapshot
返回 (snapshot, scrollback)
```

**要求**：
- `Session.resize()` 返回 (snapshot, scrollback) 元组
- `ResizeHandler` 响应携带 scrollback
- `publish_session_resized` 广播携带 scrollback
- 前端 `restoreScrollbackAndSnapshot` 模式 A 已有恢复逻辑，无需改动
- subscriber 初始订阅路径不变

### 3.3 鼠标模式可靠

**原则**：后端终端模型是鼠标模式唯一权威源，前端不再依赖字节流嗅探恢复模式。

- 后端在模式变化时向订阅连接主动推送 `mouse_mode` 事件（reader 线程模式监控，P3 已实现）
- resize 响应（resize_complete/session_resized）携带 `appMouseMode`
- 前端 DECSET 检测仅作实时指示，模式状态以后端推送为准
- DECSET OFF 直接覆盖 daemon 标志（daemon 仅作订阅初始猜测）
- 修复 `setAppMouseMode` 早退与 override 持久化时序

**重大发现（P3 实测）**：Windows ConPTY 不把 DECSET 1000/1002/1003/1049 等序列
转发到 ConPTY 输出流（OpenConsole 消费后仅渲染其效果，原始序列不可见）。
因此 wezterm 终端模型在 Windows 上永远无法跟踪鼠标追踪/备用屏幕模式，
`is_mouse_tracking()`/`is_alt_screen()` 恒为 False —— 前端 DECSET 检测与
后端推送在 Windows 上均不会触发，鼠标模式检测架构性失效（历史遗留问题）。
修复方向（后续专项）：Windows 输入侧直接写 SGR 鼠标序列（ConPTY 自身按
应用声明的模式门控投递，无需模型知道模式）+ 前端按"活动会话+用户交互"
启发式决定是否发送鼠标事件。

## 4. 实施计划

分阶段实施，每阶段独立可验证：

| 阶段 | 内容 | 验证 |
|------|------|------|
| P1 | uid 主标识改造（Bug #3） | 同名会话隔离 e2e |
| P2 | resize 保留 scrollback（Bug #1） | resize scrollback e2e |
| P3 | 鼠标模式可靠（Bug #2） | 鼠标模式 e2e |
| P4 | 附带 bug 修复 | 回归测试 |
| P5 | 测试补齐 | 单元 + e2e 全量 |

详见 `design/WEB-TERMINAL-REFACTOR-PLAN.md`。
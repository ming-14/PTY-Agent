# Web 终端重构实施计划

> 配套文档：`docs/WEB-TERMINAL-REFACTOR.md`（架构与 Bug 分析）
> 目标：修复 web 终端三大严重 bug + 附带问题，重构为 uid 主标识、scrollback 保留、鼠标模式可靠的架构。

## 进度总览

| 阶段 | 状态 | 说明 |
|------|------|------|
| P1a 后端 uid 改造 | ✅ 完成 | manager/仓库/连接上下文/锁/历史存储/协议/处理器全部 uid 路由，22 单元测试 + 2 e2e 通过，真实 DB 迁移成功 |
| P1b 前端 uid 改造 | ✅ 完成 | state/wsClient/messageHandlers/sessionHandlers/ui/lifecycle/terminal/*/rime/fontLoader/autohide 全部 uid 键 + sessionUid 出站；34 文件语法通过，74 web 单元测试 + 3 e2e 通过 |
| P2 resize 保留 scrollback | ✅ 完成 | Session.resize() 返回 (snapshot, scrollback)；ResizeHandler/SetSizeModeHandler/程序 resize 三路携带；6 单元测试 + 2 e2e 通过 |
| P3 鼠标模式可靠 | ✅ 完成（含重大发现） | 后端 mouse_mode 事件推送（reader 线程模式监控，5 单元测试）；前端修复：DECSET 跨块缓冲/DECSET OFF 清 daemon/早退暂存回填。**重大发现**：Windows ConPTY 不转发 DECSET 1000/1002/1003/1049 到输出流（实测确认），模型永远无法跟踪鼠标/备用屏模式 → Windows 上鼠标模式检测架构性失效（历史遗留，非本次引入），修复需在输入侧直接写 SGR 序列 + 前端改发送策略，列为后续专项 |
| P4 附带 bug 修复 | ✅ 完成 | `_resizeBuffer` 不丢弃改重建后写入（修复 resize 期间输出丢失，messageHandlers + lifecycle 两处）；`SetSizeModeHandler` 补自适应锁校验（后端防线，5 单元测试） |
| P5 测试补齐 | ✅ 完成 | 全量回归：1848 单元测试 + 5 e2e 全绿；协议一致性审计（resolveMsgUid 优先 msg.uid 权威字段，修复历史详情同名 sid 路由）；34 前端文件语法检查通过 |
| P3 鼠标模式可靠 | ⬜ 待做 | 后端推送 + 前端同步修复 |
| P4 附带 bug 修复 | ⬜ 待做 | _resizeBuffer 丢输出等 |
| P5 测试补齐 | ⬜ 待做 | 全量回归 |

## 阶段划分

| 阶段 | 内容 | 涉及 | 风险 |
|------|------|------|------|
| P1 | uid 主标识改造（Bug #3） | 后端 session/manager、web 全层、前端 state/消息路由 | 高（协议改动） |
| P2 | resize 保留 scrollback（Bug #1） | 后端 session/output、web handlers、前端（少量） | 中 |
| P3 | 鼠标模式可靠（Bug #2） | 后端 input/terminal、前端 mouseMode/events | 中 |
| P4 | 附带 bug 修复 | _resizeBuffer 丢输出、程序 resize、repaint 等待 | 低 |
| P5 | 测试补齐 | 单元 + e2e | 低 |

## P1：uid 主标识改造

### 目标

- 后端会话活跃表、历史存储、订阅/回调/锁全部以 uid 为唯一键
- sid 仅作为用户展示名与 CLI 操作名
- 前端所有状态（sessions/history/termInstances/tabOrder/sessionFontSizes/pendingCreates/localAdaptiveOwnerSids）以 uid 为键

### 后端改动

1. `src/session/manager.py`
   - `_sessions` key：sid → uid；新增 `_sid_index: {sid: uid}`（一个 sid 只允许一个活跃会话）
   - `get_session(uid)` 改 uid 查找；新增 `resolve_sid(sid) -> uid`（不存在/重名歧义时抛错）
   - `create_session`：生成 uid 前置（由 Session 内部生成），注册到 `_sessions[uid]` + `_sid_index[sid]`
   - 回调 `_on_session_created(uid, sid)` / `_on_session_removed(uid, sid, ...)` 签名扩展
   - `list_sessions()` 返回 uid + sid
2. `src/web/infrastructure/repositories/history_store.py`
   - SQLite `sessions.id` 主键改 uid（迁移：加 uid 列→唯一索引→旧数据保留，新写入按 uid）
   - 所有查询/删除按 uid
3. `src/web/infrastructure/web/connection_context.py`
   - `_subscribed_session_ids`/`_decoders`/`_callbacks_by_sid`/`_held_sessions` key 改 uid
4. `src/web/application/adaptive_lock.py`
   - `_owners` key 改 uid
5. WS 协议（`src/protocol/response.py`）
   - 出站消息统一携带 `sessionUid`（保留 `sessionId` 展示用）
   - `ws_subscribed` 增加 uid 字段
6. 处理器（`src/web/application/handlers/*`）
   - 入站消息 `session_id` → 后端 resolve 为 uid 后操作（兼容旧字段名）

### 前端改动

1. `src/web/static/js/domain/state.js`
   - `sessions`/`history`/`termInstances`/`tabOrder`/`sessionFontSizes`/`pendingCreates`/`localAdaptiveOwnerSids` key 改 uid
   - 会话对象保留 `sid` 字段（展示）
   - `getSessionSizeConfigBySid` 直接按 uid
2. `src/web/static/js/application/messageHandlers.js`
   - 消息路由按 `sessionUid` 查找；`session_created`/`session_removed` 处理按 uid
3. `src/web/static/js/presentation/views/ui.js`
   - 标签/侧边栏渲染按 uid 查找，显示 sid
   - `openSessionInTab` 修复 sid 复用混写 bug
4. `src/web/static/js/infrastructure/terminal/lifecycle.js`
   - `ensureTerminal`/`disposeTerminal` 按 uid 管理实例
   - wsSend 消息带 sessionUid
5. 其他：`sizeSelector.js`/`detail.js`/`vnc.js`/`fastscreen.js`/`sessionHandlers.js`/`autohide.js` 按 uid 路由

### CLI 兼容

- CLI 保持 sid 接口；后端 `resolve_sid` 消歧（重名时明确报错提示用完整 sid 或 uid）

## P2：resize 保留 scrollback

### 后端改动

1. `src/session/output.py`
   - `resize()` 时序改为：`model.resize → capture_scrollback（干净 reflow 历史）→ pty.resize → 等 repaint → clear_scrollback（仅清污染行）→ snapshot`
   - 返回 `(snapshot, scrollback)` 元组（或新增 `resize_with_scrollback()`，旧接口转发）
   - `clear_scrollback` 仅用于清 repaint 污染（保证下次订阅/--full 正确）
2. `src/web/application/handlers/session.py`
   - `ResizeHandler`：接收 scrollback，响应 `resize_complete` 携带；`publish_session_resized` 携带
   - 移除二次 `clear_scrollback` 后的硬编码 `""`
3. `src/web/application/handlers/size_mode.py`
   - `SetSizeModeHandler` fixed/custom 分支同样携带 scrollback（移除 clear + 硬编码）
4. `src/session/output.py` `_apply_program_resize`：`notify_resized` 携带 scrollback

### 前端改动（少量）

- `handleResizeComplete`/`handleSessionResized` 已支持模式 A，无需改动
- `restoreScrollbackAndSnapshot` 模式 A 已实现（lifecycle.js:287-310）

## P3：鼠标模式可靠

### 根因摘要（详见架构文档 Bug #2）

1. **stale daemon 标志**：`appMouseModeDaemon` 订阅时设置、永不清理；DECSET OFF 只清 decset → 退出 TUI 后 `appMouseMode` 恒 true → 滚轮/点击/右键全失效
2. **前端 DECSET 检测无跨块缓冲**：分片序列检测不到 → 模式不启用
3. **resize 丢弃缓冲含 DECSET**：模式与后端脱节
4. **`setAppMouseMode` 早退竞态**：inst 未创建时静默 return
5. **mouseInputOverride sid/uid 混杂**

### 后端改动

1. 新增鼠标模式变化推送：终端模型模式变化时向订阅连接推送 `mouse_mode` 事件（mode + sgr + ps），前端据此更新（后端为权威源）
2. `resize_complete`/`session_resized`/`size_mode_ack` 响应携带 `appMouseMode`（后端 `is_mouse_tracking()`）
3. 评估：模式变化检测下沉 pywezterm（TermModeState 变化回调）或轮询对比

### 前端改动

1. `mouseMode.js`
   - DECSET 检测加跨块尾部缓冲（仿后端 64B 窗口）
   - DECSET OFF 直接覆盖 daemon 标志（或 daemon 仅作初始猜测）
   - 新增 `handleMouseModeMsg` 处理后端推送
2. `messageHandlers.js`
   - `handleResizeComplete`/`handleSessionResized` 调 `setAppMouseMode`
   - 修复 `setAppMouseMode` 早退（inst 创建后回填）
3. `lifecycle.js`
   - `getInitialMouseOverride` 在 uid 就绪后重读应用
4. 清理死代码：`appMouseEncoding`、`term._appMouseMode`、不存在的 mouse_mode 事件分支

## P4：附带 bug 修复

- `_resizeBuffer` 丢弃窗口：改为"snapshot 去重后保留差异"或缩短窗口
- 200ms repaint 等待：评估精确 repaint 完成信号
- STALE resize_complete 的 `_resizePending` 清理

## P5：测试

### 单元测试（tests/unit/web/）

- `test_resize_scrollback.py`：`Session.resize()` 返回 scrollback 且内容正确（模型级）
- `test_uid_identity.py`：manager resolve_sid、uid 路由、同名复用不污染
- `test_history_uid.py`：归档/查询按 uid
- `test_adaptive_lock_uid.py`：锁按 uid

### e2e 测试（tests/e2e/）

- `test_web_resize_scrollback_e2e.py`：真实 WS 会话，resize 前后 scrollback 行数不变
- `test_web_uid_sessions_e2e.py`：同名会话复用后新旧隔离
- 鼠标模式 e2e：DECSET 启用 → 鼠标事件 → 编码验证

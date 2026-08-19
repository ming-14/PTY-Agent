# 线协议重构计划：信封 Envelope + 分组载荷 Grouped Payload

> 目标：把 daemon↔CLI 的 JSON 线协议从"扁平 dict + 原样 dump"升级为
> 「信封 + 类型化分组载荷 + 统一错误模型 + 表示层友好元信息」，
> 为 CLI 呈现层（Presenter）提供稳定契约，彻底告别"CLI 原样输出 daemon 响应"。

## 0. 实施状态

- **Phase 1（信封 + 分组载荷）— 已完成**：`protocol/envelope.py` + `Message` 线程局部响应包装 +
  客户端 `_send_recv` 两端接缝 + dispatcher 拆信封。请求分组 `op/condition/output/io`、响应分组 `data/state/meta`。
- **Phase 3（CLI 呈现层）— 已完成**：实现单独成文 [CLI-PRESENTATION-PLAN.md](CLI-PRESENTATION-PLAN.md)，
  `client/result.py`（类型化 Result）+ `client/presenter.py`（内容→stdout / 元信息→stderr / 错误+退出码），
  全线人类可读、零 JSON dump。
- **Phase 2（认证与错误模型）— 已完成**：认证凭证移入 ``auth`` 段（token/password/pubkey_fp），
  三组认证器/提供者 + Ed25519 签名器一致适配（签名内容含 auth.pubkey_fp 身份），受影响测试（test_password/test_pubkey/
  test_ed25519_signer/test_handler/basic e2e 裸消息）已迁移。统一错误码 surfacing：`client/result.py:_classify_error` → `ErrorResult.code`，
  服务端显式 `code` 优先。**注**：真实 TLS/token e2e 需具备真实 daemon 环境补充验证。
- **Phase 4（链路验证与收尾）— 进行中**：文档同步（本份 + ARCHITECTURE 附录 A，已完成）；全量单测 1652 passed
  （2 个 session 崩溃检测为环境既存失败）；三认证 e2e 待真实 daemon 验证。

## 1. 背景与目标

- CLI 现为 daemon 响应的"显示器"：`transport.cmd_*` 内部直接 `print_response` 原样 JSON dump。
- 线协议为扁平 dict：跨切面（类型/认证/签名）与业务字段混在同一层，无法支撑表示层区分"操作/返回条件/返回过滤"。
- 重构目标：
  1. 消息**自解释**：版本、方向、关联 id、时间戳一次到位。
  2. 载荷**按语义分组**：请求 `op / condition / output / io`，响应 `data / state / meta`。
  3. 签名、认证、业务解耦，**签名绑定不可变业务内容**，抗篡改抗重放。
  4. 统一**错误模型**（稳定 code + message + params），不再是 `type=error` 混类。
  5. 为表示层预留 `kind / meta` 渲染意图，CLI Presenter 不靠猜字段。

## 2. 现状调研结论

### 2.1 线格式
- 换行分隔 JSON：`json.dumps(obj, ensure_ascii=False) + "\n"`，UTF-8。
- `Message.encode/decode/send/recv`（[protocol/message.py](../src/protocol/message.py)）：逐行缓冲 `bytearray`，`MAX_MESSAGE_LENGTH` 兜底；`_recv_buffers` 连接级残留缓冲供二进制帧续读。
- 文件传输走独立二进制帧 `[4B len][1B type][payload]`（[protocol/transfer.py](../src/protocol/transfer.py)），不占 JSON 流。

### 2.2 消息结构（当前，扁平）
- 请求：`type`（路由）+ `id` + 业务字段（`command/input/trigger/timeout/encoding/newline/fresh/full/keep_ansi/...`）+ 认证注入字段 + 签名字段 + `client_defaults`。
- 响应：命令结果用 `commandType`（`sessionId/uid/outputStream/outputOffset/triggerReturnReason/program/hint/terminalState/sessionDefaults/screenBufferZ/Meta`）；通用用 `type`（`error/warning/info/config/pong/status`）。

### 2.3 签名
- `MessageSigner`（[protocol/signing.py](../src/protocol/signing.py)）对**整条消息 dict** 规范 JSON（sorted keys + 紧凑）签名。
- HMAC（`_sig`，token/basic）双向；Ed25519（`_sig_ed25519`，tls）单向，排除 `pubkey_fp`。
- 线程局部签名器（basic/token 与 tls 分线程）；`ping/pong/stop` 走 `skip_sign`。
- 接收端：启用签名器时，普通消息无签名字段直接丢弃。

### 2.4 路由与认证
- [daemon/handlers/dispatcher.py](../src/daemon/handlers/dispatcher.py)：`DaemonDispatcher.dispatch` 按 `msg["type"]` 路由；`ping` 特判；进程级插件按 `message_types` 接管（冲突内置优先）。
- 连接级首次消息由 `authenticator.authenticate(msg)` 校验，认证字段：`token`（token）、`password`（basic）、`pubkey_fp`（tls）。

### 2.5 消息类型清单
- 内置 handler：`exec / send / read / kill / mouse / events / closewin / status / list / stop / wait / plugin / workflow`；特判 `ping`（回 `pong`）。
- 进程级插件（files）：`file_read / file_write / file_edit / file_grep / file_glob / file_upload_start / file_download_start`。

### 2.6 响应模型
- [protocol/response.py](../src/protocol/response.py) 统一构造 dict；会话结果经 [daemon/handlers/utils.py](../src/daemon/handlers/utils.py) `build_result()`（含 debugInformation 剥离、`triggerReturnReason` 映射、hint）。
- 大字段：`screenBufferZ` = 稀疏网格 gzip+base64；`screenBufferMeta` 元信息。

### 2.7 痛点
1. CLI 原样 JSON dump，人眼不可读；元信息与内容混同一 stream，无法管道。
2. 类型/方向/认证/业务全在一层，职责不清。
3. 无协议版本、无请求关联 id、无时间戳，难以演进与审计。
4. 错误用 `type=error` 混在命令命名空间，客户端只能看 message 字符串。
5. 表示层要靠猜字段判断渲染方式，无 `kind` 契约。

## 3. 目标协议

### 3.1 信封 Envelope
```
proto   协议版本（整型，如 1）
dir     request | response | push
type    命令/事件
mid     消息 id（请求/响应预关联，requestId 语义）
ts      时间戳（防重放窗口判定）
kind    呈现意图（session|table|keyval|raw|error）
auth    认证 + 签名（独立段）
payload 业务内容（请求参数 / 结果）
meta    表示层元信息（hint/elapsed/terminalState...，可选）
error   统一错误（无则缺省）
```
保留项：换行 JSON、三监听器、HMAC/Ed25519 方向分离、文件二进制帧。

### 3.2 请求载荷分组
```
payload
 ├── op        操作本体（做什么）
 ├── condition 返回条件（何时返回）
 ├── output    返回数据过滤（返回多少/怎样）
 └── io        IO 偏好（编码/行尾，可选）
```
字段归属矩阵（四大终端命令）：

| 分组 | 归属字段 |
|------|---------|
| `op` | exec:`command/mode/cwd/env/cols/rows/force` ∥ send:`input` ∥ mouse:`action` ∥ 共用:`id` |
| `condition` | `trigger / newline / fresh / timeout / idleTimeout / idleAfterFirstOutput` |
| `output` | `full / keepAnsi / lines / grep / offset / column / snapshotDiff` |
| `io` | `encoding / sendEol` |

### 3.3 响应载荷分组
```
payload
 ├── data   返回内容（程序输出/快照/表格）
 ├── state  状态与原因（offset/reason/program）
 └── meta   渲染注解（elapsed/terminalState/hint/uid）
```
表格类统一 `data.columns + data.rows`，渲染器零特判。

### 3.4 统一错误模型
```
error: { code: 稳定机器码, message: 人话, params: 插值参数, retry: bool }
```
命令成功与否不靠 `type`，命令行与脚本据此分支。

### 3.5 签名设计
- 签名覆盖范围 = `payload` + `auth` 身份字段（credential），并纳入 `ts`（防重放窗口，如 ±5min）。
- `auth = { sig, credential: { token | password | pubkey_fp } }`，身份仍受签名保护（与现状一致）。
- `ping/pong/stop` 免签（`auth={}`）。

### 3.6 kind/meta 表示层意图
- `kind` 让 CLI Presenter 直接定位渲染通道（内容→stdout / 元信息→stderr）。
- `meta` 承载渲染注解，不污染载荷；CLI 不猜字段。

## 4. 范围边界

- **本期范围**：daemon↔CLI 的 TCP JSON 线协议 + 配套 CLI 呈现层。
- **不在本期**：Web 侧 WebSocket 协议（`Response.ws_*`，独立呈现，后续可借 `kind/meta` 概念收敛）；文件二进制帧协议（transfer.py）保持不变。
- 属**破坏性协议变更**：一次性切换，不做新旧双协议兼容壳（遵循"避免兼容/降级方案"原则）。

## 5. 兼容与迁移

- 采用 `proto` 版本号；老客户端未知新信封时按缺失字段默认拒绝并报 error。
- 各 phase 结束时跑全量 e2e 验证，确保 daemon 直连（绕过 CLI）语义不回退。
- docs 同步更新：ARCHITECTURE.md 附录 A、README 命令输出示例。

## 6. 分 phase 实施

### Phase 1 — 协议层：信封 + 分组载荷
- `protocol/message.py`：加入信封封装（proto/dir/type/mid/ts/kind），decode/encode 校验。
- 新增载荷分组：请求 `op/condition/output/io`，响应 `data/state/meta`。
- `daemon/handlers/*`：从 `payload.op.*` 读、产出 `payload.data/state/meta`。
- `daemon/handlers/dispatcher.py`：按 `payload.type` 路由（信封内）。
- `protocol/response.py`：构造分组响应 + 信封。
- 插件（files）：适配新信封读写。
- 更新 protocol/unit 测试。

### Phase 2 — 认证与错误模型
- 签名绑定 `payload + auth.credential + ts`；认证字段移入 `auth` 段。
- 统一错误 `error{code,message,params,retry}`；命名空间清理（命令 vs 错误解耦）。
- 认证器/签名器与 auth 测试更新。

### Phase 3 — CLI 表示层
- `client/result.py`：类型化结果模型，从分组 `payload` 构造。
- `client/presenter.py`：按 `kind/模型` 渲染，内容→stdout、元信息→stderr。
- `client/transport.cmd_*`：返回 Result 不再打印；`cli/commands/*` 调 presenter。
- `client/formatter.py`：脱钩插件钩子，收敛为底层序列化。
- 更新 client/cli 单测与 mocks。

### Phase 4 — 链路验证与收尾
- 全量 e2e：token/basic/tls 三认证 × 四终端命令 × 返回条件/过滤。
- workflow / plugin / file / events / status / list / kill 呈现回归。
- 文档同步；移除计划文档（按项目清洁度，完成后删除本计划文件）。

## 7. 测试策略

- 单元：`protocol/`（信封编解码、签名绑 payload、错误模型）、`auth/`（签名器/认证器）、`client/`（result 构造、presenter 渲染分流）、`cli/`（退出码/help/formatter）。
- 集成：loopback TCP 全链路（handler ↔ Message）。
- e2e：三认证模式 × 命令往返，断言信封字段非原始 dump、内容与元信息分流。

## 8. 风险与依赖

| 风险 | 说明 | 缓解 |
|------|------|------|
| 破坏面大 | 语义在 dispatcher/handlers/插件/客户端全链改动 | 分 phase、每 phase 跑全量测试 |
| 插件兼容 | files/ai 插件按扁平 dict 读写 | Phase 1 同步适配；插件响应形状随信封迁移 |
| 签名覆盖变化 | 签 `payload+credential+ts` 与旧签全消息不同 | Phase 2 单独成段，独立验证防重放 |
| e2e 依赖 | auth/tls 需密钥与证书 | 沿用现有 test fixtures + 三监听器参数化 |
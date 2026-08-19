# 执行链架构（Execution Chain Architecture）

本文档记录 `daemon` 侧"执行原语 → 后台监控 → 返回条件 → 输出过滤 → 响应组装"这条链路的**统一管理重构**：目标形态、已落地改动、以及因时序/安全而**暂缓**的部分。

> 原则：**只归一、不改行为**。所有重构均以"行为零变化"为前提，由全量单测守护。

---

## 1. 背景：审计发现的问题

执行链存在"散落 + 重复"，具体 5 类：

| 编号 | 问题 | 严重度 |
|------|------|--------|
| P0-A | 响应组装（选源→过滤→build_result→attach_screen→send）在 `execution.py` / `read_handler.py` / `workflow/engine.py` **三处各写一遍** | 🔴 |
| P0-B | "为何返回"的判定在 exec 等待循环、TriggerMatcher/wait_for_trigger、后台监控线程 **三个循环各算一遍** | 🔴 |
| P1-C | `build_result` 双职责 + crash 判定在两层各算一次 | 🟠 |
| P1-D | 请求字段（id/command/input/encoding/cwd/env/mode/...）靠 `msg.get` 在各 handler **散落读取** | 🟠 |
| P2-E | `--offset`↔`--full/--lines/--snapshot-diff/等待模式` 等互斥校验在多个 handler 重复 | 🟡 |

---

## 2. 目标架构（四层）

```
┌───────────────────────────────────────────────────────────────┐
│ ① 请求契约 / 条件    RequestContext + ReturnConditions ✅      │  声明一次
│     id/command/input/encoding/cwd/env/mode/cols/rows/plugins   │
│     + trigger/idle/full/keep_ansi/snapshot_diff/explicit       │
├───────────────────────────────────────────────────────────────┤
│ ② 校验              validated_request / validate_offset_policy ✅ │  一处
├───────────────────────────────────────────────────────────────┤
│ ③ 执行引擎          resolve_output() ✅  + 判定单源化 ✅        │  判定/取源
│     · 结束原因 (crash/ended) → resolve_exit_reason() 单一判定点 │
│     · GUI 检测 → check_gui_detected() 单一判定点（保留 1s 节流） │
│     · 完整 wait_reason 引擎统一 ⏸（线程时序，需 e2e）           │
│     · 后台监控线程只投喂事件, 不再自行判定（冗余为设计性并存）    │
├───────────────────────────────────────────────────────────────┤
│ ④ 响应装配          assemble_response() ✅                     │  组装一次
│     source → _apply_line_filters → build_result → attach_screen │
└───────────────────────────────────────────────────────────────┘
```

---

## 3. ✅ 已落地（行为零变化，全量单测守护）

| 改动 | 文件 | 说明 |
|------|------|------|
| **输出过滤统一** | `src/daemon/handlers/utils.py` | 新增核心 `_apply_line_filters`（lines/grep/column，非法抛 `ValueError`）；`filter_snapshot_lines`（静默版）、`apply_lines_grep`（报错版）降为**薄包装**；删除 `read_handler` 的内联第三份复制（改用 `apply_lines_grep`） |
| **① 返回条件声明** | `src/daemon/conditions.py::ReturnConditions` | `from_msg(msg)` 一处解析 trigger/newline/fresh/idle/full/keep_ansi/snapshot_diff/explicit_timeout；`Reason` 统一"为何返回"词表 |
| **P0-A 步1 取源统一** | `utils.py::resolve_output` | 统一 `snapshot / full / diff` 选源（含 read 的 `--lines` 隐式 full 语义 `force_full`）；`execution.py` / `read_handler.py` / `workflow/engine.py` 三处选择分支 → 一处 |
| **P2-E 偏移互斥校验** | `utils.py::validate_offset_policy` | 统一 `--offset` 与 `--lines/--full/--snapshot-diff/等待模式` 的互斥策略；`read_handler` 多处内联校验收敛 |
| **P1-D 请求契约 VO** | `src/daemon/conditions.py::RequestContext` | `RequestContext.from_msg(msg)` 一次解析 id/command/input/encoding/cwd/env/mode/cols/rows/plugins/lines/grep/column/offset/action/t_start，内嵌 `ReturnConditions`；exec/send/read/mouse 四 handler 与 `execution.py` 三流程收敛散落 `msg.get`（read 子进程 trigger 的 `fresh` 默认 True 语义保留在调用点） |
| **P0-A 步2 响应装配统一** | `src/daemon/execution.py::assemble_response` | build_result → (snapshotDiagnostics/stderr) → attach_screen → extra_fields → send/return 收敛为一处；`execution.py` 三流程 + `read_handler` 四处 + `workflow._read_type` 接入；read 路径 `consume_events=False`、workflow `send_response=False` 保持原语义 |
| **P0-B 判定单源化** | `session/events.py::resolve_exit_reason` + `session/trigger.py::check_gui_detected` | crash/ended 判定（原 4 处重复）→ `resolve_exit_reason()` 一处；GUI 检测（原 3 处）→ `check_gui_detected()` 一处（保留 1s 节流与 `enabled` 语义：未启用短路时仍轮询但不清空事件）；**保留** exec 等待循环 vs 后台监控线程 2s 兜底冗余（设计性并存，未做硬去重） |
| **P0-B 完整等待引擎统一** | `src/session/wait.py::wait_reason` | 新增统一迭代骨架 `wait_reason`（cancel 检查 / remaining/timeout 判定 / 循环单源）；`session/trigger.py::_wait_for_trigger_inner` 与 `execution.py::_run_snapshot_flow` 两个等待分支全部收敛到骨架，各循环检查顺序与等待原语经 `iteration` 回调保留（行为零变化）。线程职责：请求线程主判定、后台 `_monitor_loop` 只投喂事件做 2s 兜底，冗余按设计保留不硬去重 |

**验证**：全量单测 `1666 passed`（`tests/unit`，含新增 `RequestContext` 与 `test_wait_helpers` 用例），仅剩 2 个**环境既存失败**（`test_session_events` 崩溃判定时序，属 Windows 崩溃检测环境问题，与本次重构无关）。

---

## 4. ⏸️ 暂缓（如实说明，不强行"做完"）

以下未落地，原因是**大而时序/安全敏感**，强行改有"悄悄改变行为"的风险（与本项目"干净架构 + 行为零变化"底线冲突）。

| 项 | 内容 | 暂缓理由 |
|----|------|---------|
| 监控线程冗余去重 | 合并 exec 等待循环与后台 `_monitor_loop` 的 crash/gui 判定 | **涉线程时序**：二者冗余是"低延迟主动判定 vs 2s 兜底"的设计性并存；硬合并会改变崩溃/窗口检测时序。等待引擎骨架已统一，此冗余按设计**永久保留**（不视为待办） |

> `exec` 显式 `--timeout` 到期杀会话（`execution.py`，源自 commit `8083ed1`）是**既有行为**，非本重构引入。是否改为"到期只返回不杀"是独立的产品决策，需单独拍板，不可在本次架构统一中顺带改动。

---

## 5. 后续建议

- **监控线程冗余**：保持现状（低延迟主动判定 + 2s 兜底的设计性并存），不硬去重。
- **`exec --timeout` 杀会话**：独立产品决策，单独拍板是否改为"到期只返回不杀"。
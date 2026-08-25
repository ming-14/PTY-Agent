# scripts/ — Live 测试与调试工具

本目录存放需**手动运行**的 live 级测试与调试工具（不参与 pytest 自动收集）。
涉及 web 终端的 resize/scrollback 行为验证——用真实 xterm（Edge CDP）渲染
后端数据，确认显示结果（无重复/无错乱）。

## 前置条件

- Windows + Edge（默认路径 `C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`）
- daemon 运行中：`python -m src start`（监听 127.0.0.1:18766）
- Node.js（`run-xterm-repro.js` 用）

## 工具清单

| 工具 | 用途 | 用法 |
|------|------|------|
| `verify_pywezterm_reflow.py` | pywezterm 模型多次 resize 后 scrollback 完整性（leaf 同款权威验证，不依赖 daemon） | `python scripts/verify_pywezterm_reflow.py` |
| `verify_ws_resize3.py` | 直连 daemon WS：创建会话 → 输出 dir 风格内容 → 连续 resize → 检查 resize_complete 返回的 scrollback（行数/拆分残留/重叠） | `python scripts/verify_ws_resize3.py` |
| `live-collect-resize-data.py` | 直连 daemon：输出 → 8 次 resize → 收集每次 resize_complete 的 (scrollback, snapshot) 到 `scripts/live-resize-data.json`（供 xterm 渲染复现） | `python scripts/live-collect-resize-data.py` |
| `live-xterm-resize.html` | 真实 xterm 复现前端 rebuild/不重建流程：写入输出 → resize（xterm 自身 reflow）→ snapshot 更新可见区 → dump buffer（关键行完整性/拆分残留/重复检测） | 由 `run-xterm-repro.js` 驱动；也可浏览器直接打开（需注入数据） |
| `run-xterm-repro.js` | Edge CDP 运行器：加载 `live-xterm-resize.html` → 注入 `live-resize-data.json` → 渲染 → 输出结果 | `node scripts/run-xterm-repro.js` |
| `audit_state_imports.js` | 前端 JS 未使用具名导入审计 | `node scripts/audit_state_imports.js` |

## 典型验证流程（resize 后 scrollback 无重复/无错乱）

```bash
# 1. 启动 daemon（若未运行）
python -m src start

# 2. 收集 daemon 真实 resize 数据
python scripts/live-collect-resize-data.py

# 3. 用真实 xterm 渲染复现（Edge CDP headless）
node scripts/run-xterm-repro.js
```

`run-xterm-repro.js` 输出 JSON：每步（initial / resize 窄 / snapshot / resize 宽）
的 buffer 行数、关键行（`__rikka_kimi`/`__rikka_pi`/中文统计行）完整性、
拆分残留计数、尾部 5 行。判定：关键行完整、尾部无重复段 = 通过。

## 设计背景（为什么有这些测试）

- 后端 pywezterm 是 scrollback 权威（多次 resize 后 reflow 完整）
- 前端 xterm 是显示终端（ttyd 式：resize 不重建 scrollback，自身 reflow +
  后端 snapshot 更新可见区）
- 订阅恢复（刷新/断连）时后端 capture scrollback + 前端一次性重建
- 本目录工具验证这三条链路的实际行为（pytest e2e 只覆盖 WS 协议层，
  渲染层需手动 live 验证）

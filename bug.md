# ConPTY Ctrl+C 与鼠标修复记录

## 问题现象

### 1. 回车无效 / 整行消失 / 光标前移
- python REPL 内输入 `print(1)` + 回车：输入行消失，命令未执行，报 `SyntaxError`
- 根因：**DECSET 污染** — `condrv.py` 第 9 步向输入管道写入 `\x1b[?1002h\x1b[?1006h`，conhost 输入侧不解析模式设置序列，字节原样透传进子进程输入缓冲，python REPL 收到 `\x1b[?1002h\x1b[?1006hprint(1)` 报语法错误

### 2. Ctrl+C（\x03）无法中断进程
- 向 ConPTY 输入管道写入 `\x03` 不产生 CTRL_C_EVENT
- 直连脚本（probe_3.py）环境下有效，但 daemon 环境下无效（差异根因未定位）

### 3. 鼠标不能用
- web 端点击后 MOUSE_EVENT_RECORD 注入成功（WriteConsoleInputW），但 tcell/opencode 未收到
- 依赖 interceptor 的 SGR 鼠标处理路径（inject vs pipe 模式）

## 修复历史

### 方案 A（废弃）：vt_input + on_ready DECSET
- 尝试：后台 AttachConsole + SetConsoleMode(ENABLE_VIRTUAL_TERMINAL_INPUT)，成功后发 DECSET
- 失败：OpenConsole 下 DECSET 仍透传污染；VT_INPUT 破坏传统 ReadConsoleW 回车

### 方案 B（废弃）：全移除（vt_input + DECSET 全删）
- 尝试：回车正常（无 DECSET 污染），但鼠标不能用（DECSET 移除后 conhost ?1006 无法激活）

### 方案 C（当前）：系统 conhost + vt_input + on_ready DECSET
- 系统 conhost 下 DECSET 在 VT_INPUT 开启后被 conhost 解析消费（不污染）
- python 回车正常（`123+456` → `579` ✓）
- 鼠标：探针（字节流读取者）仍收不到 SGR；tcell（ReadConsoleInput 类型）待测
- Ctrl+C：仍无效（\x03 在 daemon 环境无法触发 CTRL_C_EVENT）

## 关键发现

### 机制
1. **DECSET 污染**：`\x1b[?1002h\x1b[?1006h` 写入输入管道 → conhost 对模式设置序列不解析 → 原样透传进子进程输入缓冲
2. **OpenConsole vs 系统 conhost 差异**：
   - OpenConsole（WT 1.24）：VT_INPUT 开启后 DECSET 仍透传
   - 系统 conhost（19045）：VT_INPUT 开启后 DECSET 被解析消费（不透传）
3. **VT_INPUT 副作用**：
   - 开启后 \x03 被解析为 Ctrl+C 键事件（产生 CTRL_C_EVENT，handler 收到）
   - 但 ReadConsoleW（python REPL 的回车）在 OpenConsole 下被破坏；系统 conhost 下正常
4. **conhost 鼠标 SGR 翻译**（interceptor.py:154-159 注释）：
   - VT_INPUT=ON 时 conhost 把 MOUSE_EVENT_RECORD 翻译为 SGR-1006 送 stdin
   - 前提：conhost 自身启用 ?1006（需往输入端发 DECSET）
   - 实际测试（mop4/mop5）：探针字节流未收到 SGR，翻译机制可能不完整

### 残留问题
- Ctrl+C（\x03）在 daemon 环境下始终无效（直连脚本有效，差异未定位）
- 鼠标注入后 tcell 是否收到仍需 web 端实测
- 当前配置：系统 conhost（OpenConsole 已禁用）+ vt_input + on_ready DECSET

## 复现步骤
```bash
# 回车验证
python app.py exec test -c "python" -t ">>>"
python app.py send test -i "123+456" -e cr -t "^579"

# Ctrl+C 验证
python app.py send test -i "import time; time.sleep(30)" -e cr
python app.py send test -j -i "{ctrl+c}" -e none -t "KeyboardInterrupt"
```

## 引用文件
- `src/pty/windows/condrv.py` — 第 9 步：后台 vt_input + on_ready DECSET
- `src/pty/windows/vt_input.py` — VT_INPUT 初始化（AttachConsole + SetConsoleMode）
- `src/pty/windows/conpty.py` — 新增 vt_input 后台调用
- `src/pty/base.py` — 新增 poll_gui_windows 默认实现
- `src/input/interceptor.py` — SGR 鼠标处理（inject/pipe 模式），注释记载机制
- `src/pty/windows/conpty_handle.py` — dwFlags=0x4（PSEUDOCONSOLE_WIN32_INPUT_MODE，之前会话遗留）
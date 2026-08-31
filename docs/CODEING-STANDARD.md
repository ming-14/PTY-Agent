# Python 编码规范

> 本文档定义本项目的 Python 编码规范。所有 `src/` 目录下的代码须遵循此规范。

---

## 1. 语言与命名

### 1.1 命名风格

| 类别 | 风格 | 示例 |
|------|------|------|
| 包名 | 全小写，无下划线 | `pty/`, `session/`, `protocol/` |
| 模块名 | 全小写，蛇形 | `message.py`, `encoding.py`, `ansi.py` |
| 类名 | PascalCase | `class PseudoTerminal:`, `class WindowsPseudoTerminal:` |
| 函数/方法 | snake_case | `def create_pty():`, `def strip_ansi():` |
| 变量 | snake_case | `output_buffer`, `trigger_pattern`, `session_id` |
| 常量 | SCREAMING_SNAKE_CASE | `MAX_OUTPUT_BUFFER`, `TOKEN_PORT`, `IS_WINDOWS` |
| 私有 (模块内) | 前导下划线 | `_ANSI_RE`, `_ensure_condrv()`, `_CONDRV_OK` |
| 伪私有 (类内) | 前导下划线 | `self._lock`, `self._reader_loop()` |
| 类型变量 | PascalCase | `T = TypeVar('T')` |
| 异常 | PascalCase + Error 后缀 | `PtyError`, `SessionNotFoundError` |

### 1.2 命名原则

- **名称应自文档化**：避免缩写（除广泛接受的：`buf` → buffer, `enc` → encoding, `tmp` → temporary）
- **布尔变量/函数用疑问式**：`is_running`, `has_output`, `should_stop`
- **集合用复数名词**：`sessions`, `connections`, `handlers`
- **不内置类型名做变量名**：不要用 `str`, `dict`, `list`, `type` 做变量名

```python
# 正确
def decode_strip_tail(data: bytes, encoding: str) -> str:
    ...

# 错误
def decode(s, e):
    ...
```

---

## 2. 代码风格

### 2.1 缩进与格式

- **缩进**：4 个空格，不使用 Tab
- **行宽**：最多 **100 字符**
- **空行**：
  - 顶层函数/类定义之间：2 个空行
  - 类内方法定义之间：1 个空行
  - 逻辑块之间：1 个空行

### 2.2 导入规范

```python
"""模块文档字符串"""

import os          # 标准库，按字母序
import re
import sys
import time
from typing import Optional

import third_party   # 第三方库（按需使用，如 cryptography/fastapi 等）

from .daemon import (  # 本地包，使用相对导入
    CONNECT_MODE,
    TOKEN_PORT,
    is_running,
    start_daemon,
    stop_daemon,
    Message,
)
```

**规则**：
1. 标准库 → 第三方库 → 本地包
2. 每组之间一个空行
3. 每组内部按字母序排列
4. 优先使用 `from ... import ...` 而非 `import ...`（减少命名空间污染）
5. 大量导入时使用括号换行对齐

### 2.3 类型注解

对所有**公开的**函数和方法的参数、返回值使用类型注解。

```python
def create_session(
    session_id: str,
    command: str | list[str],
    encoding: str | None = None,
) -> Session:
    """创建新会话"""
    ...
```

**规则**：
- 参数必须有类型注解
- 返回值必须有类型注解（`-> None` 如果无返回值）
- 内部辅助函数（以下划线开头）酌情使用类型注解
- 对于 `Optional` 类型，Python 3.10+ 使用 `X | None`，之前版本使用 `Optional[X]`

### 2.4 文档注释（Doxygen 风格）

所有公开的类、函数、方法必须包含 Doxygen 风格的文档字符串。

```python
def get_output(
    from_offset: int | None = None,
    encoding: str | None = None,
) -> str:
    """获取会话输出

    从指定偏移位置读取输出缓冲区中的内容，并按指定编码解码。

    Args:
        from_offset: 从指定字节偏移开始读取。None 表示从头读取。
        encoding:    指定解码编码。None 表示自动探测。

    Returns:
        解码后的输出文本字符串。缓冲区为空时返回空字符串。

    Raises:
        RuntimeError: 如果会话已关闭且无法访问缓冲区。

    Note:
        本方法在持锁状态下读取缓冲区，确保线程安全。
        编码探测逻辑由 encoding.detect_decode() 完成。
    """
```

**字段规范**：
- `Args:` — 每个参数一行，后跟简短说明
- `Returns:` — 返回值说明
- `Raises:` — 可能抛出的异常（仅限文档化的异常）
- `Note:` / `Warning:` — 补充说明和注意事项
- `Example:` — 使用示例（可选）

---

## 3. 模块化设计

### 3.1 单一职责原则

每个模块只做一件事。判断标准：能否用一句话描述该模块的职责？

```python
# 正确：protocol/message.py — 消息编解码
class Message:
    """JSON 换行分隔消息协议"""
    ...

# 正确：protocol/ansi.py — ANSI 颜色/样式过滤
def strip_ansi(text: str) -> str:
    """去除字符串中的 ANSI 颜色/样式码，保留清屏/光标等控制序列"""
    ...
```

### 3.2 文件尺寸控制

建议单文件不超过600行

### 3.3 依赖方向

```
config.py (纯数据，不被任何包依赖)
    ↓
protocol/ (零依赖，仅依赖标准库)
    ↓
pty/ (依赖 config + 标准库)
    ↓
session/ (依赖 pty + protocol + config)
    ↓
daemon/ (依赖 session + protocol + config)
    ↓
client/ (依赖 protocol + config)
    ↓
cli/ (依赖 client + config)
    ↓
__main__.py (依赖 cli)
```

**禁止**：
- 上层模块导入下层模块
- 形成循环依赖
- 模块在自己的 `__init__.py` 中做复杂初始化

---

## 4. 错误处理

### 4.1 异常类型

```python
# 积极使用内置异常
raise ValueError("会话 ID 必须为非空字符串")
raise KeyError(f"会话 '{session_id}' 已存在")
raise RuntimeError(f"创建伪终端失败: {e}") from e
raise TypeError(f"输入数据必须是 str 或 bytes, 收到 {type(data).__name__}")
```

**规则**：
- 优先使用 Python 内置异常类
- 捕获时指定具体的异常类型，禁止裸 `except:`
- 异常链使用 `raise ... from e`
- 同一模块内可定义自定义异常（继承自 `Exception`）

### 4.2 异常边界

```python
# 防火墙模式：handler 捕获所有异常并记录，防止守护进程崩溃
def handle(self, conn, addr):
    try:
        ...
    except json.JSONDecodeError:
        logger.error("JSON 解析失败")
        Message.send(conn, {"type": "error", "error": "请求格式错误"})
    except (BrokenPipeError, ConnectionError, OSError) as e:
        logger.warning(f"客户端连接异常: {e}")
    except Exception as e:
        logger.error(f"请求处理异常: {e}\n{traceback.format_exc()}")
        Message.send(conn, {"type": "error", "error": f"服务器内部错误: {e}"})
```

| 层 | 异常处理策略 |
|----|------------|
| `pty/` | 抛出 `OSError` / `RuntimeError`，不上层吞没 |
| `session/` | 包装为 `RuntimeError` 或记录日志后继续 |
| `daemon/handler.py` | 异常防火墙，全部捕获并记录，返回错误响应 |
| `client/transport.py` | 捕获 `ConnectionError`，其他抛到 `__main__` |
| `cli/main.py` | 捕获 `KeyboardInterrupt` + `Exception` 兜底 |

### 4.3 防御性检查

```python
def write_input(self, data):
    """写入输入到 PTY"""
    if not self._pty or not self.running:
        raise RuntimeError(f"会话 '{self.id}' 未运行")
    if not isinstance(data, (str, bytes)):
        raise TypeError(f"输入数据必须是 str 或 bytes, 收到 {type(data).__name__}")
    try:
        self._pty.write(data)
    except Exception as e:
        _logger.error("写入输入失败 (会话 '%s'): %s", self.id, e)
        raise RuntimeError(f"写入输入失败: {e}") from e
```

---

## 5. 并发与线程安全

### 5.1 锁策略

```python
class OutputBuffer:
    def __init__(self, ...):
        self._lock = threading.RLock()  # RLock 支持可重入（reader_loop 持锁时调用 append）

    def get_slice(self, start=0, end=None):
        with self._lock:
            data = bytes(self._buffer[start:end])
            return data
```

**规则**：
- 所有共享可变状态的访问必须持锁
- 持锁时间尽量短，不阻塞 I/O 操作
- 标记哪些方法在持锁状态下调用（如 `_check_trigger_locked()`）
- 避免嵌套锁：一个线程最多持有一把锁

### 5.2 线程管理

```python
# 守护线程 + 命名
self._reader_thread = threading.Thread(
    target=self._reader_loop,
    daemon=True,
    name=f"pty-reader-{self.id}",
)

# 正确停止：信号模式而非强制终止
self._stop_event.set()
self._trigger_event.set()
if self._reader_thread and self._reader_thread.is_alive():
    self._reader_thread.join(timeout=3)
```

---

## 6. 日志

### 6.1 日志器获取

每个模块通过 `get_logger` 工厂获取日志器（统一入口，校验注册表）：

```python
from ..logging import get_logger

_logger = get_logger("pty-session")
_logger = get_logger("pty-daemon")
_logger = get_logger("pty-client")
```

### 6.2 日志级别

| 级别 | 场景 |
|------|------|
| `ERROR` | 影响功能的异常（PTY 创建失败、写入失败） |
| `WARNING` | 非致命但不期望的情况（连接断开、回退路径） |
| `INFO` | 关键生命周期事件（守护进程启动/停止、会话创建/销毁） |
| `DEBUG` | 任何对调试有帮助的信息（消息内容、线程状态） |

```python
_logger.error("连接异常 (会话 '%s'): %s", session_id, e)  # ERROR
_logger.warning("会话已结束 (会话 '%s')", session_id)      # WARNING
_logger.info("创建会话 '%s': %s", session_id, command)     # INFO
```

### 6.3 上下文绑定

在会话/连接/请求入口绑定上下文字段，后续所有日志自动携带：

```python
from ..logging import bind, unbind

token = bind(session_id=session_id, connection_id=conn_id)
try:
    _logger.info("处理请求")  # 自动带 [session_id=xxx connection_id=yyy]
finally:
    unbind(token)
```

### 6.4 异步架构

日志系统基于异步队列（`QueueHandler` + 后台单线程），业务线程零阻塞：
- 业务线程仅 `queue.put_nowait`（O(1)），格式化/IO/归档全在后台 `pty-log-writer` 线程
- 队列满时 `drop_oldest` 丢弃最旧记录，防止背压阻塞业务
- `shutdown()` 刷空队列 + 最后一次归档，确保所有日志落盘

---

## 7. 代码组织模板

### 7.1 模块模板

```python
"""模块一句话描述

详细说明（可选）：模块的职责、关键设计决策、使用注意事项。
"""
# ── 导入 ──
import os
import sys
from typing import Optional

# ── 日志 ──
from ..logging import get_logger
_logger = get_logger("pty-xxx")

# ── 常量 ──
MAX_BUFFER_SIZE = 1024 * 1024

# ── 公开类 ──
class SomeClass:
    """类一句话描述

    详细说明（可选）。
    """
    def __init__(self, ...):
        ...

# ── 公开函数 ──
def some_function(...) -> ...:
    """函数说明"""
    ...

# ── 内部辅助 ──
def _internal_helper(...) -> ...:
    """内部辅助函数说明"""
    ...
```

### 7.2 类模板

```python
class SomeManager:
    """XXX 管理器

    管理 XXX 的生命周期。

    Attributes:
        id: 唯一标识符。
        running: 是否正在运行。
    """

    def __init__(self, some_id: str):
        """初始化

        Args:
            some_id: 唯一标识符。
        """
        self.id = some_id
        self.running = False
        self._buffer = bytearray()
        self._lock = threading.Lock()
```

---

## 8. Windows 特有代码

### 8.1 平台隔离

Windows 特有代码按业务域落位，不设集中式 `windows/` 子包：

- 进程管理（Job Object / 进程查询 / GUI 监控）：`src/process/windows/`，仅在 Windows 被导入
- PTY/控制台绑定：`src/pty/wezterm_pty.py`（wezterm-py 后端，跨平台统一；Windows 用 OpenConsole 宿主，Unix 用 openpty）
- 平台分支：`src/pty/pty_factory.py` 工厂按 `IS_WINDOWS` 选择后端（Windows 优先 wezterm-py，沙箱启用时走沙箱后端；Unix 统一 wezterm-py）

```python
# src/pty/pty_factory.py — 平台分支（示意）
from ..config.common import IS_WINDOWS
from .wezterm_pty import WeztermPseudoTerminal  # 跨平台统一后端

if IS_WINDOWS:
    # Windows：优先 WeztermPseudoTerminal；失败或沙箱启用时走沙箱后端
    ...
else:
    # Unix：统一 WeztermPseudoTerminal（openpty）
    ...
```

**Unix 平台** 永远不会导入 `src/process/windows/` 中的任何代码。

### 8.2 ctypes API 声明

```python
# src/process/windows/api.py — Windows 进程管理 API 绑定

import ctypes
from ctypes import wintypes as W

K = ctypes.WinDLL("kernel32", use_last_error=True)
U = ctypes.WinDLL("user32", use_last_error=True)

def _api(name, restype, argtypes):
    """绑定 kernel32 API 函数"""
    fn = K[name]
    fn.restype = restype
    fn.argtypes = argtypes
    return fn
```

**规则**：
- 进程管理相关 API 绑定集中在 `src/process/windows/api.py`（`_api`/`_uapi` 辅助绑定）
- PTY/控制台绑定（ConPTY、管道）由 `src/pty/wezterm_pty.py` 承担，仅声明少量通用绑定（如 CloseHandle），两处显式独立、避免跨层依赖
- 所有 API 函数名加 `_` 前缀标记为私有
- 使用 `_api()` / `_uapi()` 辅助函数简化绑定
- 不要将 API 声明分散到其他文件

---

## 9. 配置管理

所有配置常量集中在 `config/` 包（TOML 文件 + 加载器）：

- `config/` 包不导入任何其他项目业务模块
- 常量名全大写 `SCREAMING_SNAKE_CASE`
- 所有模块从 `config/` 包导入所需常量，不要重复定义
- 配置文件：`common.toml` / `shared.toml` / `transfer.toml`（根）、`daemon/daemon.toml` / `daemon/logging.toml` / `daemon/web.toml`（可选，缺失即 web 禁用） / `daemon/sandbox.toml`（可选，缺失即沙箱关闭）、`client/client.toml`（项目根 `config/`，加载器在 `src/config/`）；`daemon/vnc.toml` / `daemon/vnc.example.toml` 为 winvnc.exe 外部配置，Python 不加载。插件业务参数由插件自包含配置（`plugin.json` config.defaults + 内存覆盖，可选的 `config.schema.json` 校验）提供；`registry.json` 可选，缺失即插件系统禁用
- 加载机制：`_loader.py` 提供 `load_toml()` / `flatten()` / `merge()` 工具函数

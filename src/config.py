"""集中管理所有配置常量"""

import os
import sys

# ── 日志 ──
# 日志级别: "DEBUG" / "INFO" / "WARNING" / "ERROR" / "CRITICAL"
# 设为 None 则不写日志
DAEMON_LOG_LEVEL = "DEBUG"
CLIENT_LOG_LEVEL = "DEBUG"

# 日志体积上限（防无限增长）。两个文件的写入者生命周期不同，策略也不同：
#   daemon.log —— 单一长驻进程写 → RotatingFileHandler 轮转
#   client.log —— 多个短命客户端并发写 → 只在进程启动时按大小截断
#                 （多写者下轮转的 rename 会互踩）
LOG_MAX_BYTES    = 5 * 1024 * 1024   # 单个日志文件上限（字节）
LOG_BACKUP_COUNT = 3                 # daemon.log 保留的历史份数

# 受管 logger 名：客户端与守护进程共用同一份（两处配置各取所需 handler）。
# 新模块若引入新的 logger 名，必须在此登记 —— 否则该 logger 会落到 root /
# lastResort（守护进程里等于丢失，客户端里等于污染 stderr）。
# test/unit/test_lifecycle.py::TestLoggerCoverage 扫描 src 断言这一条。
MANAGED_LOGGERS = (
    "pty-client", "pty-daemon", "pty-session", "pty-protocol",
    "backend-subprocess", "backend-tty", "backend-factory", "backend-base",
    "backend-errors", "backend-windows", "backend-windows-error",
    "backend-job", "backend-gui-monitor", "backend-unix", "backend-unix-tracker",
)

# ── 文件路径 ──
DATA_DIR = os.path.join(os.path.expanduser("~"), ".pty-agent")  # Unix 共享内存文件目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

# ── 输出缓冲 ──
MAX_HISTORY_LINES   = 10000           # 滚动历史最大行数（pty 模式 scrollback 上限）
MAX_OUTPUT_CHARS    = 20 * 1024 * 1024  # 全量输出最大字符数（行缓冲字符预算）
MAX_TRIGGER_SCAN    = 10 * 1024 * 1024  # 10MB，触发检查最大扫描范围（字符）

# ── 超时 ──
DEFAULT_TRIGGER_TIMEOUT = 120.0          # 触发等待超时（秒）
DAEMON_START_TIMEOUT    = 3.0            # 守护进程启动等待（秒）
DAEMON_START_POLL_INTERVAL = 0.2         # 等待守护进程就绪的轮询间隔（秒）
STOP_TIMEOUT            = 3.0            # 停止守护进程超时（秒）
EXIT_CODE_POLL_INTERVAL = 0.05           # 退出码获取重试间隔（秒）

# ── 共享内存轮询 ──
DAEMON_POLL_INTERVAL    = 0.1            # 守护进程信箱轮询间隔（秒）
CLIENT_POLL_INTERVAL    = 0.01           # 客户端等待 DONE 标记的轮询间隔（秒）
DAEMON_HEARTBEAT_INTERVAL = 1.0          # 守护进程心跳更新间隔（秒）
DAEMON_HEARTBEAT_FRESH  = 10.0           # 心跳新鲜阈值（秒，超过视为僵死）

# ── 其他 ──
READ_SIZE              = 65536           # 后端单次读取字节数

# ── 输入长度限制（防资源耗尽）──
MAX_SESSION_ID_LEN     = 128      # 会话标识符最大长度
MAX_COMMAND_LEN        = 65536    # 命令字符串最大长度（64 KB）
MAX_PATTERN_LEN        = 4096     # 触发/过滤正则最大长度（4 KB）
MAX_INPUT_LEN          = 65536    # send 输入文本最大长度

# ── 终端尺寸（pty 模式 pyte 屏幕）──
DEFAULT_COLS           = 80       # 终端默认宽度（列）
DEFAULT_ROWS           = 24       # 终端默认高度（行）

# ── 共享内存 — 守护进程信息区（单实例 + 心跳）─
# 格式: "PID:状态:心跳时间戳"（如 "5488:1:1234567890.123"）
# 状态: 0=停止, 1=运行
MMAP_DAEMON_INFO_NAME = "Local\\PTYAgentDaemon"
MMAP_DAEMON_INFO_SIZE = 64

# ── 共享内存 — 请求信箱（客户端 → 守护进程）─
# 固定槽位数组，每槽 256 字节，共 32 槽
# 每槽: state(1) + client_pid(8) + req_name(64) + resp_name(64) + token(64) + seq(8) + padding
MMAP_MAILBOX_NAME = "Local\\PTYAgentMailbox"
MAILBOX_SLOT_COUNT = 32
MAILBOX_SLOT_SIZE = 256           # 每槽 256 字节
MAILBOX_SIZE = MAILBOX_SLOT_COUNT * MAILBOX_SLOT_SIZE  # 8192 字节

# ── 共享内存 — 请求/响应通道 ──
REQ_SHM_SIZE = 256 * 1024         # 256 KB（请求体上限由各字段长度限制约束）
RESP_SHM_SIZE = 64 * 1024 * 1024  # 64 MB，响应 JSON（超出截断+truncated 标志）

# ── 认证令牌（同用户会话隔离，防跨用户越权）─
# 令牌通过共享内存在守护进程与客户端之间传递，每次请求携带。
# 令牌每 30 分钟轮换限制泄露窗口。
AUTH_TOKEN_NAME = "Local\\PTYAgentAuth"
AUTH_TOKEN_SIZE = 64  # hex-encoded 32-byte token
AUTH_TOKEN_ROTATE_INTERVAL = 1800  # 令牌轮换周期（秒），默认 30 分钟
AUTH_TOKEN_GRACE_PERIOD    = 120   # 旧令牌宽限期（秒），轮换后 2 分钟内仍有效

# ── 平台 ──
IS_WINDOWS = sys.platform == "win32"

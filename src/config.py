"""集中管理所有配置常量"""

import os
import sys

# ── 日志 ──
# 日志级别: "DEBUG" / "INFO" / "WARNING" / "ERROR" / "CRITICAL"
# 设为 None 则不写日志
DAEMON_LOG_LEVEL = "DEBUG"
CLIENT_LOG_LEVEL = "DEBUG"
CLIENT_DEBUG = True

# ── 文件路径 ──
DATA_DIR = os.path.join(os.path.expanduser("~"), ".pty-agent")  # Unix 共享内存文件目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

# ── 缓冲区 ──
MAX_OUTPUT_BUFFER = 100 * 1024 * 1024    # 100 MB，会话输出缓冲区上限
MAX_TRIGGER_SCAN  = 10 * 1024 * 1024     # 10 MB，触发检查最大扫描范围

# ── 超时 ──
DEFAULT_TRIGGER_TIMEOUT = 120.0          # 触发等待超时（秒）
DAEMON_START_TIMEOUT    = 3.0            # 守护进程启动等待（秒）
STOP_TIMEOUT            = 3.0            # 停止守护进程超时（秒）

# ── 共享内存轮询 ──
DAEMON_POLL_INTERVAL    = 0.1            # 守护进程信箱轮询间隔（秒）
CLIENT_POLL_INTERVAL    = 0.02           # 客户端响应轮询间隔（秒）
DAEMON_HEARTBEAT_INTERVAL = 1.0          # 守护进程心跳更新间隔（秒）
DAEMON_HEARTBEAT_FRESH  = 10.0           # 心跳新鲜阈值（秒，超过视为僵死）

# ── 其他 ──
PTY_READ_SIZE          = 65536

# ── 输入长度限制（防资源耗尽）──
MAX_SESSION_ID_LEN     = 128      # 会话标识符最大长度
MAX_COMMAND_LEN        = 65536    # 命令字符串最大长度（64 KB）
MAX_PATTERN_LEN        = 4096     # 触发/过滤正则最大长度（4 KB）
MAX_INPUT_LEN          = 65536    # send 输入文本最大长度

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
MAILBOX_SIZE = 32 * 256           # 8192 字节

# ── 共享内存 — 请求/响应通道 ──
REQ_SHM_SIZE = 256 * 1024         # 256 KB，请求 JSON 最大 64KB
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

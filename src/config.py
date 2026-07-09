"""集中管理所有配置常量"""

import os
import sys

# ── 网络 ──
DAEMON_HOST = "127.0.0.1"
DEFAULT_DAEMON_PORT = 18765              # 默认端口（实际端口由动态分配写入 port 文件）

# ── 日志 ──
# 日志级别: "DEBUG" / "INFO" / "WARNING" / "ERROR" / "CRITICAL"
# 设为 None 则不写日志
DAEMON_LOG_LEVEL = "DEBUG"
CLIENT_LOG_LEVEL = "DEBUG"
CLIENT_DEBUG = True

# ── 文件路径 ──
DATA_DIR = os.path.join(os.path.expanduser("~"), ".pty-agent")  # Unix 回退用
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
PORT_FILE = os.path.join(DATA_DIR, "daemon.port")  # Unix 回退用

# ── 缓冲区 ──
MAX_OUTPUT_BUFFER = 100 * 1024 * 1024    # 100 MB，会话输出缓冲区上限
MAX_TRIGGER_SCAN  = 10 * 1024 * 1024     # 10 MB，触发检查最大扫描范围

# ── 超时 ──
DEFAULT_TRIGGER_TIMEOUT = 120.0          # 触发等待超时（秒）
DAEMON_START_TIMEOUT    = 3.0            # 守护进程启动等待（秒）
PING_TIMEOUT            = 1.0            # ping 探测超时（秒）
CONNECT_TIMEOUT         = 30.0           # 客户端连接超时（秒）
STOP_TIMEOUT            = 3.0            # 停止守护进程超时（秒）

# ── 其他 ──
SOCKET_LISTEN_BACKLOG  = 5
SOCKET_RECV_BUFSIZE    = 4096
PTY_READ_SIZE          = 65536

# ── 输入长度限制（防资源耗尽）──
MAX_SESSION_ID_LEN     = 128      # 会话标识符最大长度
MAX_COMMAND_LEN        = 65536    # 命令字符串最大长度（64 KB）
MAX_PATTERN_LEN        = 4096     # 触发/过滤正则最大长度（4 KB）
MAX_INPUT_LEN          = 65536    # send 输入文本最大长度

# ── 共享内存（Windows 命名 mmap 用于守护进程端口传递）─
# 安全说明：`Local\` 前缀限定同 Windows 会话/同 Unix 用户访问，
# 跨用户隔离由内核保证。同用户下其他进程可读写共享内存，但：
# 1) 这些进程同样可以连接 127.0.0.1 TCP 端口
# 2) 令牌每 30 分钟轮换
# 3) 攻击者需同时绕过共享内存 + TCP 两层防护
MMAP_NAME = "Local\\PTYAgentDaemon"
MMAP_SIZE = 32

# ── 认证令牌（同用户会话隔离，防跨用户越权）─
# 令牌通过共享内存在守护进程与客户端之间传递，随后在 TCP 连接中明文发送。
# 这是接受的设计决策：127.0.0.1 TCP 嗅探在 Windows 上需要管理员权限，
# 在 Linux 上需要 root 权限。令牌每 30 分钟轮换限制泄露窗口。
AUTH_TOKEN_NAME = "Local\\PTYAgentAuth"
AUTH_TOKEN_SIZE = 64  # hex-encoded 32-byte token
AUTH_TOKEN_ROTATE_INTERVAL = 1800  # 令牌轮换周期（秒），默认 30 分钟
AUTH_TOKEN_GRACE_PERIOD    = 120   # 旧令牌宽限期（秒），轮换后 2 分钟内仍有效

# ── 平台 ──
IS_WINDOWS = sys.platform == "win32"

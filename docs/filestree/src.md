# src/ 主包

> 主 Python 包。前端静态资源（`src/web/static/`）单独列在 [web-static.md](web-static.md)。

```
src/
├── __main__.py              # CLI 入口（argparse 参数解析 + 命令派发）

├── config/                  # ═══════ 配置中心（TOML 加载器，数据文件在 <项目根>/config/） ═══════
│   ├── __init__.py          # 包导出 + 配置域归档说明
│   ├── _loader.py           # TOML 加载/展平/合并工具（load_toml(filename, domain) / flatten / merge）
│   ├── common.py            # 共有配置加载（common.toml + IS_WINDOWS / DATA_DIR / PROJECT_ROOT）
│   ├── shared.py            # 跨侧共享配置加载（common + shared.toml + PORT_FILE / LOG_DIR）
│   ├── daemon.py            # 守护进程配置加载（common + shared + daemon/ + logging/ + web/）
│   ├── client.py            # 客户端配置加载（common + shared + client/ + PORT_FILE / LOG_DIR）
│   ├── transfer.py          # 传输协议配置加载（transfer.toml）
│   └── sandbox.py           # 沙箱配置加载（daemon/sandbox.toml）
│
│   # TOML 数据文件（config/ 根：common/shared/transfer；
│   # config/daemon/：daemon/logging/web/sandbox/vnc/vnc.example；
│   # config/client/：client；
│   # config/plugins/plugins.json 为 daemon 侧插件注册；
│   # vnc*.toml 为 winvnc.exe 外部配置，Python 不加载），
│   # 清单见 <项目根>/config/README.md

├── protocol/                # ═══════ 通信协议层 ═══════
│   ├── __init__.py
│   ├── message.py           # Message 类（JSON 换行分隔协议：编码/解码/收发 + ping 探测）
│   ├── signing.py           # MessageSigner 签名抽象（协议域，auth 包实现）
│   ├── ansi.py              # ANSI 转义序列过滤（strip_ansi）
│   └── response.py          # Response 类（统一响应构建器，CLI/TCP/WS 共用）

├── auth/                    # ═══════ 认证层（可插拔认证与消息签名） ═══════
│   ├── __init__.py          # 导出共享基础设施
│   ├── base.py              # 抽象接口（Authenticator / CredentialProvider）
│   ├── keys.py              # Ed25519 密钥实体（PublicKey / PrivateKey / 生成/加载/指纹）
│   ├── context.py           # AuthContext（连接级认证上下文，绑定出站签名器/入站验证器/认证器）
│   ├── token/               # ═══ Token + HMAC 认证（同机，对称双向） ═══
│   │   ├── __init__.py
│   │   ├── authenticator.py # TokenAuthenticator + TokenCredentialProvider
│   │   └── signer.py        # HmacMessageSigner（HMAC-SHA256）
│   ├── pubkey/              # ═══ Ed25519 公钥认证（跨机，非对称单向） ═══
│   │   ├── __init__.py
│   │   ├── authenticator.py # PubkeyAuthenticator + PubkeyCredentialProvider
│   │   └── signer.py        # Ed25519MessageSigner
│   └── tls/                 # ═══ TLS 基础设施 ═══
│       ├── __init__.py
│       ├── cert_manager.py  # CertificateManager（自签证书生成/加载/指纹计算）
│       └── known_hosts.py   # KnownHosts（TOFU 信任存储，类似 SSH known_hosts）

├── common/                  # ═══════ 跨侧共享工具层（Client 与 Daemon 均依赖，纯 OS 级工具） ═══════
│   ├── __init__.py
│   ├── process.py           # pid_exists（进程存在性探测，跨侧共享）
│   └── shells.py            # Shell 探测（detect_available_shells / format_shell_info，跨侧共享）

├── daemonctl/               # ═══════ daemon 控制包（client 侧守护进程生命周期控制与 TLS 连接） ═══════
│   ├── __init__.py          # 导出 start/stop/is_running/端口发现/TLSClient
│   ├── lifecycle.py         # 守护进程启动/停止/探测/强制清理（Popen python -m src.daemon）
│   └── tls.py               # TLSClient（TLS 连接 + TOFU 证书验证，CONNECT_MODE=tls 跨机模式）

├── client/                  # ═══════ 前端客户端层 ═══════
│   ├── __init__.py
│   ├── lifecycle.py         # 客户端日志配置（setup_client_logging；daemon 控制见 daemonctl 包）
│   ├── transport.py         # TCP/TLS 连接管理 + Client 类（自动启动守护进程，按 CONNECT_MODE 三路路由）
│   ├── formatter.py         # 响应格式化输出（JSON 模式）
│   ├── renderer.py          # 终端快照渲染器（GDI+BuiltinGlyphs / SVG / Pillow 回退 / 纯文本）
│   ├── config_manager.py    # 纯内存客户端配置管理（--default 临时覆盖）
│   ├── ai_analyser.py       # AI 分析器（--ai-analyse 调用 aichat 做二次分析，按 uid 续聊）
│   └── input.py             # 输入文本处理（process_input / unescape_json_string / safe_print）

├── daemon/                  # ═══════ 守护进程层 ═══════
│   ├── __init__.py
│   ├── __main__.py          # 入口（python -m src.daemon），转调 lifecycle.main()
│   ├── lifecycle.py         # 守护进程入口（main + 日志/控制台处理 + 单实例获取）
│   ├── server.py            # DaemonServer（多 Listener 编排 + 认证上下文构建 + 生命周期）
│   ├── listener.py          # Listener（单端口 accept 循环，封装 plain/tls 传输 + AuthContext）
│   ├── handler.py           # RequestHandler（委托 handlers/ 子包）
│   └── handlers/            # ═══ 命令处理器子包（每命令一文件 + 派发器） ═══
│       ├── __init__.py
│       ├── base.py          # DaemonHandler 基类 + HandlerContext
│       ├── dispatcher.py    # DaemonDispatcher（内置 handler 派发 + 进程级插件消息路由）
│       ├── exec_handler.py  # exec 命令处理
│       ├── send_handler.py  # send 命令处理
│       ├── read_handler.py  # read 命令处理
│       ├── list_handler.py  # list 命令处理
│       ├── kill_handler.py  # kill 命令处理
│       ├── events_handler.py # events 命令处理
│       ├── stop_handler.py  # stop 命令处理
│       ├── closewin_handler.py # closewin 命令处理
│       ├── mouse_handler.py # mouse 命令处理
│       ├── status_handler.py # status 命令处理
│       ├── wait_handler.py  # wait 命令处理
│       └── utils.py         # 处理器工具函数（含 Git-Bash 路径提示）

├── transfer/                # ═══════ 文件传输核心层（客户端驱动 + 双端共享） ═══════
│   ├── __init__.py
│   ├── common.py            # 帧协议常量/错误类型（TransferError/TransferTimeoutError/TransferAbortedError）
│   ├── scan.py              # 本地/远端树扫描（清单生成）
│   ├── client_upload.py     # CLI 侧上传驱动（握手→清单→逐文件→进度）
│   └── client_download.py   # CLI 侧下载驱动
│   # 注：daemon 侧传输业务（judge/map/daemon_upload/daemon_download）位于
│   #     config/plugins/files/ 插件；帧编解码在 protocol/transfer.py

├── plugins/                 # ═══════ 插件系统 ═══════
│   ├── __init__.py
│   ├── base.py              # Plugin 基类 + PluginContext/ProcessPluginContext + HANDLED 哨兵
│   ├── loader.py            # 插件目录扫描与声明校验（triggers/message_types/needs_io）
│   ├── registry.py          # PluginRegistry（进程级插件单例实例化 + auto_load 匹配）
│   ├── host.py              # PluginHost（会话级挂载链、钩子调度、返回控制）
│   └── io.py                # PluginIO（进程级插件连接收发端口：消息 + 传输帧）

├── pty/                     # ═══════ 伪终端后端层 ═══════
│   ├── __init__.py
│   ├── pty_factory.py       # 工厂函数 create_pty + 平台检测
│   ├── base.py              # PseudoTerminal 抽象基类
│   ├── unix/                # ═══ Unix 子包 ═══
│   │   ├── __init__.py
│   │   ├── pty_impl.py      # UnixPseudoTerminal（os.openpty + fork + termios）
│   │   └── process.py       # Unix 进程管理
│   │   # Shell 探测见 common/shells.py（跨侧共享）
│   └── windows/             # ═══ Windows 子包（仅 Win32 加载） ═══
│       ├── __init__.py
│       └── wezterm_pty.py   # WeztermPseudoTerminal（wezterm-py Pty，OpenConsole 宿主）
│       # Shell 探测见 common/shells.py（跨侧共享）

├── ipc/                     # ═══════ 进程间通信层 ═══════
│   ├── __init__.py
│   ├── shm.py               # 共享内存工具（端口/PID + 认证令牌 + HMAC 密钥读写）
│   └── single_instance.py   # 单实例互斥锁（Windows 命名互斥 / Unix flock，守护进程与客户端共用）

├── logging_setup.py         # 日志系统共享工具（按模块分组写独立日志文件 + 前一日日志 gzip 归档）

├── terminal/                # ═══════ 终端屏幕层 ═══════
│   ├── __init__.py
│   ├── backends.py          # WeztermBackend（wezterm-py Terminal）+ ScreenCell/渲染函数
│   └── screen.py            # TerminalScreen（VT 序列解析 → 字符网格 → 屏幕快照）

├── session/                 # ═══════ 会话管理层 ═══════
│   ├── __init__.py
│   ├── manager.py           # SessionManager（会话 CRUD + stop_all）
│   ├── session.py           # Session 协调器（组合各子组件，委托线程管理）
│   ├── session_threads.py   # SessionThreads + SessionComponents（后台读者/监控线程管理）
│   └── publisher.py         # 会话状态发布器

├── encoding/                # ═══════ 编码探测层 ═══════
│   ├── __init__.py
│   ├── codec.py             # 编码探测与解码纯函数（detect_decode / decode_strip_tail / auto_detect / 智能裁剪）
│   └── detector.py          # EncodingDetector（编码探测状态管理）

├── output/                  # ═══════ 输出处理层 ═══════
│   ├── __init__.py
│   ├── buffer.py            # OutputBuffer（线程安全输出缓冲区）
│   ├── trigger.py           # TriggerMatcher + safe_regex_search（触发条件匹配 + ReDoS 防护）
│   └── events.py            # EventHistoryManager + PendingEvent（事件队列 + 历史 + 存在性检测）

├── process/                 # ═══════ 进程处理层 ═══════
│   ├── __init__.py
│   ├── base.py              # ProcessTreeTracker 抽象基类 + ProcessNotification
│   ├── monitor.py           # ProcessMonitor（进程树 diff + IOCP 排空 + 崩溃检测）
│   ├── info.py              # 进程查询与错误格式化（pid_exists 见 common/process.py）
│   ├── gui.py               # GuiDetector（GUI 窗口轮询检测，2s 节流）
│   ├── win32_error.py       # Windows NTSTATUS/Win32 错误码格式化
│   ├── unix/                # ═══ Unix 子包 ═══
│   │   ├── __init__.py
│   │   └── pgid_tracker.py  # PgidProcessTreeTracker（进程组追踪 + waitpid 轮询崩溃检测）
│   └── windows/             # ═══ Windows 子包 ═══
│       ├── __init__.py
│       ├── api.py           # Windows API 绑定（Job 相关 ctypes 声明）
│       ├── job_tracker.py   # JobProcessTreeTracker（Job Object 追踪 + IOCP 通知 + KILL_ON_JOB_CLOSE）
│       └── gui_monitor.py   # GuiWindowMonitor + GuiWindowInfo（EnumWindows GUI 窗口轮询）

├── input/                   # ═══════ 输入处理层 ═══════
│   ├── __init__.py
│   ├── interceptor.py       # 输入处理辅助（编码转换 + 鼠标动作执行）
│   ├── mouse.py             # 鼠标动作编码与坐标解析
│   └── wezterm_input.py     # wezterm 模式感知输入编码器

├── web/                     # ═══════ Web 服务器层（洋葱架构） ═══════
│   ├── __init__.py
│   ├── server.py            # WebServer（实现见 presentation/server.py）
│   ├── history.py           # HistoryStore 导出（实现见 infrastructure/repositories/history_store.py）
│   ├── httpserver.ps1       # Web 服务器启动脚本（PowerShell）
│   ├── httpserver.sh        # Web 服务器启动脚本（Unix）
│   ├── application/         # ═══ 用例层 ═══
│   │   ├── __init__.py
│   │   ├── adaptive_lock.py # 自适应排他锁服务
│   │   ├── dispatcher.py    # WebSocket 消息分发器
│   │   ├── handlers.py      # WebSocket 消息用例处理器
│   │   ├── ports.py         # 应用端口（接口）
│   │   └── services.py      # 编码服务、订阅服务
│   ├── domain/              # ═══ 领域层 ═══
│   │   ├── __init__.py
│   │   ├── entities.py      # 领域实体（SessionDetail 等）
│   │   └── settings_schema.py # 设置项 Schema
│   ├── infrastructure/      # ═══ 基础设施层 ═══
│   │   ├── __init__.py
│   │   ├── thread_executor.py # 线程执行器
│   │   ├── cursor_locator_adapter.py # 光标定位器适配器
│   │   ├── auth/            # ═══ Web 认证子包 ═══
│   │   │   ├── __init__.py
│   │   │   └── session_store.py  # 会话存储（登录态）
│   │   ├── repositories/    # ═══ 仓储适配器 ═══
│   │   │   ├── __init__.py
│   │   │   ├── history_repository_adapter.py
│   │   │   ├── history_store.py
│   │   │   └── session_repository_adapter.py
│   │   ├── system/          # ═══ 系统服务 ═══
│   │   │   ├── __init__.py
│   │   │   ├── shell_provider.py  # Shell 提供者
│   │   │   └── stats_provider.py  # 系统 CPU/内存统计
│   │   └── web/             # ═══ Web 基础设施 ═══
│   │       ├── __init__.py
│   │       ├── connection_context.py # 连接上下文
│   │       ├── event_publisher.py    # 事件发布器
│   │       └── fastapi_transport.py  # FastAPI 传输层
│   ├── presentation/        # ═══ 展示层 ═══
│   │   ├── __init__.py
│   │   ├── server.py        # FastAPI + uvicorn 服务器
│   │   └── controllers/     # ═══ 控制器 ═══
│   │       ├── __init__.py
│   │       ├── websocket_controller.py  # WebSocket 控制器
│   │       ├── fastscreen_controller.py # FastScreen 控制器
│   │       ├── settings_controller.py   # 设置控制器
│   │       └── auth_controller.py       # 登录/认证控制器
│   └── static/              # ═══ 前端静态资源（完整结构见 [web-static.md](web-static.md)） ═══

├── fastscreen/              # ═══════ 快速屏幕流层 ═══════
│   ├── __init__.py
│   ├── adapter.py           # 适配器
│   ├── ports.py             # 端口定义
│   ├── server.py            # 流服务器（aiohttp）
│   └── streamers/           # ═══ 流编码器子包 ═══
│       ├── __init__.py
│       ├── h264.py          # H.264 流编码
│       ├── h264_mse.py      # H.264 MSE 流编码
│       ├── mjpeg.py         # MJPEG 流编码
│       ├── manager.py       # 流管理器
│       └── encoding/        # ═══ 编码工具子包 ═══
│           ├── __init__.py
│           ├── fmp4.py      # fMP4 编码
│           ├── h264.py      # H.264 编码工具
│           └── mjpeg.py     # MJPEG 编码工具

└── vnc/                     # ═══════ VNC 远程桌面层 ═══════
    ├── __init__.py
    ├── adapter.py           # VNC 适配器（管理 winvnc.exe 进程）
    ├── ports.py             # 端口定义
    ├── process_manager.py   # VNC 进程管理（winvnc 启停）
    ├── password_loader.py   # VNC 密码加载
    └── src/                 # VNC 密码工具
        └── vnc_password.py  # VNC 密码生成/验证
                            # 注：noVNC 前端位于 src/web/static/vendor/novnc/
```

# src/ 主包

> 主 Python 包。前端静态资源（`src/web/static/`）单独列在 [web-static.md](web-static.md)。

```
src/
├── __main__.py              # CLI 入口（瘦身：转调 cli/main.py）

├── cli/                     # ═══════ CLI 命令子系统（命令注册/解析/派发） ═══════
│   ├── __init__.py          # 导出 main()（CLI 入口）
│   ├── main.py              # CLI 入口：注册表装配 + 公共管线 + 派发 + 异常处理
│   ├── base.py              # Command 基类（add_arguments/validate/run）+ CommandContext
│   ├── registry.py          # CommandRegistry（注册/构建解析器/派发，构建期选项冲突检测）+ _HintParser
│   ├── common_args.py       # 共享参数组（common/session_io/output）+ 配置键转换/idle 警告
│   ├── pipeline.py          # 公共管线（config ops / debug / cli_plugins / 通用冲突校验）
│   ├── windows.py           # Windows exec -c 命令引号修复
│   └── commands/            # ═══ 每命令一个文件（与 daemon/handlers/ 对称） ═══
│       ├── __init__.py      # 注册清单 register_all（顺序 = 帮助显示顺序）
│       ├── start.py         # start 命令
│       ├── stop.py          # stop 命令（--force 强制清理）
│       ├── status.py        # status 命令
│       ├── list_.py         # list 命令
│       ├── exec.py          # exec 命令（-c 必填校验 / idle 警告）
│       ├── send.py          # send / advsend 命令（共用实现，idle 警告）
│       ├── read.py          # read 命令（--offset 与 --full 冲突检测）
│       ├── kill.py          # kill 命令
│       ├── events.py        # events 命令（时间补全 _maybe_expand_time）
│       ├── closewin.py      # closewin 命令
│       ├── mouse.py         # mouse 命令（坐标解析 + 动作构建/参数校验）
│       ├── wait.py          # wait 命令
│       ├── keygen.py        # keygen 命令（本地命令，生成 Ed25519 密钥对）
│       ├── set_default.py   # set-default 命令（本地命令，持久化默认配置）
│       ├── plugin.py        # plugin 命令（list/ls/attach/detach/cmd）
│       ├── workflow.py      # workflow 命令（run/list/show/cancel + --vars 解析）
│       └── file.py          # file 命令（read/write/edit/grep/glob/upload/download + 内容解析）

├── config/                  # ═══════ 配置中心（TOML 加载器，数据文件在 <项目根>/config/） ═══════
│   ├── __init__.py          # 包导出 + 配置域归档说明
│   ├── _loader.py           # TOML 加载/展平/合并工具（load_toml(filename, domain) / flatten / merge）
│   ├── common.py            # 共有配置加载（common.toml + IS_WINDOWS / DATA_DIR / PROJECT_ROOT）
│   ├── shared.py            # 跨侧共享配置加载（common + shared.toml + PORT_FILE / LOG_DIR）
│   ├── daemon.py            # 守护进程配置加载（common + shared + daemon/ + logging/ + web/；web.toml 可选，缺失即 web 禁用）
│   ├── plugins.py           # 插件系统配置加载（config/plugins/plugins.json，可选，缺失即插件系统禁用）
│   ├── client.py            # 客户端配置加载（common + shared + client/ + PORT_FILE / LOG_DIR）
│   ├── transfer.py          # 传输协议配置加载（transfer.toml）
│   └── sandbox.py           # 沙箱配置加载（daemon/sandbox.toml，可选，缺失即沙箱关闭）
│
│   # TOML 数据文件（config/ 根：common/shared/transfer；
│   # config/daemon/：daemon/logging/web/sandbox/vnc/vnc.example；
│   # config/client/：client；
│   # config/plugins/plugins.json 为 daemon 侧插件注册；
│   # vnc*.toml 为 winvnc.exe 外部配置，Python 不加载），
│   # 清单见 <项目根>/config/README.md
│
├── optional.py              # ═══════ 可选模块惰性导入网关 ═══════
│   # 集中探测并缓存 web/vnc/screenshare/cursorlocator/sandbox/plugins 可用性；
│   # 提供 *available() 与 get_*_cls() 工厂函数，缺失模块返回 None/False 不抛 ImportError；
│   # 供 daemon/（惰性获取 WebServer）与 web/（惰性获取 Vnc/Screenshare/CursorLocator adapter）使用

├── protocol/                # ═══════ 通信协议层 ═══════
│   ├── __init__.py
│   ├── message.py           # Message 类（JSON 换行分隔协议：编码/解码/收发 + ping 探测）
│   ├── envelope.py          # 线协议信封 + 分组载荷（请求 op/condition/output/io，响应 data/state/meta）
│   ├── transfer.py          # 文件传输二进制帧协议（file upload/download 专用，零业务编解码）
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
│   ├── password/            # ═══ 共享密码认证（basic 监听器，密码即 HMAC 密钥） ═══
│   │   ├── __init__.py
│   │   └── authenticator.py # PasswordAuthenticator + PasswordCredentialProvider
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
│   ├── transport.py         # TCP/TLS 连接管理 + Client 类（自动启动守护进程，按 CONNECT_MODE 三路路由；信封封装接缝）
│   ├── result.py            # 类型化结果模型（Result / from_response 工厂，含稳定错误码）
│   ├── presenter.py         # 人类可读渲染层（内容→stdout / 元信息→stderr / 错误+退出码）
│   ├── cli_plugins.py       # CLI 插件宿主（CliPluginHost，kind=cli 钩子链）
│   ├── renderer/            # ═══ 终端快照渲染器（SVG / Pillow / GDI / box-drawing） ═══
│   │   ├── __init__.py      # 包导出（render_to_file / render_svg_string / is_image_ext）
│   │   ├── common.py        # 渲染共享基础（颜色映射 / 字符宽度 / 行格式展开）
│   │   ├── svg.py           # SVG 矢量渲染 + scour 压缩
│   │   ├── image.py         # 像素渲染后端（Pillow 跨平台 / Windows GDI 原生）
│   │   └── box_drawing.py   # Box Drawing 字符的 GDI 几何绘制原语
│   ├── config_manager.py    # 纯内存客户端配置管理（--default 临时覆盖）
│   └── input.py             # 输入文本处理（process_input / unescape_json_string / safe_print）

├── daemon/                  # ═══════ 守护进程层 ═══════
│   ├── __init__.py
│   ├── __main__.py          # 入口（python -m src.daemon），转调 lifecycle.main()
│   ├── lifecycle.py         # 守护进程入口（main + 日志/控制台处理 + 单实例获取）
│   ├── server.py            # DaemonServer（多 Listener 编排 + 认证上下文构建 + WorkflowManager 装配）
│   ├── listener.py          # Listener（单端口 accept 循环，封装 tcp/tls 传输 + AuthContext）
│   ├── handler.py           # RequestHandler（委托 handlers/ 子包）
│   ├── execution.py         # 执行原语（快照/子进程执行流程，exec/send/read handler 与 workflow 共用）
│   ├── conditions.py        # 返回条件统一声明（ReturnConditions.from_msg + Reason 词表）
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
│       ├── plugin_handler.py # plugin 命令处理（list/ls/attach/detach/cmd 插件管理）
│       ├── workflow_handler.py # workflow 命令处理（run/list/show/cancel）
│       ├── status_handler.py # status 命令处理
│       ├── wait_handler.py  # wait 命令处理
│       └── utils.py         # 处理器工具函数（含 Git-Bash 路径提示）

├── workflow/                # ═══════ workflow 脚本编排子系统（YAML + DAG 并行调度） ═══════
│   ├── __init__.py
│   ├── definition.py        # YAML 定义解析与校验（步骤 schema/依赖环检测/隐式依赖显式化）
│   ├── expr.py              # 安全表达式求值（AST 白名单）：if 条件 + {{...}} 插值
│   ├── engine.py            # DAG 调度引擎（依赖图 + 线程池并行 + 失败传播/重试/取消）
│   ├── runner.py            # WorkflowRun（单次运行状态机 + 事件日志）
│   └── manager.py           # WorkflowManager（运行注册表：启动/查询/取消/容量淘汰）

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
│   ├── base.py              # PseudoTerminal 抽象基类
│   ├── pty_factory.py       # 工厂函数 create_pty + 平台检测
│   ├── subprocess_pty.py    # SubprocessPseudoTerminal（--subprocess 模式，Popen 双管道）
│   └── wezterm_pty.py       # WeztermPseudoTerminal（wezterm-py Pty 跨平台统一，OpenConsole 宿主）
│   # Shell 探测见 common/shells.py（跨侧共享）

├── sandbox/                 # ═══════ 沙箱会话层（win_sandbox 原生进程内封装） ═══════
│   ├── __init__.py
│   ├── manager.py           # SandboxSessionManager（win_sandbox_native 封装：启停/命令/通知队列）
│   ├── pty.py               # SandboxPty（沙箱 ConPTY 后端，HPCON 外部传入）
│   └── tracker.py           # SandboxProcessTreeTracker（进程树追踪委托原生能力）

├── ipc/                     # ═══════ 进程间通信层 ═══════
│   ├── __init__.py
│   ├── shm.py               # 共享内存工具（端口/PID + 认证令牌 + HMAC 密钥读写）
│   └── single_instance.py   # 单实例互斥锁（Windows 命名互斥 / Unix flock，守护进程与客户端共用）

├── logging/                 # ═══════ 日志系统（异步队列 + 分组日志 + 归档） ═══════
│   ├── __init__.py          # 包导出（get_logger / bind / unbind / setup_* / shutdown）
│   ├── setup.py             # 日志装配入口（daemon/client 两侧初始化）
│   ├── config.py            # 日志配置结构（从 config/ 常量组装 LoggingConfig）
│   ├── registry.py          # logger 名注册表（get_logger 校验，防配置遗漏导致日志丢失）
│   ├── context.py           # ContextVar 上下文绑定（session_id/connection_id 自动注入）
│   ├── _queue.py            # 异步队列核心（QueueHandler + pty-log-writer 后台线程）
│   ├── archiver.py          # LogArchiver（前一日日志 gzip 归档）
│   ├── formatters.py        # 文本格式器（上下文字段注入）
│   └── handlers.py          # 文件 handler 封装（毫秒时间戳文件名）

├── terminal/                # ═══════ 终端屏幕层 ═══════
│   ├── __init__.py
│   ├── backends.py          # WeztermBackend（wezterm-py Terminal）+ ScreenCell/渲染函数
│   └── screen.py            # TerminalScreen（VT 序列解析 → 字符网格 → 屏幕快照）

├── session/                 # ═══════ 会话管理层 ═══════
│   ├── __init__.py
│   ├── manager.py           # SessionManager（会话 CRUD + stop_all）
│   ├── publisher.py         # SessionPublisher（会话状态发布器）
│   └── session/             # Session 类实现子包
│       ├── __init__.py      # 导出 Session / InputMixin / OutputMixin / TriggerMixin / EventsMixin / Threads / Components
│       ├── session.py       # Session 协调器基类（子组件装配 + start/stop + 状态代理，组合 *Mixin）
│       ├── io.py            # InputMixin（输入写入/信号/鼠标动作）
│       ├── output.py        # OutputMixin（输出读取/屏幕快照/resize/终端状态）
│       ├── trigger.py       # TriggerMixin（触发条件与等待）
│       ├── events.py        # EventsMixin（事件接收/历史/退出回调）
│       ├── threads.py       # Threads + Components（后台读者/监控线程管理）
│       └── _win_console.py  # Windows Ctrl+C 控制台辅助（AttachConsole + 控制台处理器）

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
│   │       ├── screenshare_controller.py # Screenshare 控制器
│   │       ├── settings_controller.py   # 设置控制器
│   │       └── auth_controller.py       # 登录/认证控制器
│   └── static/              # ═══ 前端静态资源（完整结构见 [web-static.md](web-static.md)） ═══

├── screenshare/             # ═══════ 屏幕查看流层 ═══════
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

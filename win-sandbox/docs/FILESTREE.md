# win-sandbox 项目文件树

```
win-sandbox/
├── docs/                                   # ═══════ 文档 ═══════
│   ├── API_REFERENCE.md                    # Python API 完整参考
│   ├── ARCHITECTURE.md                     # 架构与技术原理（权威设计文档）
│   ├── BLACKBOX_TEST_REPORT.md             # 黑盒测试报告（公开 API 观测）
│   ├── DEPLOYMENT.md                       # 构建/部署/wheel 打包指南
│   ├── FILESTREE.md                        # 本文件树文档
│   ├── Lessons-Learned.md                  # 踩坑记录（项目记忆）
│   └── USER_GUIDE.md                       # 用户手册
├── python/                                 # ═══════ Python 包 ═══════
│   ├── scripts/                            # ═══ 构建辅助脚本 ═══
│   │   └── fix_wheel_platform.py           # wheel 平台标记修正（py3-none-any → win_amd64）
│   ├── win_sandbox/                        # ═══ Python 包（pyd 薄封装） ═══
│   │   ├── __init__.py                     # 包入口（加载 pyd + 导出公共 API）
│   │   ├── exceptions.py                   # 异常层级（SandboxError 等）
│   │   └── helpers.py                      # 句柄读写/定时器/轮询等纯 ctypes 工具
│   └── pyproject.toml                      # 包构建配置（hatchling）
├── src/                                    # ═══════ C++ 核心（干净架构四层） ═══════
│   ├── adapters/                           # ═══ 接口适配器层 ═══
│   │   ├── ConfigLoader.cpp                # IConfigLoader 实现（JSON→SandboxConfig 严格 schema 校验）
│   │   ├── ConfigLoader.hpp                # 配置加载适配器声明
│   │   ├── NativeSandboxInstance.cpp       # 沙箱实例组装（Job/token/可写区/ETW）+ 进程管理与清理
│   │   ├── NativeSandboxInstance.hpp       # 沙箱实例管理器声明
│   │   ├── PermissionDetector.cpp          # 权限/能力检测（管理员判定 + 降级原因）
│   │   ├── PermissionDetector.hpp          # 权限检测器声明
│   │   ├── StartProcessPayloadParser.cpp   # start_process JSON payload 严格反序列化
│   │   ├── StartProcessPayloadParser.hpp   # payload 解析声明
│   │   └── StringUtils.hpp                 # 字符串工具（stderr AccessDenied 关键字检测，纯头文件）
│   ├── bindings/                           # ═══ pybind11 绑定层（框架最外层） ═══
│   │   ├── BindingCommon.hpp               # py↔json 转换 / Result 错误转异常等公共辅助（纯头文件）
│   │   ├── CallbacksBinding.cpp            # 绑定 contains_access_denied_keyword 函数
│   │   ├── CallbacksBinding.hpp            # 回调辅助注册声明
│   │   ├── ConfigBinding.hpp               # 从 Python 参数组装 JSON 复用 payload 解析（纯头文件）
│   │   ├── ProcessBinding.cpp              # PyProcess 类（句柄属性/回调 GIL 桥接/生命周期）
│   │   ├── ProcessBinding.hpp              # 进程绑定注册声明
│   │   ├── SandboxInstanceBinding.cpp      # PySandboxInstance 类（构造/start_process/shutdown）
│   │   ├── SandboxInstanceBinding.hpp      # 沙箱实例绑定注册声明
│   │   └── module.cpp                      # PYBIND11_MODULE 入口（注册全部绑定）
│   ├── core/                               # ═══ 领域层（实体 / 端口 / 用例） ═══
│   │   ├── entities/                       # ═══ 领域实体（纯值对象，不依赖 windows.h） ═══
│   │   │   ├── BehaviorEvent.hpp           # ETW 行为事件结构（进程/文件/注册表/网络 + JSON 序列化）
│   │   │   ├── Callbacks.hpp               # 回调 payload 值对象（事件回调契约）
│   │   │   ├── ErrorCode.hpp               # 跨模块全局错误码枚举
│   │   │   ├── EtwConfig.hpp               # ETW 监控配置（session/provider/过滤/降级）
│   │   │   ├── GlobalQuota.hpp             # 全局资源配额配置与使用统计
│   │   │   ├── IsolationPolicy.hpp         # 隔离策略（网络 allowlist + 剪贴板开关）
│   │   │   ├── JobAccountingInfo.hpp       # Job 会计信息值对象（CPU/IO/内存）
│   │   │   ├── JobNotification.hpp         # Job 事件通知值对象
│   │   │   ├── NetworkRule.hpp             # 网络白名单规则（IP/端口/协议）
│   │   │   ├── ResourceQuota.hpp           # 资源配额值对象（CPU/内存/IO/进程数/超时）
│   │   │   ├── Result.hpp                  # Rust 风格 Result<T> 错误处理模板
│   │   │   ├── SandboxConfig.cpp           # BuildDefault 默认配置构造
│   │   │   ├── SandboxConfig.hpp           # 沙箱全局配置领域对象（聚合子配置）
│   │   │   ├── SandboxedProcess.hpp        # 被隔离进程领域视图（状态/退出原因）
│   │   │   └── StartProcessRequest.hpp     # 启动进程请求实体（用例输入）
│   │   ├── ports/                          # ═══ 端口接口（抽象外部世界，全部纯虚） ═══
│   │   │   ├── IConfigLoader.hpp           # 配置加载端口
│   │   │   ├── IEtwMonitor.hpp             # ETW 行为监控端口
│   │   │   ├── IGlobalQuotaManager.hpp     # 全局配额端口（共享内存跨进程）
│   │   │   ├── IJobNotificationSink.hpp    # Job 通知回调端口
│   │   │   ├── IJobObject.hpp              # Job Object 端口（void* 隐藏 HANDLE）
│   │   │   ├── ILogger.hpp                 # 日志端口（core 不绑定 spdlog）
│   │   │   ├── IProcessLauncher.hpp        # 进程启动端口（CreateProcess/管道/信号/等待/句柄关闭）
│   │   │   ├── ISilo.hpp                   # Server Silo 隔离端口（可选增强）
│   │   │   ├── ITokenIsolator.hpp          # 隔离 token 派生端口（Low IL）
│   │   │   ├── IWfpEngine.hpp              # 网络白名单引擎端口（SOCKS5 代理）
│   │   │   └── IWriteArea.hpp              # 可写区端口（创建/查询/删除）
│   │   └── usecases/                       # ═══ 用例层（启动进程用例） ═══
│   │   │   ├── NativeSandboxedProcess.cpp  # 启动进程用例实现（token+可写区+代理+Launch+IOCP）
│   │   │   └── NativeSandboxedProcess.hpp  # 启动进程用例声明
│   ├── infra/                              # ═══ 框架与驱动层（Win32 实现） ═══
│   │   ├── etw/                            # ═══ ETW 行为监控实现（IEtwMonitor） ═══
│   │   │   ├── EtwMonitorImpl.cpp          # ETW 监控实现（真内核 session / 非管理员降级轮询）
│   │   │   ├── EtwMonitorImpl.hpp          # ETW 监控实现声明
│   │   │   ├── EventRecordParser.cpp       # EVENT_RECORD→BehaviorEvent 解析（TDH schema）
│   │   │   ├── EventRecordParser.hpp       # 事件解析器声明
│   │   │   └── RingBuffer.hpp              # 环形事件缓冲（满丢弃 + seq 丢包检测）
│   │   ├── globalquota/                    # ═══ 全局配额实现（IGlobalQuotaManager） ═══
│   │   │   ├── GlobalQuotaManagerImpl.cpp  # 共享内存+Mutex 跨进程配额池实现
│   │   │   └── GlobalQuotaManagerImpl.hpp  # 全局配额实现声明
│   │   ├── job/                            # ═══ Job Object 实现（IJobObject） ═══
│   │   │   ├── JobObjectImpl.cpp           # 资源限制/Assign/IOCP 通知/崩溃静默实现
│   │   │   └── JobObjectImpl.hpp           # Job Object 实现声明
│   │   ├── logging/                        # ═══ 日志系统实现（ILogger） ═══
│   │   │   ├── Logger.cpp                  # spdlog 初始化/清理（文件 + 彩色 stderr）
│   │   │   └── Logger.hpp                  # 日志器声明
│   │   ├── process/                        # ═══ 进程启动实现（IProcessLauncher） ═══
│   │   │   ├── ProcessLauncherImpl.cpp     # CreateProcessW/AsUser + 管道 + ConPTY 实现
│   │   │   └── ProcessLauncherImpl.hpp     # 进程启动实现声明
│   │   ├── silo/                           # ═══ Server Silo 实现（ISilo） ═══
│   │   │   ├── SiloImpl.cpp                # ntdll 动态加载将 Job 升级为 Silo
│   │   │   └── SiloImpl.hpp                # Silo 实现声明
│   │   ├── token/                          # ═══ Low IL token 实现（ITokenIsolator） ═══
│   │   │   ├── TokenIsolatorImpl.cpp       # DuplicateTokenEx 派生 Low IL primary token
│   │   │   └── TokenIsolatorImpl.hpp       # token 实现声明
│   │   ├── wfp/                            # ═══ 网络白名单实现（IWfpEngine，SOCKS5） ═══
│   │   │   ├── WfpEngineImpl.cpp           # 本地 SOCKS5 代理白名单（拦截回调）
│   │   │   └── WfpEngineImpl.hpp           # 白名单引擎实现声明
│   │   ├── writearea/                      # ═══ 可写区实现（IWriteArea） ═══
│   │   │   ├── WriteAreaImpl.cpp           # 打 Low 标签可写目录 + 递归删除
│   │   │   └── WriteAreaImpl.hpp           # 可写区实现声明
│   │   ├── StartupCleanup.cpp              # 启动期残留清理（会话目录 + 残留 ETW session）
│   │   └── StartupCleanup.hpp              # 残留清理工具声明
│   └── CMakeLists.txt                      # 构建目标声明（win_sandbox_native.pyd）
├── tests/                                  # ═══════ 测试 ═══════
│   ├── e2e/                                # ═══ e2e 测试（pybind11 直调功能验证） ═══
│   │   ├── _native_helpers.py              # e2e 共用工具（加载 pyd / make_sandbox / drain 等）
│   │   ├── REGRESSION_REPORT.md            # 全量回归报告
│   │   ├── run_all_regression.py           # 全量回归运行器（逐用例子进程汇总）
│   │   ├── smoke.py                        # 安装验证冒烟（创建→capabilities→关闭）
│   │   ├── test_behavior_log.py            # 行为事件日志测试
│   │   ├── test_cleanup.py                 # 残留清理（会话目录/ETW session）测试
│   │   ├── test_degraded_monitor.py        # ETW 降级轮询模式测试
│   │   ├── test_etw_admin.py               # ETW 管理员模式测试（非管理员 SKIP）
│   │   ├── test_global_quota.py            # 全局配额测试（未注入，SKIP）
│   │   ├── test_helpers.py                 # Python helpers 模块单测
│   │   ├── test_hpcon_conpty.py            # 外部 ConPTY（hpcon）集成测试
│   │   ├── test_job_enhancement.py         # Job 增强（进程列表/退出码/崩溃静默）测试
│   │   ├── test_lowil_isolation.py         # Low IL 文件系统隔离语义测试
│   │   ├── test_multiprocess.py            # 多进程/多实例并发测试
│   │   ├── test_native_etw.py              # ETW 回调基础测试
│   │   ├── test_native_smoke.py            # native 扩展冒烟测试
│   │   ├── test_network_allowlist.py       # 网络 allowlist 测试（需管理员）
│   │   ├── test_oj_scenario.py             # OJ 评测场景（限额/超时）测试
│   │   ├── test_permission_matrix.py       # 权限能力矩阵测试
│   │   ├── test_process_tree.py            # 进程树事件/退出码测试
│   │   ├── test_resource_quota.py          # 资源配额与统计测试
│   │   ├── test_scenario_c_sample.py       # 样本分析场景（隔离+AccessDenied）测试
│   │   ├── test_scenario_d_ci.py           # CI 多实例并行场景测试
│   │   ├── test_signal.py                  # 信号控制（Kill/CtrlBreak）测试
│   │   ├── test_silo.py                    # Server Silo 降级验证
│   │   └── test_write_stdin.py             # 交互式 stdin 写入测试
│   ├── unit/                               # ═══ C++ 单测/验证程序（ctest） ═══
│   │   ├── crash_dummy.cpp                 # 崩溃测试助手（空指针触发 ACCESS_VIOLATION）
│   │   ├── probe_t16.cpp                   # Token/WriteArea 行为验证
│   │   ├── verify_t11.cpp                  # core 层无 windows.h 依赖编译验证
│   │   ├── verify_t14.cpp                  # ProcessLauncherImpl 运行时验证
│   │   ├── verify_t17.cpp                  # ConfigLoader 解析验证
│   │   ├── verify_t27.cpp                  # isolation 段 + payload 解析验证
│   │   └── verify_t28.cpp                  # Job 增强（进程列表/退出码/崩溃分类）验证
│   └── CMakeLists.txt                      # 测试构建定义（注册 ctest）
├── third_party/                            # ═══════ 第三方依赖（子模块，不逐文件展开） ═══════
│   ├── nlohmann_json/                      # JSON 解析（header-only）
│   ├── pybind11/                           # Python ↔ C++ 绑定
│   ├── spdlog/                             # 日志库（静态链接，内置 fmt）
│   └── wil/                                # Windows Implementation Libraries（RAII header-only）
├── .gitignore                              # 忽略规则（构建产物/日志/临时文件）
├── AGENTS.md                               # 项目智能体约定
├── BUILD.ps1                               # 统一构建脚本（vcvars 探测 / Config / Rebuild）
├── CMakeLists.txt                          # 顶层 CMake（第三方依赖集成 + 编译链路）
└── README.md                               # 项目说明
```

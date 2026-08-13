# tests/ 测试套件

```
tests/
├── conftest.py              # pytest 全局配置
├── repro_reflow_bug.py      # reflow bug 复现脚本
├── mouse_inject_tool.py     # 鼠标注入调试工具（手动运行）
├── test_ai_analyser.py      # AI 分析器测试
├── test_config_manager_ai.py # AI 配置管理器测试
├── test_e2e_ai_analyse.py   # AI 分析端到端测试
├── test_tui/                # TUI 测试子包
│   └── test_click.py

├── unit/                    # ═══ 单元测试（隔离测试单一模块） ═══
│   ├── test_connect_robustness.py  # 连接健壮性测试
│   ├── test_job.py          # Job 对象测试
│   ├── auth/                # 认证层测试
│   │   ├── test_context.py
│   │   ├── test_ed25519_signer.py
│   │   ├── test_or_verifier.py
│   │   ├── test_pubkey.py
│   │   ├── test_keys.py
│   │   └── tls/             # TLS 认证测试
│   ├── client/              # 客户端层测试
│   │   ├── test_transport.py
│   │   ├── test_tls_transport.py
│   │   ├── test_keygen.py
│   │   ├── test_input.py
│   │   ├── test_config_manager.py
│   │   ├── test_formatter.py
│   │   └── test_renderer.py
│   ├── daemon/              # 守护进程层测试
│   │   ├── test_handler.py
│   │   ├── test_server.py
│   │   ├── test_server_tls.py
│   │   ├── test_listener.py
│   │   └── test_lifecycle.py
│   ├── pty/                 # PTY 层测试
│   │   ├── test_factory.py
│   │   ├── test_base.py
│   │   ├── unix/            # Unix PTY 测试
│   │   │   └── test_process.py
│   │   └── windows/
│   │       ├── test_error_msg.py
│   │       └── test_job.py
│   ├── protocol/            # 协议层测试
│   │   ├── test_message.py
│   │   ├── test_ansi.py
│   │   └── test_encoding.py
│   ├── session/             # 会话层测试
│   │   ├── test_screen.py
│   │   ├── test_mouse.py
│   │   ├── test_manager.py
│   │   ├── test_shm_utils.py
│   │   ├── process/
│   │   ├── output/
│   │   └── encoding/
│   ├── terminal/            # 终端层测试
│   │   ├── test_grid_screen.py
│   │   └── test_grid.py
│   ├── web/                 # Web 层测试
│   │   ├── test_ws_handler.py
│   │   ├── test_settings_controller.py
│   │   └── test_settings_schema.py
│   ├── test_transport.py
│   ├── test_server.py
│   ├── test_pty_drain.py
│   ├── test_main.py
│   ├── test_handler.py
│   ├── test_factory.py
│   ├── test_config_refactor.py
│   ├── test_config.py
│   ├── test_pty_subprocess.py
│   ├── test_pty_shell.py
│   ├── test_trigger.py
│   ├── test_session_events.py
│   ├── test_process_monitor.py
│   ├── test_process_info.py
│   ├── test_output_buffer.py
│   ├── test_event_history.py
│   ├── test_encoding_detector.py
│   ├── test_terminal_size.py
│   ├── test_lifecycle.py
│   ├── test_shm_utils.py
│   ├── test_windows_error.py
│   ├── test_manager.py
│   ├── test_cli_optimization.py
│   └── test_gui_monitor.py

├── integration/             # ═══ 集成测试（多模块协作） ═══
│   ├── test_terminal_size_integration.py
│   ├── test_mouse.py
│   ├── test_debug_subprocess.py
│   ├── test_session.py
│   ├── test_single_instance.py
│   └── auth/                # 认证集成测试
│       └── test_assembly.py

├── e2e/                     # ═══ 端到端测试 ═══
│   ├── test_vnc_job_kill.py
│   ├── test_vnc_job.py
│   ├── test_vnc_proxy.py
│   ├── test_tls_auth_e2e.py
│   ├── test_pubkey_auth_e2e.py
│   ├── test_keygen_e2e.py
│   ├── test_resize_cursor_sync.py
│   ├── test_resize_cursor_e2e.py
│   ├── _repro_pubkey_e2e.py  # pubkey e2e 复现脚本
│   └── _repro_authorized_keys # pubkey 复现数据文件

└── web/                     # ═══ Web 界面测试 ═══
    ├── __init__.py
    ├── test_web.py
    ├── test_mse_detailed.py
    ├── test_mse_ws.py
    └── test_h264_ws.py
```

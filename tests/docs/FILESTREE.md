# tests/ 测试套件

```
tests/
├── __init__.py              # 测试包标记（pytest 可收集）
├── conftest.py              # pytest 全局配置
├── mouse_inject_tool.py     # 鼠标注入调试工具（手动运行）

├── docs/                    # 文档（文件树文档见 [FILESTREE.md](FILESTREE.md)）

├── test_tui/                # TUI 测试子包
│   ├── __init__.py
│   └── test_click.py

├── unit/                    # ═══ 单元测试（隔离测试单一模块） ═══
│   ├── __init__.py
│   ├── auth/                # 认证层测试
│   │   ├── __init__.py
│   │   ├── tls/             # TLS 认证测试
│   │   │   ├── __init__.py
│   │   │   ├── test_cert_manager.py
│   │   │   └── test_known_hosts.py
│   │   ├── test_context.py
│   │   ├── test_ed25519_signer.py
│   │   ├── test_keys.py
│   │   ├── test_password.py   # 共享密码认证测试
│   │   └── test_pubkey.py
│   ├── client/              # 客户端层测试
│   │   ├── __init__.py
│   │   ├── test_cli_optimization.py
│   │   ├── test_config_manager.py
│   │   ├── test_connect_robustness.py
│   │   ├── test_file_cli.py
│   │   ├── test_input.py
│   │   ├── test_keygen.py
│   │   ├── test_main.py
│   │   ├── test_presenter.py
│   │   ├── test_terminal_size.py
│   │   └── test_transport.py
│   ├── daemon/              # 守护进程层测试
│   │   ├── __init__.py
│   │   ├── test_encoding_detector.py
│   │   ├── test_event_history.py
│   │   ├── test_file_edit_handler.py
│   │   ├── test_file_grep_glob_handler.py
│   │   ├── test_file_read_handler.py
│   │   ├── test_file_upload_download.py
│   │   ├── test_file_write_handler.py
│   │   ├── test_handler.py
│   │   ├── test_lifecycle.py
│   │   ├── test_listener.py
│   │   ├── test_output_buffer.py
│   │   ├── test_plugin_handler.py
│   │   ├── test_process_info.py
│   │   ├── test_process_monitor.py
│   │   ├── test_pty_drain.py
│   │   ├── test_server.py
│   │   ├── test_server_tls.py
│   │   ├── test_session_events.py
│   │   └── test_trigger.py
│   ├── daemonctl/           # daemon 控制测试
│   │   ├── __init__.py
│   │   ├── test_lifecycle.py
│   │   └── test_tls.py
│   ├── files/               # 文件插件测试（read / search / transfer / write）
│   │   ├── __init__.py
│   │   ├── read/
│   │   │   ├── __init__.py
│   │   │   └── test_reader.py
│   │   ├── search/
│   │   │   ├── __init__.py
│   │   │   ├── test_glob.py
│   │   │   ├── test_grep.py
│   │   │   └── test_ignore.py
│   │   ├── transfer/
│   │   │   ├── __init__.py
│   │   │   ├── test_judge.py
│   │   │   ├── test_map.py
│   │   │   ├── test_protocol.py
│   │   │   └── test_scan.py
│   │   ├── write/
│   │   │   ├── __init__.py
│   │   │   ├── test_edit.py
│   │   │   └── test_writer.py
│   │   ├── test_diff.py
│   │   ├── test_history.py
│   │   ├── test_paths.py
│   │   ├── test_permission.py
│   │   └── test_state.py
│   ├── input/               # 输入层测试
│   │   ├── __init__.py
│   │   └── test_wezterm_input.py
│   ├── plugins/             # 插件系统测试
│   │   ├── __init__.py
│   │   ├── test_plugin_host.py
│   │   ├── test_plugin_loader.py
│   │   ├── test_process_plugin.py
│   │   └── test_state_check.py
│   ├── process/             # 进程层测试
│   │   ├── __init__.py
│   │   ├── unix/
│   │   │   ├── __init__.py
│   │   │   └── test_pgid_tracker.py
│   │   ├── windows/
│   │   │   ├── __init__.py
│   │   │   ├── test_gui_monitor.py
│   │   │   └── test_job.py
│   │   ├── test_base.py
│   │   └── test_win32_error.py
│   ├── protocol/            # 协议层测试
│   │   ├── __init__.py
│   │   ├── test_ansi.py
│   │   ├── test_encoding.py
│   │   ├── test_envelope.py
│   │   └── test_message.py
│   ├── pty/                 # PTY 层测试
│   │   ├── __init__.py
│   │   ├── unix/
│   │   │   └── __init__.py
│   │   ├── windows/
│   │   │   └── __init__.py
│   │   ├── test_base.py
│   │   └── test_factory.py
│   ├── sandbox/             # 沙箱层测试
│   │   ├── __init__.py
│   │   ├── test_config.py
│   │   ├── test_sandbox_manager.py
│   │   ├── test_sandbox_pty.py
│   │   └── test_sandbox_tracker.py
│   ├── session/             # 会话层测试
│   │   ├── __init__.py
│   │   ├── encoding/
│   │   │   ├── __init__.py
│   │   │   ├── test_codec.py
│   │   │   └── test_detector.py
│   │   ├── output/
│   │   │   ├── __init__.py
│   │   │   ├── test_buffer.py
│   │   │   ├── test_events.py
│   │   │   └── test_trigger.py
│   │   ├── process/
│   │   │   ├── __init__.py
│   │   │   ├── test_gui.py
│   │   │   ├── test_info.py
│   │   │   └── test_monitor.py
│   │   ├── test_manager.py
│   │   ├── test_mouse.py
│   │   ├── test_screen.py
│   │   └── test_shm_utils.py
│   ├── shared/              # 跨侧共享测试
│   │   ├── test_config.py
│   │   ├── test_config_refactor.py
│   │   └── test_shm_utils.py
│   ├── terminal/            # 终端层测试
│   │   ├── __init__.py
│   │   └── test_backends.py
│   └── web/                 # Web 层测试
│       ├── __init__.py
│       ├── test_settings_controller.py
│       ├── test_settings_schema.py
│       └── test_ws_handler.py

├── integration/             # ═══ 集成测试（多模块协作） ═══
│   ├── __init__.py
│   ├── auth/
│   │   └── __init__.py
│   ├── test_debug_subprocess.py
│   ├── test_mouse.py
│   ├── test_sandbox.py
│   ├── test_session.py
│   ├── test_single_instance.py
│   ├── test_terminal_size_integration.py
│   └── test_wezterm_pty.py

├── e2e/                     # ═══ 端到端测试 ═══
│   ├── __init__.py
│   ├── test_keygen_e2e.py
│   ├── test_basic_password_e2e.py  # basic 密码认证 e2e
│   ├── test_plugins_e2e.py
│   ├── test_pubkey_auth_e2e.py
│   ├── test_resize_cursor_e2e.py
│   ├── test_resize_cursor_sync.py
│   ├── test_sandbox_conpty_e2e.py
│   ├── test_tls_auth_e2e.py
│   ├── test_vnc_job.py
│   ├── test_vnc_job_kill.py
│   └── test_vnc_proxy.py

└── web/                     # ═══ Web 界面测试 ═══
    ├── __init__.py
    └── test_web.py
```

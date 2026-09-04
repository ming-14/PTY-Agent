- cl，cmake位置自己找，可能不在系统变量
- 文档：
	- 代码变更时及时更新文档`AGENTS.md`
- 遵守C++语言规范，**遵守项目架构**
- 构建使用本仓库的`build.py`

## 变更记录

### WGC 连续捕获 x86 access violation 修复（api.cpp / capture_session.cpp / wgc_capture.cpp）
- 根因：`fs_stop_continuous` 先 delete ctx（capture 线程回调所引用的 ContinuousCaptureCtx）再停会话，capture 线程仍在跑时解引用已释放内存
- `fs_stop_continuous` / `fs_destroy_session`：改为先 `session->stop()`（join capture 线程）再删 ctx
- `CaptureSession::stop()`：先 `request_stop()` WGC 会话再 join capture 线程，避免 capture 线程持锁等待导致 STA 线程无法清理
- `WGCCapture` 新增 `request_stop()`；`capture_from_session` 循环检查 `session_stop_requested_` 快速退出释放锁
- `test_capture.py`：移除 x86 跳过（真正的修复已生效）

### CI 配置（.github/workflows/ci.yml）
- 矩阵构建 x64 / x86 / arm64（windows-latest，`build.py --arch`）
- x64、x86 运行 pytest（x86 用 32 位 Python）；ARM64 仅交叉编译（GH Actions 无 ARM64 Windows runner）
- 推送 `v*` 标签自动创建 Release，附带三架构 DLL（`gh release create`）

### 全架构支持（x64 / x86 / ARM64）
- `CMakeLists.txt`：按 MSVC_ARCHITECTURE_ID 条件编译，ARM64 跳过 `/arch:AVX2`
- `dxgi_capture.cpp`：`convert_r8g8b8a8_to_bgra()` 增加 ARM64 NEON 路径（`vqtbl1q_u8`），x86/x64 保留 AVX2
- `build.py`：新增 `--arch {x64,x86,arm64}` 参数，DLL 按架构安装到 `fastscreencore/<arch>/`
- `fastscreencore/_core.py`：`_find_dll()` 通过 `platform.machine()` 自动选择对应架构子目录

### COM 跨线程崩溃修复（image_encoder.cpp）
- `ImageEncoder::init()` 不再调用 `ensure_factory()` 创建 WIC factory
  （factory 延迟到第一次 `ensure_factory()` 时由调用线程创建，避免跨线程 COM 对象悬空）
- `ensure_factory()` 每次无条件调用 `CoInitializeEx(nullptr, COINIT_MULTITHREADED)`，
  确保当前线程 COM 公寓已初始化（MTA），其他线程可安全使用 MTA WIC factory

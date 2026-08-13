# win-sandbox 故障排查

常见问题定位与解决指南。涵盖已知问题、症状-原因对照、日志分析方法与调试工具使用。

> 对应 Phase 7 任务 T7.11。使用教程见 [USER_GUIDE.md](USER_GUIDE.md)，部署见 [DEPLOYMENT.md](DEPLOYMENT.md)。

---

## 1. 快速定位表

| 症状 | 可能原因 | 章节 |
|------|---------|------|
| `sandbox pipe closed before delivering expected message` | 服务端退出/崩溃；或 Shutdown 消息竞态 | §2.1 |
| 连接管道超时 | 管道名冲突、权限不足、sandbox 提前退出 | §2.2 |
| `FileNotFoundError: sandbox.exe not found` | Python 包未正确打包 | §2.3 |
| 无 BehaviorLog 事件 | 非管理员降级模式 | §2.4 |
| 网络无法连接 | 非管理员 WFP 降级 / 代理环境变量 / allowlist 规则 | §2.5 |
| `AccessDenied` 事件异常 | Low IL 全盘只读语义（写入拒绝属预期） | §2.6 |
| 沙箱进程崩溃（rc=0xC0000005） | 已知 bug 已修复，见 §3 | §3 |
| e2e 用旧二进制 | `build/bin/sandbox.exe` 未更新 | §4.3 |

---

## 2. 常见问题

### 2.1 `sandbox pipe closed before delivering expected message`

**现象**：调用 `wait_exit()` 抛 `SandboxProcessError`。

**原因分析**：
1. **沙箱进程崩溃**（最严重）——检查 `rc`：
   - 崩溃（rc=0xC0000005 等）：查看 §3 已知问题
2. **Shutdown 消息竞态**（正常退出但消息丢失）：
   - 服务端 `SendEvent(ShutdownComplete)` 入队后立即退出进程，命名管道未读缓冲
     可能被 broken pipe 丢弃
   - `wait_exit` 已对此降级处理（2026-08-05 修复），收到 pipe closed 会继续等进程退出
   - 若仍抛错，说明是其他路径：检查沙箱是否真的退出了

**排查步骤**：
```python
try:
    client.send_shutdown()
    code = client.wait_exit(timeout=5.0)
except SandboxError as e:
    # 检查进程退出码区分崩溃 vs 正常
    rc = client._proc.returncode if client._proc else None
    print(f"err={e} rc={rc}")
```

- `rc == 0`：正常退出，消息竞态已降级处理，非错误
- `rc != 0`（如 0xC0000005）：崩溃，见 §3

### 2.2 连接管道超时

**现象**：`start()` / `connect_only()` 抛 `SandboxTimeoutError`。

**排查**：
1. 管道名是否冲突：`\\.\pipe\win-sandbox-<pid>` 应每次唯一（用 `_make_pipe_name` 模式）
2. sandbox 是否提前退出：`start()` 时进程退出会抛 `SandboxProcessError`
3. 权限：管道 DACL 仅授权当前用户，跨用户连接会被拒
4. 用 debug 级别看 sandbox 日志（见 §4.1）

### 2.3 `FileNotFoundError: sandbox.exe not found`

**原因**：wheel 打包时 `bin/sandbox.exe` 未注入，或源码目录没有 Release 构建。

**解决**：
```powershell
# 确保 Release 构建存在
cmake --build build --config Release
# 重新打包 wheel
python -m build python/
```

### 2.4 无 BehaviorLog 事件

**原因**：非管理员模式，ETW 降级为进程列表轮询，仅产生 ProcessStart/ProcessStop。

**验证**：检查 Ready 事件的 `capabilities.modules.etw`：
- `available: true` → 管理员模式，应有完整行为日志
- `available: false` → 降级模式，行为日志仅进程事件

### 2.5 网络无法连接

**可能原因**：
1. 非管理员运行且 `net_policy=allowlist`：WFP callout 不可用（`capabilities.network.available=false`），allowlist 降级为 unrestricted，**网络不会受限**——需要真实拦截请用管理员运行
2. `net_allowlist` 规则配置错误（`ip`/`port`/`protocol` 不匹配，`protocol` 6=TCP / 17=UDP / 0=任意）
3. 应用未走代理：SOCKS5 代理仅拦截识别 `ALL_PROXY`/`HTTP_PROXY`/`HTTPS_PROXY` 的程序，直接 socket 连接不受管控

**排查**：确认 Ready 事件 `capabilities.modules.network` 状态；allowlist 模式下查日志 `network blocked (native): ip=... port=...`（仅拦截事件会打日志）。

### 2.6 AccessDenied 事件异常

**现象**：进程被错误拒绝访问，或 AccessDenied 事件缺失。

**排查**：
1. **写入失败（全盘只读是预期行为）**：Low IL 子进程对任何目录（除可写区）写入被拒是**预期行为**（完整性级别 NO_WRITE_UP）——需要落盘请写入 `%TEMP%`（已重定向到可写区 `%LOCALAPPDATA%\win-sandbox\sessions\<os-pid>-<process_id>\writable`）
2. 读取宿主文件被拒：完整性级别不限制读，若仍被拒请检查文件 ACL（`low_il_token` 模块不受影响）
3. 工作目录无效：Low IL 进程无法以宿主 `%TEMP%` 下的目录为 cwd（权限不足，报"当前目录无效"）——请传完整可遍历路径或依赖默认工作目录（可写区）

---

## 3. 已知问题与已修复 bug

### 3.1 ✅ 已修复：Shutdown 崩溃（2026-08-05）

3 个 Shutdown 路径崩溃 bug 已修复（详见 `docs/memory/Lessons-Learned.md` #012/#013/#014）：

| Bug | 现象 | 根因 | 修复 |
|-----|------|------|------|
| ProcessEntry use-after-free | Shutdown 时 0xC0000005 崩溃 | 整值移动赋值按声明序释放，usecase 依赖成员先被释放 | 显式按依赖顺序 reset |
| StopWallClockTimer 双 join | 析构崩溃 | wait 线程与析构并发 join 同一 std::thread | `std::call_once` 保护 |
| ShutdownComplete 丢失 | wait_exit 偶发报错 | 服务端退出时管道缓冲数据丢失 | 客户端 wait_exit 降级容忍 |

### 3.2 ✅ 已修复：Result::Value() 空值解引用（Lessons #009）

`Result<T>::Value()` 在 Err 状态下触发 `std::optional` 空值解引用崩溃。已改为抛 `std::logic_error`。

### 3.3 ✅ 已修复：FrameCodec 分片重组 use-after-free（Lessons #010）

`HandleDecodedMessage` 先 `erase` 再访问悬垂引用。已先拷贝数据再 erase。

### 3.4 ✅ 已修复：Shutdown race condition（Lessons #011）

`Shutdown()` 在写完 write_queue 前取消 pending I/O。已新增 flush 步骤。

### 3.5 已知限制（平台性）

| 限制 | 说明 |
|------|------|
| Low IL 单向墙 | 子进程不能写宿主目录（全盘只读）；但读不受限，可读宿主常规文件（敏感文件需 ACL 另行限制） |
| 可写区唯一 | 写入只能落 `%TEMP%`（可写区）；持久化需从可写区取回 |
| ETW 内核 session 需管理员 | 普通用户降级为进程列表轮询 |
| WFP 需管理员 | `net_policy=allowlist` 普通用户降级为 unrestricted（不拦截） |

---

## 4. 日志与调试

### 4.1 获取日志

```python
client = SandboxClient(exe_path=..., pipe_name=..., log_level="debug")
```

- `log_level=debug` 时，sandbox stderr 写入 `sandbox_stderr_<pid>.log`（当前目录）
- 配置文件 `logging.dir` 指定持久化日志目录（默认 `%LOCALAPPDATA%\win-sandbox\logs`）

### 4.2 关键日志关键字

| 关键字 | 含义 |
|--------|------|
| `permission mode: StandardUser` | 权限模式 |
| `capability [...] = degraded` | 降级能力 + 原因 |
| `ShutdownComplete sent` | Shutdown 响应已入队 |
| `StartProcessUseCase destructed while process still running` | 进程运行中析构（Shutdown 路径） |
| `win-sandbox exiting` | 正常退出 |

### 4.3 e2e 测试用旧二进制

测试优先使用 `build/bin/sandbox.exe`。本地重新构建后需同步：

```powershell
copy build\bin\Release\sandbox.exe build\bin\sandbox.exe
```

否则新修复不生效、旧 bug 复现。

### 4.4 崩溃调试

需要管理员/开发环境时，可用调试器附加：

```powershell
# cdb（Windows SDK Debugging Tools）
cdb -o -g -G -c ".childdbg 1; g" python.exe tests/e2e/test_async_client.py
```

崩溃时 cdb 会停在异常点，执行 `kb` 查看调用栈。

---

## 5. 诊断辅助脚本

仓库 `tests/e2e/` 下有一批 `debug_*.py` / `diag_*.py` 脚本，用于隔离调试
（ETW 回调、分片、管道性能等）。排查相关问题时可直接运行：
- `debug_etw_*.py` — ETW 事件排查
- `debug_frag*.py` — 消息分片排查
- `debug_minimal_write.py` — 最小写入复现

---

## 6. 提交 bug 报告时应包含

1. 完整异常堆栈（Python traceback）
2. sandbox 退出码（区分崩溃 vs 正常）
3. `log_level=debug` 的 sandbox stderr 日志
4. 环境：管理员/普通用户、Windows 版本、Python 版本
5. 最小复现脚本

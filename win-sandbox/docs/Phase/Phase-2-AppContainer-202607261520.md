# Phase 2：AppContainer 隔离（M2）

## 1. 文档信息

| 项目 | 内容 |
|------|------|
| Phase | 2 |
| 对应里程碑 | M2 |
| 创建日期 | 2026-07-26 |
| 前置 Phase | Phase 1 |

---

## 2. 目标

在 Phase 1 的 Job 资源限制基础上，叠加 AppContainer 访问控制。结束时被隔离进程运行在 AppContainer 沙箱中，默认拒所有文件/注册表/网络访问，仅能访问显式授予的白名单路径。

---

## 3. 范围

### In Scope
- `IAppContainer` 接口 + `AppContainerImpl`
- AppContainer Profile 创建/删除（`CreateAppContainerProfile` / `DeleteAppContainerProfile`）
- AppContainer SID 生成（`DeriveAppContainerSidFromAppContainerName`）
- 受限 Token 创建（`CreateAppContainerToken`）
- Capability 授予（`internetClient` 等）
- 路径白名单授权（`GrantSidAccess`）
- `ProcessLauncher` 升级为 `CreateProcessAsUserW` + AppContainer Token
- `IsolationPolicy` 实体
- `EnforcePolicyUseCase`
- `AccessDenied` 事件（AppContainer 拒绝访问时通过 ETW 或日志捕获）
- 配置项：`appcontainer.enabled`、`appcontainer.capabilities`、`filesystem.read_paths`、`filesystem.write_paths`、`filesystem.execute_paths`

### Out of Scope
- 文件系统写重定向（Phase 4）
- 网络拦截策略（Phase 5）
- 行为监控 ETW 全量订阅（Phase 6）
- 自适应权限降级（Phase 7，本阶段管理员模式运行）

---

## 4. 前置依赖

- Phase 1 全部交付物
- 管理员权限运行（Phase 7 才做降级）
- userenv.lib（AppContainer API）

---

## 5. 任务清单

### T2.1 IAppContainer 接口与实体
- `src/core/ports/IAppContainer.hpp`
- `src/core/entities/AppContainerProfile.hpp`：SID、moniker、profile_path、capabilities
- `src/core/entities/IsolationPolicy.hpp`：fs_mode、net_policy、capabilities、path_rules

**验收**：接口定义完成，无 Win32 依赖。

### T2.2 AppContainerImpl
- `src/infra/appcontainer/AppContainerImpl.hpp/cpp`
- `CreateAppContainerProfile`：生成唯一 moniker（`win-sandbox-<instance_id>`）
- `DeriveAppContainerSidFromAppContainerName`：获取 SID
- `CreateAppContainerToken`：生成受限 Token
- `DeleteAppContainerProfile`：清理
- Capability 列表映射（字符串 → `SID_AND_ATTRIBUTES`）

**验收**：Profile 创建成功，SID 唯一，Token 句柄有效。

### T2.3 路径授权
- `src/infra/appcontainer/PathGrantor.hpp/cpp`
- `GrantSidAccess`（路径 → AppContainer SID 授予 Read/Write/Execute）
- 路径规范化（展开 `%ENV%`、转 NT 路径）
- glob 支持（`C:\Tools\*.exe`）→ 展开为具体路径列表

**验收**：授予 `C:\Tools` Read 后，AppContainer 内进程可读取该目录。

### T2.4 ProcessLauncher 升级
- 修改 `ProcessLauncherImpl`：
  - `CreateProcessAsUserW`（使用 AppContainer Token）
  - `STARTUPINFOEXW` + `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`
  - 设置 AppContainer SID 与 Capability 列表
- 创建后仍 `AssignProcessToJobObject`

**验收**：被隔离进程的 Token 验证为 AppContainer 令牌（`GetTokenInformation(TokenAppContainerSid)`）。

### T2.5 EnforcePolicyUseCase
- `src/core/usecases/EnforcePolicyUseCase.hpp/cpp`
- 输入：`IsolationPolicy`（含 path_rules + capabilities）、AppContainer SID
- 流程：
  - 遍历 `IsolationPolicy.path_rules`
  - 对每条 `PathRule{path, access}` 调用 `IPathGrantor::Grant(sid, path, access)`
  - `access` 是 `PathAccess` 位标志（Read | Write | Execute），实现层映射到 `FILE_GENERIC_READ/WRITE/EXECUTE`
  - `fs_mode=Disabled` 时直接返回 Ok（Phase 1 兼容）
- 在 `StartProcessUseCase::Execute` 中调用（Launch 前完成路径授权）
- 集成方式：`StartProcessUseCase` Phase 2 构造函数注入 `IAppContainer*` + `IPathGrantor*`，
  内部组合 `EnforcePolicyUseCase` 子用例；`StartProcessRequest.isolation_policy.fs_mode=DefaultDeny`
  时自动走 AppContainer 路径（CreateProfile + CreateToken + EnforcePolicy + Launch）

**API 选型说明**（T2.3 决策 1 已确认）：
Phase 2 文档原文写 "GrantSidAccess（userenv.dll）"，但 dumpbin 实测 userenv.dll 不导出该 API。
改用 `IPathGrantor::Grant` → `PathGrantorImpl` 内部调用标准 ACL 三件套
（`GetNamedSecurityInfoW` + `SetEntriesInAclW` + `SetNamedSecurityInfoW`），追加 ACE 语义。

**验收**：配置 read_paths 后，被隔离进程可读；未配置的路径访问失败。

### T2.6 AccessDenied 事件捕获
- 临时方案：通过沙箱自身日志或 `GetLastError` 检测
- Phase 6 升级为 ETW 订阅
- 发 `AccessDenied` 事件（path、operation、required_capability）

**实施状态**：✅ 已落地（stderr 解析方案）

**实施方案（stderr 解析）**：
AppContainer 拒绝访问时，cmd.exe 会向 stderr 输出固定关键字。在 `StartProcessUseCase::OnOutput` 中扫描 stderr chunk，命中关键字则通过 `IEventEmitter` 发送 `AccessDenied` 事件。

- **关键字识别**（`StartProcessUseCase::ContainsAccessDeniedKeyword`，公开静态方法，无状态纯函数）：
  - 中文 `"拒绝访问"`：同时检查 UTF-8 与 GBK(CP936) 编码
    - UTF-8: `E6 8B 92 E7 BB 9D E8 AE BF E9 97 AE`
    - GBK:   `BE DC BE F8 B7 C3 CE CA`
  - 英文 `"Access is denied"`：大小写不敏感匹配（转小写后子串搜索，避免引入 ICU/正则依赖）
  - 单 chunk 多关键字返回 true 一次（chunk 级别去重，由调用方控制）

- **事件 payload schema**（`StartProcessUseCase::EmitAccessDenied`）：
  - `pid`：进程 PID
  - `stream`：固定 `"stderr"`
  - `data`：命中的原始 stderr 片段
  - `path`：空字符串（stderr 无法准确提取，Phase 6 ETW 填实）
  - `operation`：`"unknown"`
  - `required_capability`：空字符串

- **GBK 编码踩坑记录**：
  早期代码将 `"绝"` 字 GBK 编码误写为 `0xBE 0xEC`（实际是 `"眷"`），导致 cmd.exe 中文系统默认 CP936 输出的 `"拒绝访问。"` 无法命中。Python `text.encode('gbk')` 验证正确字节为 `0xBE 0xF8`。修正后 B14/B16 集成测试全绿。

**验收**：访问白名单外路径时，Python 收到 `AccessDenied` 事件。

**验证**：`tests/unit/verify_t26.cpp` 共 27 项测试全绿
- A1-A14 单元测试：覆盖中英文关键字命中/不命中、空串、短串、长串、路径+关键字、Unix 风格误报排除
- B14 集成测试：AppContainer 访问未授权文件 → 收到 1 个 AccessDenied 事件，payload 字段齐全
- B15 集成测试：AppContainer 访问授权文件 → 不发 AccessDenied 事件，进程 exit_code=0
- B16 集成测试：AppContainer 多次访问未授权文件 → 多次 AccessDenied 事件（chunk 级别去重）

### T2.7 配置扩展
- `ConfigLoader` 扩展：
  - `appcontainer` 段
  - `filesystem.read_paths` / `write_paths` / `execute_paths`
- schema 校验

**实施状态**：✅ 已落地（含 IPC payload 同步扩展）

**配置文件 schema**（ConfigLoader 解析 → `SandboxConfig.default_isolation_policy`）：

```json
{
    "appcontainer": {
        "enabled": true,                    // → fs_mode=DefaultDeny；false/缺省 → Disabled
        "capabilities": ["internetClient"]  // 字符串数组，直接复制到 IsolationPolicy.capabilities
    },
    "filesystem": {
        "read_paths":    ["C:\\Tools", "%TEMP%\\sandbox"],  // → PathRule{Read}
        "write_paths":   ["C:\\Data"],                      // → PathRule{Write}
        "execute_paths": ["C:\\Bin"]                        // → PathRule{Execute}
    }
}
```

**合并语义**：
- 同一路径在多个列表出现 → 合并访问权限（Read|Write|Execute），用 `std::map<string, PathAccess>` 去重
- 路径字段都做 `ExpandEnvironmentStringsW` 展开（与 `logging.dir` 同模式，T2.3 要求"展开 `%ENV%`"）
- `appcontainer.enabled` 控制 `fs_mode`；`filesystem` 段独立配置规则，不会单独开启 AppContainer
- 严格模式：未知字段拒绝（`appcontainer.xxx` / `filesystem.xxx` → `ConfigSchemaValidationFailed`）
- 类型校验：`capabilities` 必须是字符串数组、`*_paths` 元素必须是字符串

**IPC payload schema**（`StartProcessPayloadParser` 解析，覆盖 `default_isolation_policy`）：

```json
{
    "command_line": "...",
    "isolation_policy": {
        "fs_mode": "default_deny",          // 或 "disabled"
        "capabilities": ["internetClient"],
        "path_rules": [
            {"path": "C:\\Tools", "access": ["read", "write", "execute"]}
        ]
    }
}
```

- payload 未含 `isolation_policy` → 用 `config.default_isolation_policy` 兜底（与 `quota` 段同模式）
- payload 含 `isolation_policy` → 完全覆盖兜底值（不与 default 字段级合并，与 `quota` 段语义一致）
- IPC 不展开环境变量（调用方应传完整绝对路径）
- `fs_mode` 取值：`"disabled"` / `"default_deny"`（其他 → `IpcSchemaValidationFailed`）
- `access` 数组元素：`"read"` / `"write"` / `"execute"`（其他 → `IpcSchemaValidationFailed`）

**架构改动**：
- `ParseStartProcessPayload` 从 `main.cpp` 抽出到独立文件 `src/adapters/StartProcessPayloadParser.hpp/.cpp`，便于单元测试覆盖 IPC payload schema（按干净架构原则）
- `main.cpp` include 头文件并调用新位置；`src/CMakeLists.txt` 新增 StartProcessPayloadParser.cpp 编译入口

**验收**：配置文件加载成功，路径展开正确。

**验证**：`tests/unit/verify_t27.cpp` 共 26 个测试场景（74 项断言）全绿
- T1-T16 ConfigLoader 测试：appcontainer.enabled 映射 fs_mode / capabilities 解析 / read_write_execute_paths 合并 / %TEMP% 环境变量展开 / 严格 schema 拒绝未知字段 / 类型校验 / 空配置 / 完整配置
- T17-T26 IPC payload 测试：fs_mode=default_deny|disabled / 兜底 / 非法值 / capabilities / path_rules access 合并 / isolation_policy 非对象 / 缺 command_line / 完整 payload（含 quota 覆盖）

### T2.8 e2e 测试
- `tests/e2e/test_appcontainer.py`
- 用例 1：访问 `C:\Windows\System32\config\SAM` 失败
- 用例 2：授予 `%TEMP%\winsandbox_t28` Read 后可读取
- 用例 3：未授予写权限时写入失败
- 用例 4：授予写权限后写入成功
- 用例 5：Profile 清理（Shutdown 后注册表 Storage 子键回到基线）

**实施状态**：✅ 已落地（5/5 PASS）

**实施方案**：
- 测试目录：`%TEMP%\winsandbox_t28`（每次清空重建，避免残留干扰）
- Profile 清理验证：通过 `winreg` 枚举 `HKCU\Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppContainer\Storage` 下 `win-sandbox-<pid>-<unix_ms>` 格式子键，对比基线/运行中/Shutdown 后三个时点的子键集合
- 测试执行不依赖 pytest，单跑 `python tests/e2e/test_appcontainer.py <N>` 可执行指定用例
- sandbox.exe 路径优先 `build/bin/Debug/sandbox.exe`，回退 `build/bin/Release/sandbox.exe`

**踩坑记录（cmd 重定向语法 bug）**：
- 初版 Test 3/4 命令为 `cmd.exe /c echo test^>"<file>"`，期望 `^>` 实现重定向写文件
- 实际：cmd 把 `^>` 解释为字面 `>` 字符，echo 输出字符串 `test>file` 到 stdout，不写文件
  - Test 3 表现：未触发 WriteFile，无 AccessDenied 事件
  - Test 4 表现：exit_code=0 但文件内容未被覆盖
- 修复：改为 `cmd.exe /c (echo test)>"<file>"`，用括号包住 echo 明确命令边界，`>` 直接作为重定向操作符
- 经验：Python 字符串里 `>` 不需要任何转义；`^` 是 cmd 交互式解释器的转义符，在 `cmd /c` 后仍然生效，会把特殊字符变成字面字符

**AccessDenied 事件 GBK→UTF-8 编码归一化**：
- 中文 Windows cmd.exe 默认输出 GBK (CP936) 编码，"拒绝访问。" 字节序列 `BE DC BE F8 B7 C3 CE CA`
- nlohmann::json 序列化 std::string 时要求合法 UTF-8，GBK 字节会触发 `type_error.316`
- 修复（`StartProcessUseCase.cpp` 新增 `NormalizeToUtf8` 函数）：
  - `IsValidUtf8`：用 `MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS)` 严格判定
  - `AcpToUtf8`：ACP(GBK) → UTF-16 → UTF-8 两步转换
  - `NormalizeToUtf8`：合法 UTF-8 原样返回，否则当 ACP 转；转换失败原样返回让 JSON 层报错便于诊断
- 调用点：`EmitProcessOutput` / `EmitAccessDenied` 把 event.data 放入 IPC payload 前调用

**事件序列验证**（每个用例均完整捕获）：
- Test 1（SAM 拒绝）：`ProcessStarted` → `ProcessOutput(stderr, "拒绝访问。")` → `AccessDenied` → `ProcessExited(exit_code=1)`
- Test 2（read 授权）：`ProcessStarted` → `ProcessOutput(stdout, "hello t28")` → `ProcessExited(exit_code=0)`（无 AccessDenied）
- Test 3（write 拒绝）：`ProcessStarted` → `AccessDenied` → `ProcessExited(exit_code=1)`
- Test 4（write 授权）：`ProcessStarted` → `ProcessExited(exit_code=0)`，文件内容被覆盖为 `"test\n"`（无 AccessDenied）
- Test 5（Profile 清理）：基线 0 moniker → StartProcess 后 1 新 moniker → Shutdown 后回到 0 leftover

**验收**：5 子用例全绿（运行 `python tests/e2e/test_appcontainer.py` 输出 `Result: 5/5 PASS`）。

---

## 6. 风险

| 风险 | 缓解 |
|------|------|
| AppContainer Profile 残留 | Shutdown 必调 DeleteAppContainerProfile；启动时扫描清理 |
| GrantSidAccess 路径格式错误 | 严格 NT 路径转换（`\??\C:\...`） |
| CreateProcessAsUserW 需要特权 | 本阶段管理员运行；Phase 7 做降级 |
| AppContainer 内进程无法启动（缺依赖 DLL） | 授予 System32/SysWOW64 Read + Execute |
| Profile 创建慢（首次） | 实测延迟，必要时复用 |

---

## 7. 退出条件

- [x] AppContainer Profile 创建/删除成功（T2.2 verify_t22 全绿 + T2.8 Test 5 验证清理）
- [x] 被隔离进程运行在 AppContainer Token 下（T2.4 verify_t24 验证 TokenAppContainerSid 匹配）
- [x] 白名单外路径访问被拒（AccessDenied）（T2.6 verify_t26 + T2.8 Test 1/3 验证）
- [x] 白名单内路径访问成功（T2.5 verify_t25 + T2.8 Test 2/4 验证）
- [x] Shutdown 后 Profile 清理（T2.8 Test 5 验证注册表 Storage 子键回到基线）
- [x] e2e 5 子用例全绿（T2.8 `python tests/e2e/test_appcontainer.py` 输出 `5/5 PASS`）

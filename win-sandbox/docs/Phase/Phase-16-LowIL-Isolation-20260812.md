# Phase 16: Low IL Token + Job 纯用户态隔离

**Phase 编号**: 16
**Phase 名称**: RestrictedToken + Low Integrity + Job 纯用户态隔离（替换 AppContainer 链）
**创建日期**: 2026-08-12
**状态**: ✅ 已完成（T1–T7 全部验收通过）
**上游依赖**: Phase 15（已完成，wheel + 全量回归）
**下游影响**: PTY-Agent `src/config/sandbox.toml` 配置语义变更、e2e 套件更新

---

## 1. 背景与动机

### 1.1 调查结论（已完成，实测定案）

沙箱"全盘可读可执行 + 网络全通 + 不可写"的需求，AppContainer 路线不可达：

1. **AppContainer 的父链死结**：AppContainer 进程访问 `C:\` 下任意路径，需要根目录及每个祖先目录的遍历许可，且受"AC SID / ALL APPLICATION PACKAGES 必须匹配祖先目录 ACE"约束。实测 `C:\` 根目录无 AAP-AC ACE（仅 Users/SYSTEM/Admins），AppContainer 从根即断。且 `C:\` ACL 非管理员无权修改（父链授权是管理员才能做的一次性操作）。
2. **非 win-sandbox 缺陷**：对照实验（finaltest.py）用系统 UWP（StartMenuExperienceHost）与 win-sandbox 的同构 token 启动 cmd，读写行为逐字节一致（拒绝访问 ×2 + TEMP 可列）——win-sandbox 的 AppContainer 隔离语义与微软 UWP 完全相同。
3. **token 本身是真 AppContainer**：probe_ac2.py 用正确枚举值（`TokenIsAppContainer=29`、`TokenAppContainerSid=31`、`TokenCapabilities=30`、`TokenRestrictedSids=11`）实查：SID 与会话授权 SID 完全一致，cap_count=2 来自 `net_policy=unrestricted` 的 internetClient+internetClientServer 注入。旧 dump 工具枚举值错误（误用 6/28/29），已弃用。
4. **`%TEMP%` 重定向**：沙箱内 `%TEMP%` 已被系统重定向到 `Packages\<moniker>\AC\Temp`（AC SID 全权），与 cmd 真实 cwd 错位，造成"dir 拒绝"的表象。

### 1.2 新路线（本 Phase）

**Low Integrity Level 模型**（实验已实证，lowil_test.py）：

- 进程 token 降 IL 至 `Low(S-1-16-4096)`：`NO_WRITE_UP` 强制写任何 Medium(默认) 对象被拒——**全盘天然禁写**，不依赖任何 ACL 修改；
- 读不受 IL 限制（默认无 `NO_READ_UP`）：保留用户 SID，用户可读文件全部可读可执行；
- 可写区：给专用目录打 Low 标签（非管理员可用，probe_writearea.py 实测 `icacls /setintegritylevel LOW` 成功 + Low 进程实际写入 OK），沙箱 `%TEMP%` 重定向到它；
- 网络：Job 不管网络，复用现有 WFP（无驱动）；
- 进程树：复用现有 JobObjectImpl（KILL_ON_JOB_CLOSE）。

| 验证项 | 结果 |
|---|---|
| Low IL 进程读 `C:\Windows\...\hosts` | ✅ 全文可读 |
| Low IL 进程写桌面 | ❌ 拒绝访问 |
| Low IL 进程写 %TEMP% | ❌ 拒绝访问 |
| 对照（普通 token）写桌面 | ✅ 成功（证明测试有效） |
| 非管理员给目录打 Low 标签 + Low 进程写入 | ✅ 成功 |

### 1.3 已对齐的需求决策（用户确认）

| 决策点 | 结论 |
|---|---|
| 隔离实现 | 直接替换，删除 AppContainer 链，不留兼容接口 |
| 威胁模型 | 接受边界：HKCU 注册表可写、同用户进程可 kill、同用户窗口消息可发（此面仅驱动能解决） |
| 可写区 | 每会话（per-process）自动创建，打 Low 标签，%TEMP% 重定向，结束清理 |
| Job 资源限制 | 不限制，只管树 + KILL_ON_JOB_CLOSE |
| fs 配置 | 重构：移除 fs_mode，沙箱固有语义 = 全盘只读 + 可写区 |
| 剪贴板隔离 | 留配置开关，默认关 |

---

## 2. Phase 目标

### 2.1 总体目标

1. 新增 Low IL token 派生 + 可写区管理，替换 AppContainer 隔离链
2. 删除 IAppContainer / IPathGrantor / EnforcePolicyUseCase / FileSystemIsolator 全链
3. IsolationPolicy 配置重构（fs_mode/path_rules/capabilities 删除）
4. PTY-Agent sandbox.toml 语义同步重构
5. 测试全量更新（新增 lowil e2e，删除/重写 AC 相关测试）+ 回归

### 2.2 非目标

- 不做 minifilter 驱动（含注册表隔离/API 监控）
- 不做管理员一次性 ACL init（本方案已不需要）
- 不改网络链路（WFP + SOCKS5 allowlist 原样保留）
- 不改 Job 资源限制语义（仍无默认上限）

---

## 3. 功能需求

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
|----------|----------|--------|----------|
| FR-16.1 | ITokenIsolator 端口 + TokenIsolatorImpl：从当前进程 token 派生隔离 token（DuplicateTokenEx → SetTokenInformation IL=Low；**plain 单路径**，用户拍板） | P0 | ✅ 启动的进程 `TokenIsAppContainer=NO`、IL=`S-1-16-4096`、特权集=宿主镜像（test_lowil_isolation.py 1 项实测） |
| FR-16.2 | ProcessLauncherImpl 增加"指定 token"启动模式（LaunchRequest 增加 token 字段，替换 app_container_sid/capabilities 分支） | P0 | ✅ Launch 用隔离 token 走 CreateProcessAsUserW，ConPTY/管道逻辑不变（native_smoke 7/7） |
| FR-16.3 | IWriteArea 端口 + WriteAreaImpl：`%LOCALAPPDATA%\win-sandbox\sessions\<os-pid>-<process_id>\writable`，打 Low 标签（实测定案：SetNamedSecurityInfo(SI_LABEL) 非管理员可用 rc=0，首选即此路线），Teardown 递归删除 | P0 | ✅ 非管理员全流程可用；Low IL 子进程可写；Teardown 后目录不存在；失败日志 + StartupCleanup 兜底 |
| FR-16.4 | 环境注入：沙箱进程 TEMP/TMP 指向可写区 | P0 | ✅ 沙箱内 `echo %TEMP%` 指向可写区且可写（test_lowil_isolation.py 4 项实测） |
| FR-16.5 | IsolationPolicy 重构：删除 fs_mode/capabilities/path_rules/fs_config；新增 clipboard_isolate | P0 | ✅ 编译通过；bindings 解析同步（verify_t27 70/70） |
| FR-16.6 | NativeSandboxedProcess::Execute 重构：删 PrepareAppContainer 与 fs_isolator 分支，改为 token 派生 + 可写区创建 + TEMP 注入；Close 增加 WriteArea Teardown | P0 | ✅ 启动链无 AC 痕迹；关闭清理可写区（test_lowil 7 项 + test_cleanup 2/2） |
| FR-16.7 | NativeSandboxInstance 装配更新：删 AppContainerImpl/PathGrantorImpl/FileSystemIsolatorImpl，加 TokenIsolatorImpl/WriteAreaImpl | P0 | ✅ 沙箱进程正常启动（native_smoke 7/7） |
| FR-16.8 | Job 剪贴板隔离接线：clipboard_isolate=true → SetUiLimits(true)（复用现有实现） | P1 | ✅ 开启后沙箱内剪贴板读写受限，默认 false（verify_t27 配置项 + JobObjectImpl 接线） |
| FR-16.9 | 删除旧链：IAppContainer/IPathGrantor/AppContainerProfile/EnforcePolicyUseCase/CapabilityMapping/IFileSystemIsolator/PathRuleEngine/PermissionDetector 及对应 infra 实现 | P0 | ✅ 引用点全部更新，无残留；`rg -i "appcontainer|path_rule|fs_mode" src/` 无旧符号（PermissionDetector 保留：重构为能力检测） |
| FR-16.10 | PTY-Agent 配置重构：sandbox.toml [isolation] 段（net_policy + clipboard_isolate），sandbox.py/manager.py 同步 | P0 | ✅ 沙箱会话可用（integration 9/9 + e2e 1/1）；配置注释说明新语义 |
| FR-16.11 | 测试：新增 test_lowil_isolation.py（读系统文件 OK / 写桌面拒 / 写可写区 OK / TEMP 重定向 / IL 断言）；删除或重写 test_appcontainer.py、test_permission_matrix.py、test_filesystem.py、test_cleanup.py；相关 unit 测试更新 | P0 | ✅ e2e 全量 21/21 + ctest 6/6 全绿（verify_t27 重写 70/70） |
| FR-16.12 | 文档更新：ARCHITECTURE / API_REFERENCE / USER_GUIDE 移除 AppContainer 描述，新增 Low IL 模型；记忆文档记录转型踩坑 | P1 | ✅ 文档无 AC 残留描述（`rg -i appcontainer docs/` 仅 archive/历史 phase/历史 design 快照，与 Lessons-Learned 007/008/019 的 Phase 16 过时标注；主文档仅保留"Phase 16 移除"说明） |

---

## 4. 技术设计

### 4.1 隔离模型总览

```
NativeSandboxedProcess::Execute
  ├─ TokenIsolator::Prepare()
  │    OpenProcessToken(当前进程, QUERY|DUPLICATE|ASSIGN_PRIMARY|ADJUST_DEFAULT)
  │    → DuplicateTokenEx(MAXIMUM_ALLOWED, 2, TokenPrimary)      // plain：不 CreateRestrictedToken
  │    → SetTokenInformation(TokenIntegrityLevel, S-1-16-4096)   // （决策见 4.2）
  │    → 返回 primary token（void*）
  ├─ WriteArea::Create()
  │    %LOCALAPPDATA%\win-sandbox\sessions\<os-pid>-<process_id>\writable
  │    → 打 Low 标签 + 当前用户 (OI)(CI)F
  ├─ env 注入: TEMP/TMP = write_area
  ├─ ProcessLauncher::Launch(req.token)
  │    CreateProcessAsUserW(token, ...)（替代原 SECURITY_CAPABILITIES 分支）
  ├─ JobObject::AssignProcess（不变）
  └─ Close(): WriteArea::Teardown() + 既有清理
```

关键语义：
- **禁写**：IL=Low 对默认 Medium 对象强制 NO_WRITE_UP（token 侧策略，默认开启），无需改任何 ACL；
- **可读**：默认无 NO_READ_UP，保持用户 SID/组 → DACL 匹配不变；
- **可执行**：执行 = 读 + 映射，Low 进程执行 Medium exe 已验证可用（cmd.exe 本身即 Medium）；
- **无需管理员**：token 派生（SeImpersonatePrivilege 默认具备）、打标签（SetNamedSecurityInfo(SI_LABEL) 零特权 rc=0，已实测）均零特权；
- **单向墙**：完整性强制只防"低写高"；宿主 Medium 写可写区放行（高写低 + Medium 创建的子文件无标签=Medium），为主机侧清理所需，威胁模型接受。
- **特权继承（plain 单路径）**：隔离 token 不删特权，特权集=宿主镜像（非管理员宿主仅 5 个无害特权：ChangeNotify 启用 + Shutdown/Undock/WorkingSet/TimeZone 禁用）。威胁模型边界 = "沙箱进程能力 ≤ 同用户宿主能力全集"（与 HKCU 可写、同用户 kill 同级，已对齐）。

### 4.2 TokenIsolatorImpl（新增 `src/infra/token/TokenIsolatorImpl.*`）

- 端口：`src/core/ports/ITokenIsolator.hpp`（`Result<void*> Prepare(const IsolationPolicy&)`，返回实现层拥有的 primary token 句柄；`void Close()`）
- **plain 单路径（2026-08-12 用户拍板）**：不使用 CreateRestrictedToken。实测定案：restricted token（DISABLE_MAX_PRIVILEGE 产物）由非管理员宿主 CreateProcessAsUserW 启动**必然失败 err=1314**（MSDN：restricted token 需要调用方 SE_ASSIGNPRIMARYTOKEN_NAME）；CreateProcessWithTokenW（需 SE_IMPERSONATE + SE_INCREASE_QUOTA，非管理员全 1314）与 NtCreateUserProcess（STATUS_ACCESS_VIOLATION，ntdll 手拼协议）均不可行（replay_cpa.py / replay_cpa2.py / replay_cpa3.py 三实验）。"特权 0"与"非管理员可用"硬冲突 → 取 plain：特权继承宿主（非管理员=5 个无害特权），隔离核心仍全部来自 IL 完整性强制
- 完整 IL 标签由 SetTokenInformation(TokenIntegrityLevel) 设置（与实验 lowil_test.py 一致）
- 生命周期：NativeProcessEntry 持有，usecase Close 时释放

### 4.3 WriteAreaImpl（新增 `src/infra/writearea/WriteAreaImpl.*`）

- 端口：`src/core/ports/IWriteArea.hpp`（`Result<void> Create(uint32_t process_id)`、`std::string Path() const`、`Result<void> Teardown()`）
- 打标签：`SetNamedSecurityInfo(path, SE_FILE_OBJECT, LABEL_SECURITY_INFORMATION)`，Label ACE = SYSTEM_MANDATORY_LABEL_ACE_TYPE(Mask=NO_WRITE_UP) + SID S-1-16-4096。**实测定案**：SetFileInformationByHandle(FileIntegrityInfo) 非管理员一律 gle=5 失败；SetNamedSecurityInfo 零特权 rc=0 成功（label_probe.py / verify_low_flow.py 双实验），不与 icacls 混用
- DACL：追加当前用户 `(OI)(CI)F`（其余继承不变）
- Teardown：直接删除；删除失败（如残留句柄）记 warn + 由 StartupCleanup（现有组件）启动期兜底扫描
- 完整性继承（实测）：目录标签 → 子目录/子文件全链继承（verify_low_flow/label_subdir 实验 a.txt、sub、sub/f.txt 均为 Low+NW）；Medium 进程创建的文件无标签（默认 Medium 语义），宿主 Medium 写可写区放行（高写低默认规则，实测），属特性（自身 Teardown 清理需要），威胁模型已对齐接受

### 4.4 ProcessLauncherImpl 改造

- `LaunchRequest`：删除 `app_container_sid` / `app_container_capabilities`，新增 `void* isolated_token`
- Launch 内删除 SECURITY_CAPABILITIES / STARTUPINFOEX 分支，改为：`isolated_token != nullptr` 时 `CreateProcessAsUserW(isolated_token, ..., 普通 STARTUPINFOW)`；其余（管道/环境块/ConPTY hpcon/working_dir）不变
- 保留 OpenProcessToken 的调用位置只属于 AC 分支，一并删除；token 由上层（usecase）提供

### 4.5 配置重构

win-sandbox `IsolationPolicy`（core 实体）：

```cpp
struct IsolationPolicy {
    NetworkPolicy net_policy = NetworkPolicy::None;
    std::vector<NetworkRule> net_allowlist;   // net_policy=Allowlist 时生效（WFP 复用）
    bool clipboard_isolate = false;           // Job UI 限制（剪贴板/全局原子表/系统参数）
};  // fs 语义固定：全盘只读 + 自动可写区，无配置项
```

删除字段：`fs_mode`、`capabilities`、`path_rules`、`fs_config`。

PTY-Agent `sandbox.toml`（最终形态，2026-08-12 T6 落地）：

```toml
[isolation]
net_policy = "unrestricted"        # unrestricted = 不限制网络；allowlist = 仅白名单放行（WFP+SOCKS5）
net_allowlist = []                 # 网络白名单规则（仅 net_policy="allowlist" 生效）：{ ip, port, protocol }
clipboard_isolate = false          # 剪贴板隔离（Job UI 限制：剪贴板/全局原子表/系统参数）
```

删除：`fs_mode`、`capabilities`、`path_rules`。`src/config/sandbox.py` 的 ISOLATION dict 收敛为 `dict(_cfg["isolation"])`；`src/sandbox/manager.py` 的 `add_path_rule` / path_rules 深拷贝逻辑已删（引用点同步更新：pty.py cwd 授权调用删除，cwd 仅透传 working_dir）。

### 4.6 删除面（不留兼容接口）

| 类型 | 文件 |
|---|---|
| port | `core/ports/IAppContainer.hpp`、`IPathGrantor.hpp`、`IFileSystemIsolator.hpp` |
| entity | `core/entities/AppContainerProfile.hpp`、`FileSystemConfig.hpp`、`PathRule.hpp`（连同 PathAccess，确认无引用后） |
| usecase | `core/usecases/EnforcePolicyUseCase.*` |
| infra | `infra/appcontainer/`（AppContainerImpl、PathGrantorImpl、CapabilityMapping）、`infra/filesystem/FileSystemIsolatorImpl.*` |
| adapters | `PathRuleEngine.*`、`PermissionDetector.*`（确认引用后） |
| tests | `tests/unit/verify_t2x.cpp` 中 AC 相关、`tests/e2e/test_appcontainer.py`、`test_permission_matrix.py`、`test_filesystem.py`（重写为 lowil 语义）、`_native_helpers.py` 中 AC 依赖 |

注：`SandboxConfig`/`ConfigLoader` 是否仍被 native 形态引用，T2 时 grep 确认；无引用则一并删除（IPC 遗留）。

### 4.7 复用面

| 组件 | 处理 |
|---|---|
| JobObjectImpl（含 IOCP/通知/杀树/Query） | 原样复用；`SetUiLimits(true)` 已有剪贴板/原子表限制实现，仅接线 |
| WfpEngineImpl（WFP+SOCKS5） | 原样复用；net_policy 派生 capability 的逻辑（原 AC 专用）删除，allowlist 注入 PROXY 环境变量逻辑保留 |
| StartupCleanup | 复用：追加 WriteArea 残留扫描 |
| EnforcePolicyUseCase 删除后 GlobalQuota/Silo 不动 | — |

---

## 5. 实施阶段（分阶段实现，每阶段独立验收）

### T1 前置实验与收尾（已完成）

- [x] lowil_test.py：Low IL 读/写/执行实证（读系统文件 OK，写桌面/TEMP 拒）
- [x] probe_writearea.py：非管理员打 Low 标签 + Low 进程写入 OK
- [x] 清理实验残留目录（ws-probe-*）
- 验收：本文档 1.2 表格结论全部实测成立

### T2 core 实体与端口改造（已完成）

- [x] IsolationPolicy 重构（删 fs_mode/capabilities/path_rules/fs_config，加 clipboard_isolate）
- [x] StartProcessRequest 清理（isolation_policy 类型变化）
- [x] 新增 ITokenIsolator / IWriteArea 端口
- [x] 删除 IAppContainer / IPathGrantor / IFileSystemIsolator / AppContainerProfile / FileSystemConfig / PathRule（含 referenced adapters）
- [x] grep 确认 SandboxConfig/ConfigLoader 引用，未用则删
- 验收：`cmake --build` 通过（暂未删使用点，先改类型与空实现占位？——不允许占位：T2 只动实体/端口 + 删除无引用文件，使用点随 T3-T5 同步迁移，最终以 T7 全量编译为准；中间态允许临时编译不过，按阶段提交）

### T3 infra 实现（已完成）

- [x] TokenIsolatorImpl（token 派生链，对照 lowil_test.py 已验证逻辑）
- [x] WriteAreaImpl（创建/SetNamedSecurityInfo(SI_LABEL) 打标/Teardown；首选路线已实测定案，无回退分支）
- 验收：临时 probe 测试（非工程测试）验证 token IL 与可写区行为与实验一致

### T4 launcher 改造（已完成）

- [x] LaunchRequest 增 `isolated_token`，删 AC 双字段
- [x] Launch 删除 SECURITY_CAPABILITIES/STARTUPINFOEX 分支，走 CreateProcessAsUserW + STARTUPINFOW
- [x] 删除 CapabilityMapping（BuildCapabilitySidList 引用消失）
- 验收：编译 + 单独拉起 cmd 验证 stdio/ConPTY 行为不变

### T5 usecase / 装配 / bindings（已完成）

- [x] NativeSandboxedProcess：删 PrepareAppContainer/use_appcontainer/fs_isolator 分支，Execute 改为 token+writearea+env；Close 加 WriteArea Teardown；WFP allowlist 的 PROXY 注入保留
- [x] NativeSandboxInstance：装配替换
- [x] bindings：StartProcessRequest 的 isolation_policy dict 解析（fs_mode/path_rules/capabilities 键删，clipboard_isolate 加）
- 验收：native_smoke 7/7 + 新 test_lowil_isolation.py 6/6；首轮 e2e 基线 14/23（9 项失败逐一定位：7 项旧 schema 配置残留、2 项新隔离语义预期行为，见 T7）

### T6 PTY-Agent 配置重构（已完成）

- [x] sandbox.toml [isolation] 段重写 + 注释（net_policy/net_allowlist/clipboard_isolate）
- [x] sandbox.py ISOLATION dict 收敛；manager.py 删 path_rules 深拷贝与 add_path_rule；pty.py 删 cwd 白名单授权
- [x] vendored pyd 同步 Phase 16 构建（守护进程重启释放文件锁）
- 验收：unit/sandbox 57 + integration 9 + e2e 1 全绿（67/67）；沙箱会话可用
- 注意：Low IL 下以宿主 %TEMP% 下目录为 cwd 会被拒（"当前目录无效"），e2e 改为不传 cwd（继承项目根，可读可遍历）

### T7 测试更新与全量回归（已完成）

- [x] 新增 tests/e2e/test_lowil_isolation.py（FR-16.11 验收点全覆盖，6/6）
- [x] 删除 test_appcontainer.py / test_filesystem.py / test_network.py
- [x] 重写 test_cleanup.py（会话目录清理语义）、test_lowil_isolation.py；修复 test_resource_quota Test 6 / test_process_tree test_3（新隔离语义适配）；test_network_allowlist / test_native_etw / test_etw_admin / test_scenario_c_sample 轻改（去旧 schema 字段）
- [x] verify_t27.cpp 重写（ConfigLoader isolation 15 项 + PayloadParser 14 项，T1–T29，70/70）
- [x] 全量回归：e2e 21/21（run_all_regression，排除 test_etw_admin）+ ctest 6/6（probe_t16/verify_t11/14/17/27/28）
- [x] 文档更新（ARCHITECTURE/API_REFERENCE/USER_GUIDE/DEPLOYMENT + TROUBLESHOOTING/Lessons-Learned/FILESTREE + src 过时注释清理 + PTY-Agent CLI/design 文档）
- [x] 死代码清理：删 unit 死探针 verify_t21–t25 / probe_api / probe_grant_api / probe_create_profile / test_protocol.py（引用已删 infra/appcontainer/客户端库，从未注册 CMake）、tests/e2e/blackbox_phase8/9（依赖已删 sandbox.exe，Phase-14 T14.23 归档要求从未执行）与 _probe_*.py、src/core/entities/ClientInfo.hpp（零引用）、ErrorCode.hpp 16 个死枚举（IPC/JobRate/Stats/ETW/Silo 遗留）；FILESTREE.txt 重新生成；清理后重建 + ctest 6/6 + e2e 21/21 复验
- 验收：全绿 + `rg -i appcontainer src/ docs/` 仅剩 archive 历史文档、历史 phase/design 快照、Lessons-Learned 007/008/019 过时标注与 Phase 16"移除说明"注释/段落

---

## 6. 测试计划

### 6.1 新增 e2e（test_lowil_isolation.py）

1. **IL 断言**：沙箱进程 `TokenIsAppContainer(29)=NO`，IntegrityLevel=4096
2. **读系统文件**：`type C:\Windows\System32\drivers\etc\hosts` 成功
3. **写被拒**：写桌面、写 `C:\` 根、写用户非可写区路径 → 拒绝访问
4. **可写区**：`%TEMP%` 指向可写区且可写入、读回
5. **执行**：System32 下 exe（ping -n 1 127.0.0.1）可运行
6. **网络**：net_policy=unrestricted 出站 ping/HTTP 通
7. **清理**：进程关闭后可写区目录被删除（Teardown）
8. **clipboard_isolate=true**：剪贴板读写受限（API 查询行为断言，按 Job UILIMIT 语义）

### 6.2 回归（已完成）

- `tests/e2e/run_all_regression.py`（21/21 PASS，排除 test_etw_admin.py）
- `ctest --test-dir build -C Debug`（6/6 PASS：probe_t16 / verify_t11 / verify_t14 / verify_t17 / verify_t27 / verify_t28）
- PTY-Agent 端：unit/sandbox 57 + integration 9 + e2e ConPTY 1（67/67）

---

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| SetFileInformationByHandle 打 Low 标签非管理员失败 | **已实测定案**：gle=5 固定失败，弃用；路线 = SetNamedSecurityInfo(SI_LABEL)（rc=0，label_probe.py 验证），无回退分支 |
| Teardown 删除 Low 标签目录失败（实验曾出现 rmdir 报"找不到文件"） | 失败仅 warn 不阻断；StartupCleanup 启动期扫描兜底；T3 验证删除逻辑（含会话父目录一并删除，防空目录残留） |
| restricted token（特权 0）与非管理员冲突 | **已实测定案**：restricted token 非管理员启动必 1314（CreateProcessAsUserW/WithTokenW/NtCreateUserProcess 三路全失败）；用户拍板 plain 单路径（特权=宿主镜像，非管理员=5 无害特权），威胁模型=同用户能力全集 |
| 宿主 Medium 进程可写可写区（高写低放行 + Medium 建的子文件无标签） | 实测确认（med/a/b.txt 实验）；属威胁模型接受范围（同用户信任边界，与 HKCU 可写同级），文档 4.3 注明 |
| e2e 大量测试依赖 default_deny 白名单语义 | 语义变化即删除/重写（FR-16.11），不保留兼容模式 |
| `%TEMP%` 重定向后部分程序写死 `%USERPROFILE%` 的临时文件会失败 | 属预期隔离行为；web 终端交互不受影响（可写区足够） |
| WFP allowlist + SOCKS5 的 PROXY 注入依赖 AC 能力派生逻辑 | 注入逻辑仅依赖 net_policy 枚举，T5 保留并回归 test_network_allowlist.py |
| 沙箱进程 cwd：请求方未传 working_dir 时 | 默认落到可写区（可读可写，状态隔离）；传了就尊重请求值 |

---

## 8. 关键实验产物（临时目录，不并入仓库）

- `%TEMP%\opencode\lowil_test.py`：Low IL 行为对照实验（NORMAL vs LOW）
- `%TEMP%\opencode\probe_writearea.py`：非管理员打标签 + Low 写入实验
- [x] `%TEMP%\opencode\probe_ac2.py`：正确 TOKEN_INFORMATION_CLASS（29/31/30/11）token 检查工具
- `%TEMP%\opencode\setil_probe.py`：SetFileInformationByHandle(FileIntegrityInfo) 路线（8/12/16/20/24 大小全试，gle=5 定案弃用）
- `%TEMP%\opencode\label_probe.py`：SetNamedSecurityInfo(SI_LABEL) 手写 Label ACE（初版 ACE 结构错误 rc=1337 → 修正 ACCESS_MASK 字段 rc=0 成功）
- `%TEMP%\opencode\verify_low_flow.py`：最终闭环（native 打标 + LOW 写区 OK + LOW 写 %TEMP% 拒 + Medium 写区放行 + 清理恢复）
- `%TEMP%\opencode\label_inherit.py`：继承机制（LOW 建子对象继承 Low+NW 标签；Medium 建子对象无标签 = 默认 Medium）
- `%TEMP%\opencode\label_subdir.py`：LOW 在可写区 mkdir + 写子文件 OK（继承链完整）
- `%TEMP%\opencode\replay_cpa.py`：CreateProcessAsUserW 复现（restricted → 1314；PLAIN-DUP → 成功）
- `%TEMP%\opencode\replay_cpa2.py`：CreateProcessWithTokenW（restricted 与 plain 均 1314，API 不可用定案）
- `%TEMP%\opencode\replay_cpa3.py`：TokenIsRestricted 枚举 + NtCreateUserProcess（RtlCreateProcessParametersEx 修正后 STATUS_ACCESS_VIOLATION，ntdll 路线放弃）
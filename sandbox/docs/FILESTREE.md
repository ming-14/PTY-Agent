# sandbox/ 目录树

> C++ 原生沙箱工程（pybind11 编译为 `win_sandbox_native.pyd`，Windows 专属）

```
sandbox/
├── src/                       # ═══════ C++ 核心（WRITE_RESTRICTED 受限令牌 + Job） ═══════
│   ├── CMakeLists.txt         # pybind11 构建（唯一目标 win_sandbox_native.pyd；vcvars + Ninja）
│   ├── winacl.h               # 共享定义：Mode/ResourceLimits/LimitKind/Job/SpawnedChild + 函数声明
│   ├── token.cpp              # CreateRestrictedToken：LUA_TOKEN|WRITE_RESTRICTED，restricting SIDs 构建
│   ├── acl.cpp                # DACL 操作：grantWrite/revokeWrite（capability SID ACE）+ 宿主进程加固
│   ├── sid.cpp                # 能力 SID 派生（workspace/temp，S-1-4-x-y，sha256 确定性）+ SHA-256
│   ├── job.cpp                # Job Object：资源配额（内存/CPU/进程数/墙钟）+ KILL_ON_CLOSE + IOCP 通知
│   ├── spawn.cpp              # CreateProcessAsUserW 受限 spawn：ConPTY（HPCON）与继承 stdio 两路
│   ├── instance.cpp           # SandboxInstance/SandboxedProcess：授权物化、令牌、spawn、wait/terminate/查询
│   ├── module.cpp             # pybind11 绑定（SandboxInstance / Process，GIL 桥接回调）
│   └── selftest.cpp           # 独立 C++ 自测入口（不经 pybind11，隔离绑定层崩溃；手动编译运行）
└── third_party/
    └── pybind11/              # pybind11 头文件库（vendored，构建时由 CMake 引用）
```

## 构建产物（gitignore 忽略，不列出）

- `sandbox/src/build/` —— CMake/Ninja 构建目录
- `win_sandbox_native*.pyd` —— 编译产物，落入 `bin/win_sandbox/_native/`（经 vendored 包 `bin/win_sandbox` 加载）

## 模块关系

- `module.cpp` 暴露 `SandboxInstance.start_process` → `instance.cpp` 物化 workspace/temp 授权（`acl.cpp`）→ 构建受限令牌（`token.cpp`，SID 派生自 `sid.cpp`）→ `spawn.cpp` 以 ConPTY 或继承 stdio 启动 → 进程挂入 `job.cpp` 的 Job（配额 + 树终止）
- 宿主进程 DACL 加固（`acl.cpp` `hardenHostProcessDacl`）在每次 spawn 前执行：拒绝沙箱 restricting SIDs 对宿主的进程写权限（含 PROCESS_TERMINATE）

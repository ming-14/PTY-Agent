# 2026-08-05 测试基建补齐 + 3 个 Shutdown 崩溃 bug 修复

## 背景
用户要求检查功能缺失与测试完备性。发现测试基建重大缺口，并顺藤摸瓜修复了 3 个 Shutdown 崩溃 bug。

## 测试基建修复
1. **ctest 零测试注册**（最严重）：`tests/CMakeLists.txt` 定义了 12 个 verify_t*.exe 但无 add_test，
   `ctest` 空转，CI 中单测从未执行。已为 verify_t11/t14~t27 全部注册 add_test + TIMEOUT。
2. **verify_t11.cpp 孤儿**：T1.1 纯头编译验证存在但未注册 CMake，已注册（仅 include 目录）。
3. **AsyncSandboxClient 零测试**：新增 `tests/e2e/test_async_client.py`（5 用例）。
4. **protocol.py 零单测**：新增 `tests/unit/test_protocol.py`（16 用例，45 断言）。

## 3 个 Shutdown 崩溃 bug（均被 e2e 的 try/except 吞掉掩盖）
1. **ProcessEntry 整值移动赋值 use-after-free**（Lessons #012）：
   `ShutdownAll` 中 `entry = ProcessEntry{}` 按声明顺序释放，usecase 最后释放但依赖的
   job/app_container 等先释放 → 析构访问悬垂指针。改为显式按依赖顺序 reset。
2. **StopWallClockTimer 并发 join**（Lessons #013）：
   wait 线程与 usecase 析构同时调用 StopWallClockTimer → 并发 join 同一 std::thread。
   用 `std::once_flag` + `std::call_once` 修复。
3. **ShutdownComplete 被 broken pipe 丢弃**（Lessons #014）：
   服务端退出时命名管道未读缓冲可能丢数据。客户端 wait_exit 增加
   `except SandboxProcessError` 降级分支（sync + async）。
4. 附带：StartProcessUseCase::EmitAccessDenied 补上缺失的 `required_capability` 字段（verify_t26 一直失败）。

## 回归结果
- ctest 13/13 PASS（45s）
- e2e 18/18 PASS（含新增 test_async_client.py 5/5）
- 探针复现：Shutdown 崩溃修复前 6/8 失败，修复后 8/8 通过

## 待办（2026-08-06 全部完成）

- ~~文档~~ → ✅ USER_GUIDE / API_REFERENCE / DEPLOYMENT / TROUBLESHOOTING 均已写完
- ~~管理员模式 ETW 真路径验证~~ → ✅ 2026-08-06 以管理员权限验证，`test_etw_admin.py` 8/8 通过
- ~~CI 实跑验证~~ → ➖ CI 已移除（不需要 GitHub Actions）


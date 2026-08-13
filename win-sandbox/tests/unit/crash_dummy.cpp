// =============================================================================
// 测试助手：crash_dummy.exe
//
// 故意触发 ACCESS_VIOLATION（0xC0000005）崩溃的可执行程序，供:
//   - verify_t28（SetCrashSilent 运行时验证）
//   - e2e test_job_enhancement.py（崩溃场景验证）
// 使用：
//   crash_dummy.exe            → 空指针解引用崩溃
//   crash_dummy.exe --exit N   → 正常以退出码 N 退出（对照场景）
// =============================================================================

#include <windows.h>

#include <cstdint>
#include <cstdlib>
#include <cstring>

int main(int argc, char** argv) {
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--exit") == 0 && i + 1 < argc) {
            const int code = std::atoi(argv[i + 1]);
            ExitProcess(static_cast<UINT>(code));
        }
    }
    // 空指针解引用 → ACCESS_VIOLATION，退出码 0xC0000005
    volatile int* p = nullptr;
    *p = 12345;
    return 0;
}
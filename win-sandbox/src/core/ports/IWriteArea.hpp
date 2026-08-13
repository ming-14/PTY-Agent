// =============================================================================
// IWriteArea - 沙箱可写区端口（core 层）
//
// Low IL 进程唯一可写目录。
//   - 路径：%LOCALAPPDATA%\win-sandbox\sessions\<os-pid>-<process_id>\writable
//   - Create()：创建目录 + 打 Low(4096) 完整性标签 + 追加当前用户 (OI)(CI)F
//   - 沙箱进程 %TEMP%/%TMP% 重定向到此目录（唯一可写区域）
//   - Teardown()：递归删除（失败仅记日志，由 StartupCleanup 启动期兜底）
//
// 打标签能力：非管理员可用（SetNamedSecurityInfo(SI_LABEL) 零特权可用），
// 唯一路线 = SetNamedSecurityInfo（SetFileInformationByHandle
// FileIntegrityInfo 非管理员固定 gle=5，不可用）。
// =============================================================================
#pragma once

#include "core/entities/Result.hpp"

#include <cstdint>
#include <string>

namespace winsandbox {

class IWriteArea {
public:
    virtual ~IWriteArea() = default;

    // 创建可写区（幂等：已创建返回 Ok）
    virtual Result<void> Create(uint32_t process_id) = 0;

    // 可写区绝对路径（Create 成功后才有效）
    virtual std::string Path() const = 0;

    // 递归删除可写区（幂等）
    virtual Result<void> Teardown() = 0;
};

} // namespace winsandbox

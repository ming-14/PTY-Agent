// =============================================================================
// WriteAreaImpl - 沙箱可写区实现（infra 层）
//
// Low IL 进程唯一可写目录，路径：
//   %LOCALAPPDATA%\win-sandbox\sessions\<os-pid>-<process_id>\writable
//
// Create() 流程：
//   1. 逐级创建目录（sessions 根 → 会话目录 → writable）
//   2. 打 Low(4096) 完整性标签：SetNamedSecurityInfo(SI_LABEL)，
//      手写 SYSTEM_MANDATORY_LABEL_ACE（Mask=NO_WRITE_UP + SID S-1-16-4096）
//      —— SetFileInformationByHandle(FileIntegrityInfo) 非管理员固定 gle=5；
//         SetNamedSecurityInfo 零特权可用，无回退分支
//   3. 校验/追加当前用户 (OI)(CI)F：%LOCALAPPDATA% 继承 DACL 通常已含
//      用户 F（继承 ACE），此时跳过；无则追加，保证 Low 进程可读写
//
// Teardown()：递归删除（std::filesystem::remove_all，宽字符路径）；
//   失败仅记 warn（由 StartupCleanup 启动期兜底扫描），幂等。
//
// 编码：内部全宽字符（%LOCALAPPDATA% 含中文用户名时窄字符 std::filesystem
// 按 ACP 解释会错），对外 Path() 输出 UTF-8。
// =============================================================================
#pragma once

#include "core/ports/ILogger.hpp"
#include "core/ports/IWriteArea.hpp"

#include <memory>
#include <string>

namespace winsandbox {

class WriteAreaImpl : public IWriteArea {
public:
    explicit WriteAreaImpl(std::shared_ptr<ILogger> logger);

    // 创建可写区（幂等：已创建且目录存在返回 Ok）
    Result<void> Create(uint32_t process_id) override;

    // 可写区绝对路径（UTF-8，Create 成功后才有效）
    std::string Path() const override;

    // 递归删除可写区（幂等）
    Result<void> Teardown() override;

private:
    // 打 Low 完整性标签（SetNamedSecurityInfo(SI_LABEL)）
    Result<void> ApplyLowLabel(const std::wstring& dir);

    // 校验并追加当前用户 (OI)(CI)F（已含则跳过）
    Result<void> EnsureUserFullControl(const std::wstring& dir);

    std::string path_;          // writable 目录（UTF-8）
    std::string session_dir_;   // 会话父目录（UTF-8，Teardown 一并删除防残留）
    std::shared_ptr<ILogger> logger_;
};

} // namespace winsandbox
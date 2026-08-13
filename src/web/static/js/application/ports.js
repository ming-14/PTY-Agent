/**
 * 应用层：外部依赖端口（依赖倒置）
 *
 * Clean Architecture 要求应用层只依赖领域层，而基础设施/表现层的具体实现
 * 通过端口注入。这里提供一个全局 ports 对象，由入口 app.js 在启动时装配。
 */

export const ports = {
  transport: {
    send: null,
  },
  terminal: {
    handleOutput: null,
    replayPending: null,
    setAppMouseMode: null,
    // 终端框尺寸应用（原 infrastructure/terminalAdapter.applyTerminalFrameSize）
    applyTerminalFrameSize: null,
    // scrollback + snapshot 重建 xterm buffer（原 restoreScrollbackAndSnapshot）
    restoreScrollbackAndSnapshot: null,
    // 问题2：重新应用所有终端尺寸（原 infrastructure/terminalAdapter.reapplyAllTerminalSizes）
    // 应用层 messageHandlers 在被降级到 fixed 时调用，触发 fixed 模式 resize 同步守护进程
    reapplyAllTerminalSizes: null,
    // 问题2：按会话保存的 frameRatio 恢复框/stage 占比（原 infrastructure/terminalAdapter.applySessionFrameRatio）
    // 被降级到 fixed 时调用，确保切模式后 frame 占 stage 的比例保持不变（用户要求）
    applySessionFrameRatio: null,
  },
  ui: {
    renderTabs: null,
    renderSidebar: null,
    renderHistoryDropdown: null,
    switchTab: null,
    removeSessionTab: null,
    updateStatusInfo: null,
    applyReadonlyState: null,
    // 自动隐藏状态更新（原 presentation/views/autohide.updateAutoHide）
    updateAutoHide: null,
    // 系统状态栏更新（原 infrastructure/domUtils.updateSystemStatsUI）
    updateSystemStats: null,
    // 问题2：尺寸选择器下拉若已打开则重新渲染（自适应锁状态变更后调用）
    refreshSizeSelectorIfOpen: null,
  },
  detail: {
    showDetailDialog: null,
    updateDetailData: null,
    appendDetailEvent: null,
    applyDetailRefresh: null,
  },
  notification: {
    showToast: null,
  },
  vnc: {
    openVncTab: null,
    closeVncTab: null,
    renderVncPanel: null,
    updateVncStatus: null,
  },
  fastscreen: {
    openFastScreenTab: null,
    closeFastScreenTab: null,
    renderFastScreenPanel: null,
    handleMessage: null,
  },
  // 会话 handler 注册机制（原 presentation/views/sessionHandlers）
  // 提供给 application 层判断 sid 是否为 handler 会话、恢复 handler tab
  session: {
    isHandlerSid: null,        // (sid) => boolean
    restoreHandlerTab: null,   // (sid) => boolean  返回 true 表示有效并已恢复
  },
  // 设置存储适配器（原 infrastructure/settingsStorage.js）
  // 提供给 application/settingsStore.js 读写 localStorage + GET 默认值
  settingsStorage: {
    loadFromLocal: null,       // () => object
    saveToLocal: null,         // (values) => void
    loadFromServer: null,      // () => Promise<object>
  },
};

export function initPorts(deps) {
  if (deps.transport) Object.assign(ports.transport, deps.transport);
  if (deps.terminal) Object.assign(ports.terminal, deps.terminal);
  if (deps.ui) Object.assign(ports.ui, deps.ui);
  if (deps.detail) Object.assign(ports.detail, deps.detail);
  if (deps.notification) Object.assign(ports.notification, deps.notification);
  if (deps.vnc) Object.assign(ports.vnc, deps.vnc);
  if (deps.fastscreen) Object.assign(ports.fastscreen, deps.fastscreen);
  if (deps.session) Object.assign(ports.session, deps.session);
  if (deps.settingsStorage) Object.assign(ports.settingsStorage, deps.settingsStorage);
}

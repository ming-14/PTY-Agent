/**
 * 基础设施层：终端适配器（Facade）
 *
 * 将 terminal/ 子模块按功能域拆分后，通过本文件统一向外暴露接口，
 * 保持应用其它部分 import 路径不变。
 */

// 生命周期与核心操作
export {
  ensureTerminal,
  disposeTerminal,
  applyReadonlyState,
  handleOutput,
  replayPending,
  applyTheme,
  updateTerminalSnapshot,
  queuePendingOutput,
  restoreScrollbackAndSnapshot,
} from './terminal/lifecycle.js';

// 终端框尺寸与字号缩放（按会话 frameRatio，Ctrl+滚轮调比例不改 cols/rows）
export {
  applyTerminalFrameSize,
  applyTerminalSize,
  applyTerminalFontSize,
  reapplyAllTerminalSizes,
  applySessionFrameRatio, // 切标签/stage 变化时恢复框大小（adaptive 设 frame+fit 改 cols/rows；非 adaptive 反算字号）
  zoomActiveSession,       // Ctrl+滚轮统一缩放入口（所有模式按 ratio 反算字号，cols/rows 不变）
  resetActiveSessionZoom,  // Ctrl+0 重置（所有模式字号回默认再反算 ratio，cols/rows 不变）
  snapshotScrollbackForResize, // term.resize 前捕获完整内容（resize 后重放）
} from './terminal/scale.js';

// 终端尺寸模式相关纯函数
export {
  applyTerminalSizeFromSession,
} from './terminal/shared.js';

// 鼠标模式
export {
  setMouseModeChangeCallback,
  setAppMouseMode,
  toggleMouseInputOverride,
  getInitialMouseOverride,
} from './terminal/mouseMode.js';

// 滚动
export {
  scrollTermToTop,
} from './terminal/scroll.js';

// 撑满检测（全局 wheel 与终端 wheel 共用，防止框超出 stage）
export {
  isFrameAtMaxSize,
} from './terminal/events.js';

// 光标调试
export {
  logCursorState,
  forceCursorBlink,
  restartCursorBlinkIfNeeded,
} from './terminal/cursorDebug.js';

// 导入光标调试模块以触发其全局调试钩子注册（window.debugCursorState / window.forceCursorBlink）
import './terminal/cursorDebug.js';

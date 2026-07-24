/**
 * 应用组合根（Composition Root）
 *
 * 负责在启动时装配 Clean Architecture 各层：
 * - 领域层：state
 * - 应用层：messageHandlers / ports
 * - 基础设施层：wsClient / terminalAdapter / domUtils / storage
 * - 表现层：events 控制器 / ui + detail 视图
 *
 * 所有外层实现通过 application/ports.js 注入，保证依赖方向向内。
 */

import { state, loadTabState } from './domain/state.js';
import { warn, setLogLevel, setBufferSize } from './domain/logger.js';
import { setBodyTheme, applySidebarWidth } from './infrastructure/storage.js';
import { connect, wsSend, setMessageHandler } from './infrastructure/wsClient.js';
import {
  handleOutput,
  setLineMode,
  replayPending,
  setMouseModeChangeCallback,
  setAppMouseMode,
  applyReadonlyState,
  applyTerminalFrameSize,
  applyTheme,
  restoreScrollbackAndSnapshot,
  reapplyAllTerminalSizes,
  applySessionFrameRatio,
} from './infrastructure/terminalAdapter.js';
import { showToast, updateSystemStatsUI } from './infrastructure/domUtils.js';
import {
  loadFromLocal as settingsLoadFromLocal,
  saveToLocal as settingsSaveToLocal,
  loadFromServer as settingsLoadFromServer,
} from './infrastructure/settingsStorage.js';
import { initPorts } from './application/ports.js';
import * as settingsStore from './application/settingsStore.js';
import { handleMsg, handleShellList } from './application/messageHandlers.js';
import { bindGlobalEvents } from './presentation/controllers/events.js';
import {
  renderTabs,
  renderSidebar,
  renderHistoryDropdown,
  switchTab,
  removeSessionTab,
  updateStatusInfo,
  updateMouseModeButton,
} from './presentation/views/ui.js';
import { updateAutoHide } from './presentation/views/autohide.js';
import { getHandlerBySid } from './presentation/views/sessionHandlers.js';
import {
  showDetailDialog,
  updateDetailData,
  appendDetailEvent,
  applyDetailRefresh,
  initDetailDialog,
} from './presentation/views/detail.js';
import {
  openVncTab,
  closeVncTab,
  renderVncPanel,
  updateVncStatus,
  initVncView,
} from './presentation/views/vnc.js';
import {
  openFastScreenTab,
  closeFastScreenTab,
  renderFastScreenPanel,
  handleFastScreenMessage,
  initFastScreenView,
  applyFastScreenSetting,
} from './presentation/views/fastscreen.js';
import { initSettingsView } from './presentation/views/settings.js';
import { refreshSizeSelectorIfOpen } from './presentation/views/sizeSelector.js';
import { initDevConsole, setVisible as setDevConsoleVisible } from './presentation/views/devConsole.js';
import { init as rimeInit, onThemeChange as rimeOnThemeChange, applyImeSetting as rimeApplyImeSetting } from './infrastructure/rimeManager.js?v=42';
import { ensureMapleMonoLoaded, applyTerminalFontAll } from './infrastructure/fontLoader.js';

// 装配端口：将基础设施/表现层实现注入应用层
// ports.session 适配器：封装 sessionHandlers 的查询与恢复逻辑，
// 让 application 层通过 ports 调用而不直接依赖 presentation 层
function sessionIsHandlerSid(sid) {
  return !!getHandlerBySid(sid);
}
function sessionRestoreHandlerTab(sid) {
  const handler = getHandlerBySid(sid);
  if (!handler) return false;
  if (handler.isValid && !handler.isValid(sid)) return false;
  if (handler.restore) handler.restore(sid);
  return true;
}

initPorts({
  transport: { send: wsSend },
  terminal: {
    handleOutput,
    setLineMode,
    replayPending,
    setAppMouseMode,
    applyTerminalFrameSize,
    restoreScrollbackAndSnapshot,
    reapplyAllTerminalSizes,
    applySessionFrameRatio,
  },
  ui: {
    renderTabs,
    renderSidebar,
    renderHistoryDropdown,
    switchTab,
    removeSessionTab,
    updateStatusInfo,
    applyReadonlyState,
    updateAutoHide,
    updateSystemStats: updateSystemStatsUI,
    refreshSizeSelectorIfOpen,
  },
  detail: {
    showDetailDialog,
    updateDetailData,
    appendDetailEvent,
    applyDetailRefresh,
  },
  notification: { showToast },
  vnc: {
    openVncTab,
    closeVncTab,
    renderVncPanel,
    updateVncStatus,
  },
  fastscreen: {
    openFastScreenTab,
    closeFastScreenTab,
    renderFastScreenPanel,
    handleMessage: handleFastScreenMessage,
  },
  session: {
    isHandlerSid: sessionIsHandlerSid,
    restoreHandlerTab: sessionRestoreHandlerTab,
  },
  settingsStorage: {
    loadFromLocal: settingsLoadFromLocal,
    saveToLocal: settingsSaveToLocal,
    loadFromServer: settingsLoadFromServer,
  },
});

// WebSocket 消息分发接入应用层处理器
setMessageHandler(handleMsg);

function isTouchDevice() {
  return (
    ('ontouchstart' in window) ||
    (navigator.maxTouchPoints > 0) ||
    (window.matchMedia && window.matchMedia('(pointer: coarse)').matches)
  );
}

/**
 * 将 level 名称字符串转为 LEVELS 数值。
 * 设置项使用字符串值（'debug'/'info'/'warn'/'error'/'none'），与 logger 的数值常量桥接。
 */
const LEVEL_NAME_MAP = { debug: 0, info: 1, warn: 2, error: 3, none: 4 };
function _levelNameToNum(name) {
  return LEVEL_NAME_MAP[name] !== undefined ? LEVEL_NAME_MAP[name] : 4;
}

async function init() {
  // 先加载用户设置（合并 web.toml 默认值 + web_user_choice.json + localStorage 缓存）
  // 主题、IME、远程桌面等配置均依赖此步完成
  try {
    await settingsStore.load();
  } catch (e) {
    console.warn('settingsStore load failed, using defaults:', e);
  }

  // 应用主题（从 settingsStore 读取，替代旧的 pty_theme localStorage）
  setBodyTheme(settingsStore.get('basic.theme') || 'dark');

  // 预加载 MapleMono 字体（若用户之前已选择 maple-mono，页面刷新后需后台加载）
  // 加载完成后会自动应用到终端（此时终端可能尚未创建，ensureTerminal 会读 getTerminalFontFamily）
  if (settingsStore.get('basic.terminalFont') === 'maple-mono') {
    ensureMapleMonoLoaded().then(() => {
      applyTerminalFontAll();
    }).catch((e) => {
      warn('app', 'MapleMono preload failed: %s', e);
    });
  }

  // 初始化 FastScreen 参数（从 settingsStore 读取，替代 state.js 硬编码默认值）
  // state 在领域层不能依赖应用层，故在外层 app.js 装配
  state.fastscreen.fps = settingsStore.get('remote.fsFps') || state.fastscreen.fps;
  state.fastscreen.bitrate = settingsStore.get('remote.fsBitrate') || state.fastscreen.bitrate;
  state.fastscreen.streamFormat = settingsStore.get('remote.fsStreamFormat') || state.fastscreen.streamFormat;

  loadTabState();
  applySidebarWidth();

  // 通过真正检测触摸能力（而非窗口宽度）来标识触摸端
  const touch = isTouchDevice();
  if (touch) {
    document.body.classList.add('touch-device');
  }

  const sb = document.getElementById('sidebar');
  if (state.sidebarCollapsed) {
    sb.classList.add('collapsed');
  }
  // 触摸端首次访问默认收起侧边栏，避免占用宝贵空间
  if (touch && !localStorage.getItem('pty_sidebar_collapsed')) {
    sb.classList.add('collapsed');
    state.sidebarCollapsed = true;
    localStorage.setItem('pty_sidebar_collapsed', 'true');
  }

  // 订阅设置变更：实时应用非重启类设置
  // - basic.theme：主题切换（设置面板 pills / 顶栏快捷按钮均触发）
  // - basic.terminalFont：终端字体切换（MapleMono 异步加载后应用）
  // - ime.*：IME 参数（defaultState/candidateCount 实时生效；keyboardLayout/vertical/enabled 需重建）
  // - remote.fsFps/fsBitrate/fsStreamFormat：FastScreen 参数实时同步到 state
  // - developer.*：日志视窗可见性、日志等级、采集等级、缓冲区容量
  settingsStore.subscribe((key, value) => {
    if (key === 'basic.theme') {
      setBodyTheme(value);
      applyTheme();
      rimeOnThemeChange(document.body.dataset.theme || 'light');
    } else if (key === 'basic.terminalFont') {
      // 切换终端字体：选 MapleMono 时异步加载资源，加载完成后自动应用
      if (value === 'maple-mono') {
        ensureMapleMonoLoaded().then(() => {
          applyTerminalFontAll();
        }).catch((e) => {
          warn('app', 'MapleMono load failed, font not applied: %s', e);
        });
      } else {
        // 切回默认字体：直接应用
        applyTerminalFontAll();
      }
    } else if (key === 'remote.fsFps') {
      applyFastScreenSetting(key, value);
    } else if (key === 'remote.fsBitrate') {
      applyFastScreenSetting(key, value);
    } else if (key === 'remote.fsStreamFormat') {
      applyFastScreenSetting(key, value);
    } else if (key.startsWith('ime.')) {
      rimeApplyImeSetting(key, value);
    } else if (key === 'developer.logPanelEnabled') {
      setDevConsoleVisible(value);
    } else if (key === 'developer.logLevel') {
      setLogLevel(_levelNameToNum(value));
    } else if (key === 'developer.bufferSize') {
      setBufferSize(value);
    }
  });

  // 初始化开发者设置（应用 settingStore 中持久化的值，确保刷新后生效）
  setLogLevel(_levelNameToNum(settingsStore.get('developer.logLevel') || 'none'));
  setBufferSize(settingsStore.get('developer.bufferSize') || 1000);

  // 先注册连接成功钩子，再发起 WebSocket 连接，避免本地极速连接导致钩子未就绪
  window.__onWsOpen__ = () => {
    wsSend({ type: 'shells' });
  };

  // 用本地缓存的 shell 列表立即填充下拉框
  handleShellList(state.availableShells);

  connect();
  bindGlobalEvents();
  initDetailDialog();
  initVncView();
  initFastScreenView();
  initSettingsView();
  initDevConsole();

  // 初始化 Web RIME 输入法管理器（按 localStorage 恢复模式，懒加载 panel）
  rimeInit();

  // 终端应用鼠标模式变化时刷新状态按钮
  setMouseModeChangeCallback(() => updateMouseModeButton(state.activeTab));

  // 暴露全局状态供调试（光标问题排查）
  window.state = state;
}

init();

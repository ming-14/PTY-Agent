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
import { t, applyStaticText } from './domain/i18n.js';
import { setBodyTheme, applySidebarWidth } from './infrastructure/storage.js';
import { connect, wsSend, setMessageHandler } from './infrastructure/wsClient.js';
import { checkAuthStatus } from './infrastructure/auth.js';
import {
  handleOutput,
  replayPending,
  setMouseModeChangeCallback,
  setAppMouseMode,
  applyReadonlyState,
  applyTerminalFrameSize,
  applyTheme,
  restoreScrollbackAndSnapshot,
  reapplyAllTerminalSizes,
  applySessionFrameRatio,
  snapshotScrollbackForResize,
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
import { LogPanel } from '../vendor/logpanel/index.js';
import { loggerSource } from './infrastructure/logPanelAdapter.js';
import { init as rimeInit, onThemeChange as rimeOnThemeChange, applyImeSetting as rimeApplyImeSetting } from './infrastructure/rimeManager.js';
import { ensureMapleMonoLoaded, applyTerminalFontAll } from './infrastructure/fontLoader.js';

// ── 日志视窗（通用 LogPanel 插件实例） ──
let _logPanel = null;

// ── rikkajs 桌宠管理 ──
const _RIKKA_COUNT_LS_KEY = 'pty_rikka_count';
let _rikkaManager = null;
let _rikkaCssEl = null;
let _rikkaJsLoaded = false;
let _rikkaCount = parseInt(localStorage.getItem(_RIKKA_COUNT_LS_KEY), 10) || 1;
let _rikkaLastDisabledTime = 0;
let _rikkaResetTimer = null;

function _rikkaLabel() {
  const n = _rikkaCount;
  return n === 1 ? t('settings.rikkaLabel') : t('settings.rikkaLabelN', { num: n });
}

function _updateRikkaDesc() {
  const row = document.querySelector('[data-key="rikka.enabled"]')?.closest('.settings-row');
  if (row) {
    const labelEl = row.querySelector('.settings-row-label');
    if (labelEl) labelEl.textContent = _rikkaLabel();
  }
}

function _saveRikkaCount() {
  localStorage.setItem(_RIKKA_COUNT_LS_KEY, String(_rikkaCount));
}

function _loadRikkaAssets() {
  return new Promise((resolve, reject) => {
    if (_rikkaJsLoaded && window.Shimeji) { resolve(); return; }
    if (!_rikkaCssEl) {
      _rikkaCssEl = document.createElement('link');
      _rikkaCssEl.rel = 'stylesheet';
      _rikkaCssEl.href = '/vendor/rikkajs/shimeji.css';
      document.head.appendChild(_rikkaCssEl);
    }
    const script = document.createElement('script');
    script.src = '/vendor/rikkajs/shimeji.js';
    script.onload = () => { _rikkaJsLoaded = true; resolve(); };
    script.onerror = () => reject(new Error('shimeji.js load failed'));
    document.head.appendChild(script);
  });
}

function _startRikka(count) {
  const n = count || _rikkaCount;
  _loadRikkaAssets().then(() => {
    if (_rikkaManager) return;
    _rikkaManager = Shimeji.create({ maxCount: n });
    for (let i = 0; i < n; i++) _rikkaManager.addMascot();
    _rikkaManager.start();
  }).catch((e) => {
    warn('rikka', '桌宠加载失败: %s', e);
  });
}

function _stopRikka() {
  if (_rikkaManager) {
    _rikkaManager.disposeAll();
    _rikkaManager.stop();
    _rikkaManager = null;
  }
}

function _onRikkaEnabled(value) {
  if (value) {
    if (_rikkaResetTimer) { clearTimeout(_rikkaResetTimer); _rikkaResetTimer = null; }
    const now = Date.now();
    if (now - _rikkaLastDisabledTime < 300) {
      _rikkaCount++;
      _saveRikkaCount();
    }
    _startRikka(_rikkaCount);
  } else {
    _rikkaLastDisabledTime = Date.now();
    _stopRikka();
    if (_rikkaResetTimer) clearTimeout(_rikkaResetTimer);
    _rikkaResetTimer = setTimeout(() => {
      _rikkaCount = 1;
      _saveRikkaCount();
      _updateRikkaDesc();
      _rikkaResetTimer = null;
    }, 300);
  }
  _updateRikkaDesc();
}

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
    replayPending,
    setAppMouseMode,
    applyTerminalFrameSize,
    restoreScrollbackAndSnapshot,
    reapplyAllTerminalSizes,
    applySessionFrameRatio,
    snapshotScrollbackForResize,
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
  // 应用静态 HTML 文案（index.html 中 data-i18n 标注的初始文本）
  applyStaticText();

  // 先检查认证状态：若启用认证且未登录，跳转登录页
  const authStatus = await checkAuthStatus();
  if (authStatus.enabled && !authStatus.authenticated) {
    return; // checkAuthStatus 内部已跳转
  }

  // 先加载用户设置（合并 web.toml 默认值 + web_user_choice.json + localStorage 缓存）
  // 主题、IME、远程桌面等配置均依赖此步完成
  try {
    await settingsStore.load();
  } catch (e) {
    warn('settings', 'settingsStore load failed, using defaults: %s', e && e.message);
  }

  // 应用主题（从 settingsStore 读取）
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
    } else if (key === 'remote.cursorLocator') {
      wsSend({ type: value ? 'cursor_locator_start' : 'cursor_locator_stop' });
    } else if (key === 'remote.cursorLocatorOuterRadius'
            || key === 'remote.cursorLocatorInnerRadius'
            || key === 'remote.cursorLocatorAlpha') {
      const paramKey = key.replace('remote.cursorLocator', '').replace(/^./, c => c.toLowerCase());
      const paramMap = {
        'remote.cursorLocatorOuterRadius': 'outer_radius',
        'remote.cursorLocatorInnerRadius': 'inner_radius',
        'remote.cursorLocatorAlpha': 'alpha',
      };
      wsSend({ type: 'cursor_locator_update_config', [paramMap[key]]: value });
    } else if (key.startsWith('ime.')) {
      rimeApplyImeSetting(key, value);
    } else if (key === 'developer.logPanelEnabled') {
      if (value) _logPanel && _logPanel.show();
      else _logPanel && _logPanel.hide();
    } else if (key === 'developer.logLevel') {
      setLogLevel(_levelNameToNum(value));
    } else if (key === 'developer.bufferSize') {
      setBufferSize(value);
    } else if (key === 'developer.windowSize') {
      _logPanel && _logPanel.setOption('windowSize', value);
    } else if (key === 'developer.windowOpacity') {
      _logPanel && _logPanel.setOption('windowOpacity', value);
    } else if (key === 'rikka.enabled') {
      _onRikkaEnabled(value);
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
  // 创建日志视窗（通用 LogPanel 插件，source 由 loggerAdapter 注入）
  _logPanel = new LogPanel({
    source: loggerSource,
    storageKeyPrefix: 'pty_logpanel_',
    theme: 'auto',
    t,
    initialVisible: !!settingsStore.get('developer.logPanelEnabled'),
    windowSize: settingsStore.get('developer.windowSize'),
    windowOpacity: settingsStore.get('developer.windowOpacity'),
  });

  // 视窗可见性写回设置项：视窗关闭按钮 → settingsStore → 设置面板开关同步
  // settingsStore.set 内部有相等跳过保护，panel.show/hide 有状态守卫，不会循环
  _logPanel.on('visibleChange', (visible) => {
    if (settingsStore.get('developer.logPanelEnabled') !== visible) {
      settingsStore.set('developer.logPanelEnabled', visible);
    }
  });

  // 初始化 Web RIME 输入法管理器（按 localStorage 恢复模式，懒加载 panel）
  rimeInit();

  // 初始化 rikka 桌宠（根据设置开关决定是否启动）
  if (settingsStore.get('rikka.enabled')) {
    _startRikka(_rikkaCount);
  }
  requestAnimationFrame(_updateRikkaDesc);

  // 终端应用鼠标模式变化时刷新状态按钮
  setMouseModeChangeCallback(() => updateMouseModeButton(state.activeTab));

  // 暴露全局状态供调试（光标问题排查）
  window.state = state;
}

init();

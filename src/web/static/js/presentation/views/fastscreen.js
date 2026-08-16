/**
 * 表现层：FastScreen 屏幕查看视图
 *
 * 负责 FastScreen tab 的打开/关闭、控制面板渲染、目标选择、流连接管理。
 * FastScreen tab 使用特殊 sid（FASTSCREEN_TAB_ID）复用普通 tab 机制，
 * 切换时与 terminal-frame / vnc-frame 互斥显示。
 *
 * 三种流格式（由工具条切换）：
 * - mjpeg:     HTTP multipart/x-mixed-replace，<img> 标签直接渲染（最简单，带宽高）
 * - mse:       WS fmp4 segment，MediaSource Extensions + <video>（低延迟低带宽）
 * - webcodecs: WS annexb NAL，VideoDecoder 直接解码 + canvas 绘制（最低延迟）
 *
 * 依赖：领域层 state / constants、应用层 ports、基础设施 wsClient。
 */

import { state, saveTabState } from '../../domain/state.js';
import { FASTSCREEN_TAB_ID } from '../../domain/constants.js';
import { $, showToast } from '../../infrastructure/domUtils.js';
import { wsSend } from '../../infrastructure/wsClient.js';
import { debug, info, warn } from '../../domain/logger.js';
import { t, i18nError } from '../../domain/i18n.js';
import { updateScreenShareButtonVisibility } from './vnc.js';
import { updateAutoHide } from './autohide.js';
import { registerSessionHandler, removeTabAndSelectNext } from './sessionHandlers.js';
import * as settingsStore from '../../application/settingsStore.js';

// ── 模块级状态：当前活跃的流连接（切换格式/目标时需先断开旧连接） ──
let _activeStream = null;  // { format, cleanup } — cleanup 是断开流的函数
let _mseSourceBuffer = null;
let _mseQueue = [];        // MSE 初始化前的 segment 队列
let _webcodecsDecoder = null;
let _webcodecsCanvasCtx = null;
let _webcodecsFrameQueue = [];
let _statusPollTimer = null;  // fs_status 轮询定时器（tab 激活时每 5s 刷新活跃会话数）
let _targetsPollTimer = null; // fs_list_targets 轮询定时器（2s 刷新窗口/显示器列表）
let _selectActive = false;    // 目标选择器是否处于交互态（focus），此时跳过列表刷新避免重建 DOM 打断选择
let _selectListenersAttached = false; // 选择器 focus/blur 监听是否已绑定（仅绑定一次）

/**
 * 安全关闭 WebSocket：避免在 CONNECTING 状态下直接 close 触发
 * "WebSocket is closed before the connection is established" 警告。
 * CONNECTING 时改用 onopen 延迟关闭；OPEN 时直接关闭；CLOSING/CLOSED 无需操作。
 */
function _safeCloseWs(ws) {
  if (!ws) return;
  try {
    if (ws.readyState === WebSocket.CONNECTING) {
      // 连接尚未建立：等 open 后立即关闭，避免浏览器警告
      ws.onopen = () => { try { ws.close(); } catch (_) {} };
    } else if (ws.readyState === WebSocket.OPEN) {
      ws.close();
    }
    // CLOSING(2) / CLOSED(3) 状态无需操作
  } catch (_) {}
}

/**
 * 打开 FastScreen tab（单例：若已存在则仅切换）。
 * 加入 tabOrder，切换显示，请求最新状态与目标列表。
 */
export function openFastScreenTab() {
  info('fastscreen', 'openFastScreenTab, current tab=%s', state.activeTab);
  if (!state.tabOrder.includes(FASTSCREEN_TAB_ID)) {
    state.tabOrder.push(FASTSCREEN_TAB_ID);
    saveTabState();
  }
  switchToFastScreenFrame();
  state.activeTab = FASTSCREEN_TAB_ID;
  // 标记 FastScreen 会话为活跃（用于 autohide 等基于 state.sessions 的判断）
  state.sessions[FASTSCREEN_TAB_ID].running = true;
  saveTabState();
  // 请求最新状态 + 目标列表
  wsSend({ type: 'fs_status' });
  wsSend({ type: 'fs_list_targets' });
  renderFastScreenPanel();
}

/**
 * 关闭 FastScreen tab（从 tabOrder 移除，切到下一个普通 tab）。
 * 关闭 tab 不断开捕获（其他客户端可能仍在查看同一目标，按需连接断开由流自身管理）。
 *
 * 重构后：本函数仅设置 running=false 并委托给 ui.js closeTab 统一处理。
 * ui.js closeTab 通过 handler 分发：
 *   1. handler.close(sid) → 调用本模块的 _disconnectStream + _stopStatusPoll（类型特定清理）
 *   2. removeTabAndSelectNext(sid) → 统一 tab 移除 + 选择 nextTab
 *   3. 切换到 nextTab（通过 nextHandler.switchTo 或 openSessionInTab）
 * 这样消除了原 closeFastScreenTab 中 `if (nextTab === VNC_TAB_ID)` 的特判分支。
 */
export function closeFastScreenTab() {
  info('fastscreen', 'closeFastScreenTab');
  // 标记 FastScreen 会话不再活跃（在 handler.close 之前设置，确保 autohide 等立即生效）
  state.sessions[FASTSCREEN_TAB_ID].running = false;
  // 委托给 ui.js closeTab 通过 handler 分发统一处理
  // （动态 import 避免与 ui.js 导入本模块形成循环依赖）
  import('./ui.js').then(ui => {
    try { ui.closeTab(FASTSCREEN_TAB_ID); } catch (e) { warn('fastscreen', 'closeTab failed: %s', e); }
  });
}

/**
 * 从 FastScreen tab 切走时调用：断开流、停止轮询、隐藏 frame。
 * 由 ui.js switchTab 在 sid !== FASTSCREEN_TAB_ID 时调用。
 */
export function deactivateFastScreen() {
  _disconnectStream();
  _stopStatusPoll();
  _stopTargetsPoll();
}

/**
 * 根据 state.fastscreen 渲染控制面板。
 * 工具条：状态/目标选择器；下方为流渲染区。
 * 推流方式（streamFormat）和帧率（fps）已迁移至设置面板，由 settingsStore 实时同步。
 */
export function renderFastScreenPanel() {
  if (state.activeTab !== FASTSCREEN_TAB_ID) {
    debug('fastscreen', 'renderFastScreenPanel skipped: tab not active');
    return;
  }
  const fs = state.fastscreen;

  // 状态指示（优先使用本地流连接状态，后端 activeSessions 作为补充）
  const statusDot = $('fs-status-dot');
  const statusText = $('fs-status-text');
  if (statusDot && statusText) {
    let dotClass = 'stopped';
    let text = t('fs.notConnected');
    if (fs.disabled) {
      dotClass = 'disabled';
      text = t('fs.disabled');
    } else if (!fs.available) {
      dotClass = 'error';
      text = t('fs.dllLoadFailed');
    } else if (_activeStream) {
      // 本地有活跃流连接时显示已连接
      dotClass = 'running';
      const fmtLabel = _activeStream.format === 'mse' ? 'H264 MSE'
        : _activeStream.format === 'webcodecs' ? 'WebCodecs' : 'MJPEG';
      text = t('fs.connected', { fmt: fmtLabel });
      if (fs.activeSessions > 1) text += t('fs.sharingClients', { n: fs.activeSessions });
      text += ')';
    } else if (fs.activeSessions > 0) {
      // 本地无连接但后端有其他客户端的活跃会话
      dotClass = 'running';
      text = t('fs.activeSessions', { n: fs.activeSessions });
    }
    statusDot.className = 'fs-status-dot ' + dotClass;
    statusText.textContent = text;
    statusText.title = fs.error || text;
  }

  // 目标选择器（桌面/窗口下拉）
  _renderTargetSelector();

  // 鼠标增强光标定位器开关
  _renderCursorLocatorToggle();

  // 入口按钮可见性
  updateFastScreenButtonVisibility();
}

/**
 * 渲染鼠标增强光标定位器开关按钮状态。
 * 按钮在 index.html 中静态定义，此处仅更新激活态和可见性。
 */
function _renderCursorLocatorToggle() {
  const label = $('fs-cursor-locator-btn');
  const cb = $('fs-cursor-locator-cb');
  if (!label || !cb) return;
  const fs = state.fastscreen;
  if (!fs.cursorLocatorAvailable) {
    label.style.display = 'none';
    return;
  }
  label.style.display = 'flex';
  cb.checked = fs.cursorLocatorRunning;
  label.classList.toggle('active', fs.cursorLocatorRunning);
}

/**
 * 同步鼠标增强光标定位器状态到设置面板 toggle 和工具栏复选框。
 * @param {boolean} running 是否运行中
 */
function _syncCursorLocatorToggle(running) {
  const cb = $('fs-cursor-locator-cb');
  const label = $('fs-cursor-locator-btn');
  if (cb) cb.checked = running;
  if (label) label.classList.toggle('active', running);
  const settingsToggle = document.querySelector('.settings-toggle[data-key="remote.cursorLocator"]');
  if (settingsToggle) settingsToggle.classList.toggle('on', running);
}

/**
 * 切换鼠标增强光标定位器开关。
 */
function _toggleCursorLocator() {
  const fs = state.fastscreen;
  if (fs.cursorLocatorRunning) {
    wsSend({ type: 'cursor_locator_stop' });
  } else {
    wsSend({ type: 'cursor_locator_start' });
  }
}

/**
 * 渲染目标选择下拉（桌面列表 / 窗口列表切换）。
 * 使用签名缓存避免内容未变化时重建 DOM（防止手机端弹出选择器闪烁）。
 *
 * 单显示器优化：桌面模式下若只有一个显示器，无需让用户选择唯一目标，
 * 直接隐藏下拉并自动选中该显示器。切回窗口模式或多显示器时恢复显示。
 */
let _lastTargetSignature = '';
function _renderTargetSelector() {
  const fs = state.fastscreen;
  const select = $('fs-target-select');
  if (!select) return;

  // 桌面模式且只有一个显示器时隐藏选择器（无需让用户选择唯一的目标）
  if (fs.targetType === 'monitor' && fs.monitors.length <= 1) {
    select.style.display = 'none';
    // 自动选中唯一的显示器
    if (fs.monitors.length === 1) {
      fs.targetId = fs.monitors[0].id;
    }
    // 重置签名缓存，确保下次显示时重新构建选项
    _lastTargetSignature = '';
    return;
  }
  select.style.display = '';

  // 计算当前目标列表签名，若未变化则跳过重建
  // （fs_status 轮询每 5s 触发 renderFastScreenPanel，但不更新 monitors/windows，
  //   签名缓存可避免不必要的 innerHTML 重建导致手机端 <select> 弹出选择器闪烁）
  const list = fs.targetType === 'window' ? fs.windows : fs.monitors;
  const signature = fs.targetType + ':' + list.map(item => {
    const id = fs.targetType === 'window' ? item.hwnd : item.id;
    const label = fs.targetType === 'window' ? (item.title || '') : (item.name || '');
    return id + '|' + label + '|' + item.width + 'x' + item.height + '|' + (item.primary || false);
  }).join(',');

  if (signature === _lastTargetSignature) return;
  _lastTargetSignature = signature;

  // 保留当前选择
  const prevValue = String(fs.targetId);
  select.innerHTML = '';

  list.forEach(item => {
    const opt = document.createElement('option');
    const id = fs.targetType === 'window' ? item.hwnd : item.id;
    opt.value = String(id);
    let label;
    if (fs.targetType === 'window') {
      label = item.title || t('fs.windowPrefix', { id });
      if (label.length > 40) label = label.slice(0, 38) + '…';
      label += ' (' + item.width + 'x' + item.height + ')';
    } else {
      label = (item.name || t('fs.monitorPrefix', { id })) + (item.primary ? t('fs.primarySuffix') : '');
      label += ' (' + item.width + 'x' + item.height + ')';
    }
    opt.textContent = label;
    opt.title = label;
    select.appendChild(opt);
  });

  // 恢复选择（若新列表中存在）
  const hasPrev = Array.from(select.options).some(o => o.value === prevValue);
  select.value = hasPrev ? prevValue : (select.options[0] ? select.options[0].value : '0');
  fs.targetId = parseInt(select.value, 10) || 0;
}

/**
 * 切换到 FastScreen frame（隐藏 terminal-frame / vnc-frame，显示 fastscreen-frame）。
 * 由 ui.js switchTab 在 sid === FASTSCREEN_TAB_ID 时调用。
 */
export function switchToFastScreenFrame() {
  $('empty-state').style.display = 'none';
  $('terminal-frame').style.display = 'none';
  const vncFrame = $('vnc-frame');
  if (vncFrame) vncFrame.style.display = 'none';
  const settingsFrame = $('settings-frame');
  if (settingsFrame) settingsFrame.style.display = 'none';
  // 进入贴边模式：取消 terminal-stage 的 padding，让 FastScreen 画面贴边填满（不留白）
  $('terminal-stage').classList.add('stage-flush');
  $('fastscreen-frame').style.display = 'flex';
  // 隐藏终端专属状态项
  $('status-pty').style.display = 'none';
  $('status-size').style.display = 'none';
  renderFastScreenPanel();
  // 自动连接流（按需连接：进入 tab 即连接）
  _connectStream();
  // 启动状态轮询（保持活跃会话数同步）
  _startStatusPoll();
  // 启动目标列表轮询（2s 刷新窗口列表，选择器交互时自动跳过）
  _startTargetsPoll();
  // 更新自动隐藏模式（FastScreen tab 激活时可能触发）
  try { updateAutoHide(); } catch (e) { warn('fastscreen', 'updateAutoHide: %s', e); }
}

/**
 * 渲染 FastScreen tab（由 ui.js renderTabs 调用，返回 tab DOM 元素）。
 */
export function buildFastScreenTabElement() {
  if (!state.tabOrder.includes(FASTSCREEN_TAB_ID)) return null;
  const tab = document.createElement('div');
  tab.className = 'tab fastscreen-tab' + (state.activeTab === FASTSCREEN_TAB_ID ? ' active' : '');
  tab.dataset.sid = FASTSCREEN_TAB_ID;
  // 状态点：与状态栏 renderFastScreenPanel 绿点逻辑同步
  // 本地有活跃流连接(_activeStream) 或 后端有活跃会话(activeSessions>0) → 绿；否则灰
  // 注：disabled/error 在 tab 上统一显示为灰(ended)，与 VNC tab 体系一致
  let dotClass = 'ended';
  if (_activeStream || (state.fastscreen.available && state.fastscreen.activeSessions > 0)) {
    dotClass = 'running';
  }
  debug('fastscreen', 'buildTab dotClass=%s _activeStream=%s available=%s activeSessions=%s',
        dotClass, !!_activeStream, state.fastscreen.available, state.fastscreen.activeSessions);
  tab.innerHTML =
    '<span class="tab-icon ' + dotClass + '"></span>' +
    '<span class="tab-title" title="' + t('session.fastscreenTitle') + '">' + t('session.fastscreenTitle') + '</span>' +
    '<span class="tab-close" data-sid="' + FASTSCREEN_TAB_ID + '" title="' + t('common.closeTab') + '">' +
    '<svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>' +
    '</span>';
  tab.onclick = e => {
    if (e.target.closest('.tab-close')) return;
    openFastScreenTab();
  };
  tab.oncontextmenu = e => { e.preventDefault(); };
  const closeBtn = tab.querySelector('.tab-close');
  closeBtn.onclick = e => {
    e.stopPropagation();
    closeFastScreenTab();
  };
  return tab;
}

/**
 * 请求 ui.js 重新渲染 tab 栏。
 * 必须通过动态 import 调用 ui.js：ui.js 静态导入 fastscreen.js，
 * 若 fastscreen.js 反向静态导入 ui.js 会形成循环依赖导致初始化顺序问题。
 * 动态 import 在调用时才加载（ui.js 通常已缓存，开销可忽略）。
 */
function renderFastScreenTab() {
  import('./ui.js').then(ui => {
    try { ui.renderTabs(); } catch (e) { warn('fastscreen', 'renderTabs failed: %s', e); }
  });
}

/**
 * 根据 FastScreen 可用性更新标签栏入口按钮的显示。
 */
export function updateFastScreenButtonVisibility() {
  // FastScreen 入口集成到电脑图标下拉菜单中。
  // 委托给 vnc.js 的共享函数更新按钮和下拉选项可见性。
  try { updateScreenShareButtonVisibility(); } catch (e) { warn('fastscreen', 'updateScreenShareButtonVisibility: %s', e); }
}

/**
 * 处理后端 FastScreen 消息（fs_status / fs_targets / fs_error）。
 * 由 messageHandlers.js 通过 ports.fastscreen.handleMessage 调用。
 *
 * 重要：fs_status 每 5s 轮询一次（_startStatusPoll），若每次都调用 renderFastScreenTab()
 * 会触发 renderTabs() 清空重建所有 tab DOM，导致选项卡标题频繁闪烁。
 * 因此仅在影响 tab 显示的状态字段（_activeStream/disabled/available/activeSessions）
 * 真正变化时才刷新 tab。其他状态变化（monitors/windows 列表）只影响面板内容，
 * 由 renderFastScreenPanel() 处理即可。
 */
export function handleFastScreenMessage(msg) {
  const fs = state.fastscreen;
  debug('fastscreen', 'handleMessage type=%s', msg.type);
  // 计算 tab 显示签名（必须在状态更新前捕获，用于对比是否需要刷新 tab）
  const prevTabSig = _computeTabSignature();
  switch (msg.type) {
    case 'fs_status':
      fs.disabled = !!msg.disabled;
      fs.available = !!msg.available;
      fs.activeSessions = msg.active_sessions || 0;
      fs.cursorLocatorRunning = !!msg.cursor_locator_running;
      fs.cursorLocatorAvailable = !!msg.cursor_locator_available;
      _syncCursorLocatorToggle(fs.cursorLocatorRunning);
      if (msg.outer_radius != null) settingsStore.set('remote.cursorLocatorOuterRadius', msg.outer_radius);
      if (msg.inner_radius != null) settingsStore.set('remote.cursorLocatorInnerRadius', msg.inner_radius);
      if (msg.alpha != null) settingsStore.set('remote.cursorLocatorAlpha', msg.alpha);
      break;
    case 'fs_targets':
      fs.disabled = !!msg.disabled;
      fs.available = !!msg.available;
      fs.monitors = msg.monitors || [];
      fs.windows = msg.windows || [];
      if (msg.error) fs.error = msg.error;
      // 首次收到目标列表时，默认选择主显示器
      if (fs.targetType === 'monitor' && fs.monitors.length > 0) {
        const primary = fs.monitors.find(m => m.primary);
        fs.targetId = primary ? primary.id : fs.monitors[0].id;
      }
      break;
    case 'fs_error':
      fs.error = i18nError(msg) || t('common.unknown');
      showToast(t('fs.errorToast', { msg: fs.error }), 'error');
      break;
    case 'cursor_locator_status':
      fs.cursorLocatorRunning = !!msg.running;
      fs.cursorLocatorAvailable = !!msg.available;
      if (msg.outer_radius != null) settingsStore.set('remote.cursorLocatorOuterRadius', msg.outer_radius);
      if (msg.inner_radius != null) settingsStore.set('remote.cursorLocatorInnerRadius', msg.inner_radius);
      if (msg.alpha != null) settingsStore.set('remote.cursorLocatorAlpha', msg.alpha);
      _syncCursorLocatorToggle(fs.cursorLocatorRunning);
      break;
    case 'cursor_locator_started':
      fs.cursorLocatorRunning = true;
      _syncCursorLocatorToggle(true);
      break;
    case 'cursor_locator_stopped':
      fs.cursorLocatorRunning = false;
      _syncCursorLocatorToggle(false);
      break;
    case 'cursor_locator_error':
      showToast(t('fs.cursorLocatorErrorToast', { msg: i18nError(msg) || t('common.error') }), 'error');
      _syncCursorLocatorToggle(fs.cursorLocatorRunning);
      break;
  }
  updateFastScreenButtonVisibility();
  renderFastScreenPanel();
  // 状态变化时若 tab 激活，重新连接流（_reconnectIfChanged 可能改变 _activeStream）
  if (state.activeTab === FASTSCREEN_TAB_ID) {
    _reconnectIfChanged();
  }
  // 仅在 tab 显示签名变化时刷新 tab，避免 fs_status 5s 轮询导致标题闪烁
  const newTabSig = _computeTabSignature();
  if (newTabSig !== prevTabSig) {
    renderFastScreenTab();
  }
}

/**
 * 计算 FastScreen tab 显示签名。
 * 签名包含影响 tab 状态点(_activeStream/disabled/available/activeSessions)的字段，
 * 用于检测是否需要重建 tab DOM。其他字段（monitors/windows/error 等）不影响 tab 显示。
 */
function _computeTabSignature() {
  return `${_activeStream ? 1 : 0}|${state.fastscreen.disabled ? 1 : 0}|${state.fastscreen.available ? 1 : 0}|${state.fastscreen.activeSessions}`;
}

// ── 流连接管理 ──

/** 启动 fs_status 轮询（tab 激活时每 5s 刷新活跃会话数）。 */
function _startStatusPoll() {
  _stopStatusPoll();
  _statusPollTimer = setInterval(() => {
    wsSend({ type: 'fs_status' });
  }, 5000);
}

/** 停止 fs_status 轮询。 */
function _stopStatusPoll() {
  if (_statusPollTimer) {
    clearInterval(_statusPollTimer);
    _statusPollTimer = null;
  }
}

/**
 * 绑定目标选择器的 focus/blur 监听（仅绑定一次）。
 * focus 时标记 _selectActive=true，跳过列表刷新避免重建 DOM 打断用户选择；
 * blur 时恢复刷新并立即请求一次最新列表（补偿选择期间的跳过）。
 */
function _attachSelectListeners() {
  if (_selectListenersAttached) return;
  const select = $('fs-target-select');
  if (!select) return;
  _selectListenersAttached = true;
  select.addEventListener('focus', () => {
    _selectActive = true;
    debug('fastscreen', 'target select focused, pausing list refresh');
  });
  select.addEventListener('blur', () => {
    _selectActive = false;
    debug('fastscreen', 'target select blurred, resuming list refresh');
    // 选择结束后立即刷新一次，确保列表是最新的（补偿选择期间跳过的刷新）
    wsSend({ type: 'fs_list_targets' });
  });
}

/** 启动 fs_list_targets 轮询（2s 刷新目标列表，选择器交互时跳过）。 */
function _startTargetsPoll() {
  _stopTargetsPoll();
  _attachSelectListeners();
  _targetsPollTimer = setInterval(() => {
    // 用户正在选择器中交互时跳过刷新，避免重建 DOM 打断选择
    if (_selectActive) return;
    wsSend({ type: 'fs_list_targets' });
  }, 2000);
}

/** 停止 fs_list_targets 轮询。 */
function _stopTargetsPoll() {
  if (_targetsPollTimer) {
    clearInterval(_targetsPollTimer);
    _targetsPollTimer = null;
  }
}

/**
 * 根据当前 state.fastscreen 配置连接流。
 * 切换格式/目标时会先断开旧连接再建立新连接。
 */
function _connectStream() {
  const fs = state.fastscreen;
  if (fs.disabled || !fs.available) {
    _showPlaceholder(t('fs.placeholderDisabled'));
    return;
  }
  if ((fs.targetType === 'monitor' && fs.monitors.length === 0) ||
      (fs.targetType === 'window' && fs.windows.length === 0)) {
    _showPlaceholder(t('fs.loadingTargets'));
    return;
  }

  _disconnectStream();
  _hidePlaceholder();

  info('fastscreen', 'connectStream format=%s target=%s:%s',
       fs.streamFormat, fs.targetType, fs.targetId);

  try {
    if (fs.streamFormat === 'mjpeg') {
      _connectMjpeg();
    } else if (fs.streamFormat === 'mse') {
      _connectMse();
    } else if (fs.streamFormat === 'webcodecs') {
      _connectWebCodecs();
    }
    // 流连接（或正在连接）后刷新状态显示与 tab 状态点
    renderFastScreenPanel();
    renderFastScreenTab();
  } catch (e) {
    warn('fastscreen', 'connectStream failed: %s', e);
    _showPlaceholder(t('fs.connectFailed', { err: e }));
  }
}

/** 断开当前流连接并清理资源。 */
function _disconnectStream() {
  if (_activeStream && typeof _activeStream.cleanup === 'function') {
    try { _activeStream.cleanup(); } catch (e) { warn('fastscreen', 'stream cleanup: %s', e); }
  }
  _activeStream = null;
  _mseSourceBuffer = null;
  _mseQueue = [];
  _webcodecsDecoder = null;
  _webcodecsCanvasCtx = null;
  _webcodecsFrameQueue = [];
  // 标记流已断开，触发 autohide 状态更新
  state.fastscreen.connected = false;
  updateAutoHide();
  // 断开后刷新状态显示与 tab 状态点（仅在 FastScreen tab 激活时刷新面板）
  if (state.activeTab === FASTSCREEN_TAB_ID) {
    renderFastScreenPanel();
  }
  renderFastScreenTab();
}

/** 当目标/格式变化时重连。 */
function _reconnectIfChanged() {
  // 简化：每次状态变化都重连（实际只在目标/格式变化时需要）
  // 为了避免无谓重连，这里仅在 tab 激活且当前无流时连接
  if (!_activeStream) {
    _connectStream();
  }
}

// ── MJPEG 流（HTTP multipart）──

function _connectMjpeg() {
  const fs = state.fastscreen;
  const img = $('fs-mjpeg-img');
  const video = $('fs-video');
  const canvas = $('fs-canvas');
  if (!img) return;

  video.style.display = 'none';
  canvas.style.display = 'none';
  img.style.display = 'block';

  // 通过添加随机参数避免缓存
  const params = new URLSearchParams({
    target_type: fs.targetType,
    target_id: String(fs.targetId),
    method: fs.method,
    fps: String(fs.fps),
    quality: String(fs.quality),
  });
  const url = '/fastscreen/mjpeg?' + params.toString();

  // stall 检测：后端 window 模式无帧时发 1x1 纯红 JPEG 作为信号帧
  // 前端通过 naturalWidth===1 + 红色像素校验识别（统一后端判断，去掉前端定时器）
  const stallCanvas = document.createElement('canvas');
  stallCanvas.width = 1;
  stallCanvas.height = 1;
  const stallCtx = stallCanvas.getContext('2d');
  img.onload = () => {
    // 1x1 帧 = 后端 stall 信号（仅 window 模式会发，monitor 不发）
    if (img.naturalWidth === 1 && img.naturalHeight === 1) {
      // 校验纯红像素，确认是 stall 信号帧而非异常小帧
      try {
        stallCtx.drawImage(img, 0, 0);
        const pixel = stallCtx.getImageData(0, 0, 1, 1).data;
        if (pixel[0] >= 250 && pixel[1] <= 5 && pixel[2] <= 5) {
          _showPlaceholder(t('fs.windowMinimized'), t('fs.bringToFront'), _bringWindowToFront);
          debug('fastscreen', 'mjpeg stall frame detected (1x1 red)');
          return;
        }
      } catch (e) {
        debug('fastscreen', 'mjpeg stall pixel check failed: %s', e);
      }
    }
    _hidePlaceholder();
  };

  img.src = url;

  _activeStream = {
    format: 'mjpeg',
    cleanup: () => {
      img.onload = null;
      img.src = '';
    },
  };
  // 标记流已连接，触发 autohide 状态更新
  state.fastscreen.connected = true;
  updateAutoHide();
  debug('fastscreen', '_activeStream assigned (format set), connected=true');
}

// ── H264 MSE 流（WS fmp4 segment）──

function _connectMse() {
  const fs = state.fastscreen;
  const img = $('fs-mjpeg-img');
  const video = $('fs-video');
  const canvas = $('fs-canvas');
  if (!video) return;

  img.style.display = 'none';
  canvas.style.display = 'none';
  video.style.display = 'block';

  if (!window.MediaSource) {
    _showPlaceholder(t('fs.mseUnsupported'));
    return;
  }

  const ms = new MediaSource();
  video.src = URL.createObjectURL(ms);

  let ws = null;
  // resize 期间暂存 init segment，等 abort/updateend 完成后再 append
  let pendingResizeInit = null;
  const cleanupMse = () => {
    _safeCloseWs(ws);
    ws = null;
    _mseSourceBuffer = null;
    _mseQueue = [];
    pendingResizeInit = null;
    try { video.pause(); video.src = ''; } catch (_) {}
  };

  // 绑定 SourceBuffer 的 updateend 回调：flush 队列 + 自动播放 + resize init 追加
  function _bindSbEvents(sb) {
    sb.mode = 'segments';
    sb.addEventListener('updateend', () => {
      // resize init segment 等待追加：abort 或 remove 完成后追加新 init
      if (pendingResizeInit && !sb.updating) {
        const initPayload = pendingResizeInit;
        pendingResizeInit = null;
        try {
          sb.appendBuffer(initPayload);
          debug('fastscreen', 'mse appended resize init segment');
        } catch (e) {
          warn('fastscreen', 'mse append resize init failed: %s', e);
        }
        return;
      }
      // flush media segment 队列
      if (_mseQueue.length > 0 && !sb.updating) {
        const seg = _mseQueue.shift();
        try { sb.appendBuffer(seg); } catch (e) {
          warn('fastscreen', 'mse append queued failed: %s', e);
        }
      }
      // 自动播放
      if (video.paused && video.buffered.length > 0) {
        video.play().catch(() => {});
      }
    });
  }

  // 首次创建 SourceBuffer 并 append init segment
  function _createSourceBuffer(initPayload) {
    try {
      _mseSourceBuffer = ms.addSourceBuffer('video/mp4; codecs="avc1.42E01E"');
      _bindSbEvents(_mseSourceBuffer);
      try { _mseSourceBuffer.appendBuffer(initPayload); } catch (e) {
        warn('fastscreen', 'mse append init failed: %s', e);
      }
    } catch (e) {
      warn('fastscreen', 'mse addSourceBuffer failed: %s', e);
    }
  }

  ms.addEventListener('sourceopen', () => {
    // 建立 WS 连接
    const host = window.location.hostname || '127.0.0.1';
    const port = window.location.port || '18766';
    const wsUrl = 'ws://' + host + ':' + port + '/fastscreen/ws/mse';
    ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => {
      ws.send(JSON.stringify({
        target_type: fs.targetType,
        target_id: String(fs.targetId),
        method: fs.method,
        fps: fs.fps,
        width: 0,
        height: 0,
        bitrate: fs.bitrate,
        gop_size: fs.gopSize,
        quality: fs.quality,
      }));
    };

    ws.onmessage = ev => {
      if (typeof ev.data === 'string') {
        // JSON 控制消息
        try {
          const msg = JSON.parse(ev.data);
          if (msg.error) {
            _showPlaceholder(t('fs.mseError', { msg: msg.error }));
            cleanupMse();
          } else if (msg.closed === true) {
            // 窗口已关闭（句柄失效）：显示提示并停止流
            _showPlaceholder(i18nError(msg) || t('fs.windowClosed'));
            cleanupMse();
          } else if (msg.stall === true) {
            _showPlaceholder(t('fs.windowMinimized'), t('fs.bringToFront'), _bringWindowToFront);
          } else if (msg.stall === false) {
            _hidePlaceholder();
          }
        } catch (_) {}
        return;
      }
      // 二进制：前 8 字节为类型标记 \x00\x00\x00\x01 + 'init'/'segm'
      const data = new Uint8Array(ev.data);
      if (data.length < 8) return;
      const marker = String.fromCharCode.apply(null, data.subarray(4, 8));
      const payload = data.subarray(8);

      if (marker === 'init') {
        if (!_mseSourceBuffer) {
          // 首次创建 SourceBuffer
          _createSourceBuffer(payload);
        } else {
          // resize 后的 init segment：后端保留了 PTS 时间线（_base_media_decode_time 连续），
          // 所以只需 append 新 init segment 更新解码器配置（SPS/PPS），无需重置 currentTime
          _mseQueue.length = 0;  // 清空旧 media segment 队列（旧 SPS/PPS 无法用新配置解码）
          if (_mseSourceBuffer.updating) {
            // 正在 updating，先 abort，等 updateend 完成后追加 init
            pendingResizeInit = payload;
            try { _mseSourceBuffer.abort(); } catch (e) {
              warn('fastscreen', 'mse abort before resize init failed: %s', e);
            }
          } else {
            // 不在 updating，直接追加
            try { _mseSourceBuffer.appendBuffer(payload); } catch (e) {
              warn('fastscreen', 'mse append resize init failed: %s', e);
            }
          }
        }
      } else if (marker === 'segm') {
        // resize init 等待追加时（pendingResizeInit 存在），media segment 排队
        if (_mseSourceBuffer && !_mseSourceBuffer.updating && !pendingResizeInit) {
          try { _mseSourceBuffer.appendBuffer(payload); } catch (e) {
            if (_mseQueue.length > 5) _mseQueue.shift();
            _mseQueue.push(payload);
          }
        } else if (_mseSourceBuffer) {
          if (_mseQueue.length > 5) _mseQueue.shift();
          _mseQueue.push(payload);
        }
      }
    };

    ws.onerror = e => { warn('fastscreen', 'mse ws error: %s', e); };
    ws.onclose = () => { debug('fastscreen', 'mse ws closed'); };
  });

  _activeStream = {
    format: 'mse',
    cleanup: cleanupMse,
  };
  // 标记流已连接，触发 autohide 状态更新
  state.fastscreen.connected = true;
  updateAutoHide();
  debug('fastscreen', '_activeStream assigned (format set), connected=true');
}

// ── H264 WebCodecs 流（WS annexb NAL + VideoDecoder + canvas）──

function _connectWebCodecs() {
  const fs = state.fastscreen;
  const img = $('fs-mjpeg-img');
  const video = $('fs-video');
  const canvas = $('fs-canvas');
  if (!canvas) return;

  img.style.display = 'none';
  video.style.display = 'none';
  canvas.style.display = 'block';

  if (!('VideoDecoder' in window)) {
    _showPlaceholder(t('fs.webcodecsUnsupported'));
    return;
  }

  const ctx = canvas.getContext('2d');
  _webcodecsCanvasCtx = ctx;
  ctx.fillStyle = '#1a1a1a';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  let decoder = null;
  let ws = null;
  let spsPps = null;  // 缓存 SPS/PPS NAL，用于关键帧前的 decoderConfigure
  let frameCount = 0;
  // configure() 或 flush() 后必须先 decode 一个 key frame。
  // 后端 x264 pict_type=I 产生 non-IDR I 帧（NAL type 1，全 intra 宏块），
  // 内容可独立解码。前端通过 EncodedVideoChunk.type='key' 标记为关键帧，
  // WebCodecs 的 key frame 检查基于 type 字段而非 NAL type。
  // 首帧（无论 type 1 还是 5）标记为 'key'，后续按 NAL type 判断。
  let waitingForKeyframe = true;
  // 缓存上次 SPS 字节签名，用于检测窗口 resize（SPS 变化 → 需重新 configure decoder）
  let lastSpsSignature = '';

  const cleanupWebCodecs = () => {
    _safeCloseWs(ws);
    ws = null;
    if (decoder) {
      try { decoder.flush(); decoder.close(); } catch (_) {}
      decoder = null;
    }
    _webcodecsDecoder = null;
    _webcodecsCanvasCtx = null;
    _webcodecsFrameQueue = [];
  };

  // 解析 NAL 单元（返回 [{type, data}]，type: 7=SPS, 8=PPS, 5=IDR, 1=non-IDR）
  // 同时支持三种格式：
  //   1. 纯 annexb：起始码 00 00 01 / 00 00 00 01 分隔所有 NAL
  //   2. 纯 AVCC：4-byte big-endian length prefix + NAL data
  //   3. 混合格式：SPS/PPS 用 annexb，slice 用 AVCC（PyAV 某些配置可能出现）
  // 策略：先 annexb 解析，若没找到 slice (type 1/5)，在最后一个 annexb NAL 的数据中
  //   搜索内嵌的 AVCC slice（混合格式），最后尝试纯 AVCC。
  const parseNals = (buf) => {
    const view = new Uint8Array(buf);

    // 解析 annexb 格式，返回 { nals, lastEnd }
    const parseAnnexb = () => {
      const nals = [];
      const starts = [];  // NAL data 起始位置（起始码 01 之后）
      for (let i = 0; i < view.length - 2; i++) {
        if (view[i] === 0 && view[i+1] === 0 && view[i+2] === 1) {
          starts.push(i + 3);
        }
      }
      let lastEnd = 0;
      for (let i = 0; i < starts.length; i++) {
        const start = starts[i];
        let end;
        if (i + 1 < starts.length) {
          end = starts[i+1] - 3;
          if (end > 0 && view[end - 1] === 0) {
            end--;
          }
        } else {
          end = view.length;
        }
        const nalData = view.subarray(start, end);
        if (nalData.length > 0) {
          const nalType = nalData[0] & 0x1F;
          nals.push({ type: nalType, data: nalData });
        }
        lastEnd = end;
      }
      return { nals, lastEnd };
    };

    // 解析 AVCC 格式（4-byte big-endian length prefix + NAL data），从 offset 开始
    const parseAvcc = (offset) => {
      const nals = [];
      let i = offset;
      while (i + 4 <= view.length) {
        const len = (view[i] << 24) | (view[i+1] << 16) | (view[i+2] << 8) | view[i+3];
        if (len <= 0 || len > view.length - i - 4) break;
        const nalData = view.subarray(i + 4, i + 4 + len);
        if (nalData.length > 0) {
          const nalType = nalData[0] & 0x1F;
          nals.push({ type: nalType, data: nalData });
        }
        i += 4 + len;
      }
      return nals;
    };

    // 在 data 中搜索 AVCC slice（4-byte length prefix + NAL type 1/5）
    // 从 fromPos 开始，最多搜索 searchLen 字节。返回 { found, offset, len, nalType }
    const findAvccSlice = (data, fromPos, searchLen) => {
      const end = Math.min(fromPos + searchLen, data.length - 5);
      for (let i = fromPos; i < end; i++) {
        if (i + 5 > data.length) break;
        const len = (data[i] << 24) | (data[i+1] << 16) | (data[i+2] << 8) | data[i+3];
        if (len > 0 && len <= data.length - i - 4) {
          const nalType = data[i+4] & 0x1F;
          if (nalType === 1 || nalType === 5) {
            return { found: true, offset: i, len: len, nalType: nalType };
          }
        }
      }
      return { found: false };
    };

    const { nals: annexbNals, lastEnd: annexbEnd } = parseAnnexb();

    // annexb 已找到 slice（type 1 或 5），说明是纯 annexb 格式，直接返回
    if (annexbNals.some(n => n.type === 1 || n.type === 5)) {
      return annexbNals;
    }

    // hex 诊断日志（前 32 字节）
    const hexPrefix = Array.from(view.slice(0, 32))
      .map(b => b.toString(16).padStart(2, '0')).join(' ');

    // annexb 没找到 slice：检查最后一个 NAL 是否内嵌 AVCC slice（混合格式）
    // PyAV 可能输出 SPS/PPS 用 annexb 起始码，SLICE 用 AVCC length prefix
    // 此时最后一个 annexb NAL（PPS）的 data 包含 PPS 数据 + AVCC slice
    if (annexbNals.length > 0) {
      const lastNal = annexbNals[annexbNals.length - 1];
      // 最后一个 NAL 数据异常大（> 100 bytes），可能内嵌 AVCC slice
      if (lastNal.data.length > 100) {
        // 在 lastNal.data 中搜索 AVCC slice（从位置 2 开始，搜索前 200 字节）
        const result = findAvccSlice(lastNal.data, 2, 200);
        if (result.found) {
          // 分割 lastNal：保留原始 NAL 数据（如 PPS），提取 AVCC slice
          const originalData = lastNal.data.subarray(0, result.offset);
          const sliceData = lastNal.data.subarray(result.offset + 4, result.offset + 4 + result.len);
          // 更新最后一个 NAL 的数据（只保留 PPS 部分）
          annexbNals[annexbNals.length - 1] = { type: lastNal.type, data: originalData };
          // 添加 slice NAL
          annexbNals.push({ type: result.nalType, data: sliceData });
          debug('fastscreen', 'parseNals: mixed format, extracted AVCC slice type=%d len=%d at offset=%d, hex=%s',
                result.nalType, result.len, result.offset, hexPrefix);
          return annexbNals;
        }
      }
    }

    // 尝试纯 AVCC 解析（从开头）
    const pureAvccNals = parseAvcc(0);
    if (pureAvccNals.length > annexbNals.length) {
      debug('fastscreen', 'parseNals: AVCC format, annexb=%d nals, avcc=%d nals, hex=%s',
            annexbNals.length, pureAvccNals.length, hexPrefix);
      return pureAvccNals;
    }

    return annexbNals;
  };

  // avcc 格式化（4 字节长度前缀，无起始码）
  const toAvcc = (nalData) => {
    const out = new Uint8Array(4 + nalData.length);
    const dv = new DataView(out.buffer);
    dv.setUint32(0, nalData.length, false);  // big-endian
    out.set(nalData, 4);
    return out;
  };

  const host = window.location.hostname || '127.0.0.1';
  const port = window.location.port || '18766';
  const wsUrl = 'ws://' + host + ':' + port + '/fastscreen/ws/webcodecs';
  ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    ws.send(JSON.stringify({
      target_type: fs.targetType,
      target_id: String(fs.targetId),
      method: fs.method,
      fps: fs.fps,
      width: 0,
      height: 0,
      bitrate: fs.bitrate,
      gop_size: fs.gopSize,
      quality: fs.quality,
    }));
  };

  ws.onmessage = ev => {
    if (typeof ev.data === 'string') {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.error) {
          _showPlaceholder(t('fs.mseError', { msg: msg.error }));
          cleanupWebCodecs();
        } else if (msg.closed === true) {
          // 窗口已关闭（句柄失效）：显示提示并停止流
          _showPlaceholder(i18nError(msg) || t('fs.windowClosed'));
          cleanupWebCodecs();
        } else if (msg.stall === true) {
          _showPlaceholder(t('fs.windowMinimized'), t('fs.bringToFront'), _bringWindowToFront);
        } else if (msg.stall === false) {
          _hidePlaceholder();
        }
      } catch (_) {}
      return;
    }
    // 二进制：annexb NAL 流（可能包含多个 NAL）
    const nals = parseNals(ev.data);
    if (nals.length === 0) return;
    debug('fastscreen', 'ws recv binary: %d bytes, %d NALs, types=[%s]',
          ev.data.byteLength, nals.length, nals.map(n => n.type).join(','));

    // 收集 SPS/PPS（type 7/8），用于配置 decoder
    let hasSps = false, hasPps = false;
    const configNals = [];
    nals.forEach(n => {
      if (n.type === 7) { spsPps = spsPps || {}; spsPps.sps = n.data; hasSps = true; configNals.push(n.data); }
      else if (n.type === 8) { spsPps = spsPps || {}; spsPps.pps = n.data; hasPps = true; configNals.push(n.data); }
    });
    if (hasSps || hasPps) {
      debug('fastscreen', 'SPS=%s(%d bytes) PPS=%s(%d bytes)',
            hasSps ? 'found' : 'missing', spsPps && spsPps.sps ? spsPps.sps.length : 0,
            hasPps ? 'found' : 'missing', spsPps && spsPps.pps ? spsPps.pps.length : 0);
    }

    // 配置 decoder（首次创建或 SPS 变化时重新 configure）
    // 窗口 resize 会导致编码器重新生成 SPS（分辨率变化），必须重新 configure decoder
    if (spsPps && spsPps.sps && spsPps.pps) {
      // 计算 SPS 字节签名（长度 + 前 32 字节），用于检测分辨率变化
      const spsSig = spsPps.sps.length + ':' + Array.from(spsPps.sps.slice(0, 32)).map(b => b.toString(16).padStart(2, '0')).join('');
      const spsChanged = (spsSig !== lastSpsSignature);
      const needConfigure = !decoder || (decoder.state === 'configured' && spsChanged);

      if (needConfigure) {
        if (spsChanged && lastSpsSignature) {
          info('fastscreen', 'SPS changed (resize detected), reconfiguring decoder: %s -> %s', lastSpsSignature, spsSig);
        }
        lastSpsSignature = spsSig;

        if (!decoder) {
          // 首次创建 decoder
          try {
            decoder = new VideoDecoder({
              output: (frame) => {
                debug('fastscreen', 'decoder output frame: %dx%d ts=%d', frame.displayWidth, frame.displayHeight, frame.timestamp);
                _drawFrame(ctx, frame, canvas);
                frame.close();
              },
              error: (e) => {
                warn('fastscreen', 'decoder error: %s', e);
                // decoder 进入 closed 状态后无法恢复，需要重建
                // 等待下一帧 SPS/PPS 到达时自动重新 configure
                if (decoder && decoder.state === 'closed') {
                  warn('fastscreen', 'decoder closed, will reconfigure on next SPS/PPS');
                  decoder = null;
                  _webcodecsDecoder = null;
                  spsPps = null;
                  lastSpsSignature = '';
                  waitingForKeyframe = true;
                }
              },
            });
            _webcodecsDecoder = decoder;
          } catch (e) {
            warn('fastscreen', 'decoder create failed: %s', e);
            return;
          }
        }

        // 用 SPS/PPS 构造 description（avcc），configure（首次或 resize 重新 configure）
        try {
          const desc = _buildAvcDescription(spsPps.sps, spsPps.pps);
          decoder.configure({
            codec: 'avc1.42E01E',
            description: desc,
          });
          // configure() 后必须等待 IDR key frame 才能开始 decode
          waitingForKeyframe = true;
          debug('fastscreen', 'webcodecs decoder configured, waiting for keyframe, spsSig=%s', spsSig);
        } catch (e) {
          warn('fastscreen', 'decoder configure failed: %s', e);
          return;
        }
      }
    }

    if (!decoder) return;

    // 解码每个 NAL（除 SPS/PPS 外）
    nals.forEach(n => {
      if (n.type === 7 || n.type === 8) return;  // SPS/PPS 已用于 configure
      if (n.type !== 5 && n.type !== 1) return;  // 仅处理 IDR(5) 和 non-IDR(1)

      // configure()/flush() 后首帧必须标记为 key frame。
      // 后端禁用了 NAL 类型重写，首帧是 non-IDR I 帧（type 1），
      // 但内容是全 intra 宏块，可独立解码。
      // WebCodecs 按 EncodedVideoChunk.type 判断 key frame 要求，
      // 不检查 NAL type，所以 type:'key' + NAL type 1 可正常解码。
      let chunkType;
      if (waitingForKeyframe) {
        chunkType = 'key';
        waitingForKeyframe = false;
        debug('fastscreen', 'first frame after configure, NAL type=%d, marking as key', n.type);
      } else {
        chunkType = n.type === 5 ? 'key' : 'delta';
      }

      try {
        const chunk = new EncodedVideoChunk({
          type: chunkType,
          timestamp: frameCount * (1000000 / fs.fps),
          data: toAvcc(n.data),
        });
        frameCount++;
        decoder.decode(chunk);
        debug('fastscreen', 'decode chunk type=%s, nalType=%d, size=%d, queueSize=%d, state=%s',
              chunkType, n.type, n.data.length, decoder.decodeQueueSize, decoder.state);
      } catch (e) {
        warn('fastscreen', 'decode failed: %s', e);
      }
    });
  };

  ws.onerror = e => { warn('fastscreen', 'webcodecs ws error: %s', e); };
  ws.onclose = () => { debug('fastscreen', 'webcodecs ws closed'); };

  _activeStream = {
    format: 'webcodecs',
    cleanup: cleanupWebCodecs,
  };
  // 标记流已连接，触发 autohide 状态更新
  state.fastscreen.connected = true;
  updateAutoHide();
  debug('fastscreen', '_activeStream assigned (format set), connected=true');
}

/**
 * 绘制 VideoFrame 到 canvas（保持比例居中）。
 */
function _drawFrame(ctx, frame, canvas) {
  // 调整 canvas 尺寸匹配 frame
  if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
    debug('fastscreen', 'canvas resize: %dx%d -> %dx%d', canvas.width, canvas.height, frame.displayWidth, frame.displayHeight);
    canvas.width = frame.displayWidth;
    canvas.height = frame.displayHeight;
  }
  ctx.drawImage(frame, 0, 0, canvas.width, canvas.height);
}

/**
 * 构造 AVCDecoderConfigurationRecord（avcc 格式的 SPS/PPS 描述）。
 * 格式：AVCC length(1) + profile(1) + compat(1) + level(1) + 0xFF +
 *       0xE1 + SPS length(2) + SPS + 1 + PPS length(2) + PPS
 */
function _buildAvcDescription(sps, pps) {
  // SPS/PPS 在传入时已包含 NAL header 字节
  const buf = new Uint8Array(11 + sps.length + pps.length);
  let i = 0;
  buf[i++] = 1;            // configurationVersion
  buf[i++] = sps[1];       // AVCProfileIndication
  buf[i++] = sps[2];       // profile_compatibility
  buf[i++] = sps[3];       // AVCLevelIndication
  buf[i++] = 0xFF;         // lengthSizeMinusOne = 3
  buf[i++] = 0xE1;         // numOfSequenceParameterSets = 1
  buf[i++] = (sps.length >> 8) & 0xFF;
  buf[i++] = sps.length & 0xFF;
  buf.set(sps, i); i += sps.length;
  buf[i++] = 1;            // numOfPictureParameterSets = 1
  buf[i++] = (pps.length >> 8) & 0xFF;
  buf[i++] = pps.length & 0xFF;
  buf.set(pps, i);
  return buf;
}

// ── 占位符 ──

/**
 * 显示占位符。
 * @param {string} text - 占位文字
 * @param {string} [actionLabel] - 可选操作按钮文字（如"置于前台"）
 * @param {Function} [actionHandler] - 操作按钮点击回调
 */
function _showPlaceholder(text, actionLabel, actionHandler) {
  const ph = $('fs-placeholder');
  if (!ph) return;
  ph.style.display = 'flex';
  const t = ph.querySelector('.fs-placeholder-text');
  if (t) t.textContent = text;
  // 操作按钮：有 label + handler 时显示，否则隐藏
  const btn = ph.querySelector('.fs-placeholder-action');
  if (btn) {
    if (actionLabel && typeof actionHandler === 'function') {
      btn.textContent = actionLabel;
      btn.style.display = '';
      btn.onclick = actionHandler;
    } else {
      btn.style.display = 'none';
      btn.onclick = null;
    }
  }
}

function _hidePlaceholder() {
  const ph = $('fs-placeholder');
  if (ph) ph.style.display = 'none';
}

/**
 * 发送"置于前台"请求（窗口最小化时由占位符按钮调用）。
 * 仅窗口模式有效，后端通过 ShowWindowAsync + SetForegroundWindow 恢复并激活窗口。
 */
function _bringWindowToFront() {
  const fs = state.fastscreen;
  if (fs.targetType !== 'window' || !fs.targetId) {
    warn('fastscreen', 'bringToFront skipped: not window mode or invalid targetId');
    return;
  }
  info('fastscreen', 'bringToFront: target_id=%d', fs.targetId);
  wsSend({ type: 'fs_bring_to_front', target_type: 'window', target_id: fs.targetId });
}

// ── 事件绑定 ──

/**
 * 绑定 FastScreen 相关 DOM 事件（由 events.js 调用）。
 */
export function bindFastScreenEvents() {
  // FastScreen 入口由 vnc.js 下拉菜单处理

  // 目标类型切换（桌面/窗口）
  const targetTypeBtns = document.querySelectorAll('.fs-target-type-btn');
  targetTypeBtns.forEach(btn => {
    btn.onclick = () => {
      state.fastscreen.targetType = btn.dataset.type;
      // 切换类型后重置目标 ID（由 _renderTargetSelector 选择第一个）
      state.fastscreen.targetId = 0;
      // 更新按钮高亮
      targetTypeBtns.forEach(b => b.classList.toggle('active', b === btn));
      renderFastScreenPanel();
      _connectStream();
    };
  });

  // 目标下拉选择
  const targetSelect = $('fs-target-select');
  if (targetSelect) {
    targetSelect.onchange = () => {
      state.fastscreen.targetId = parseInt(targetSelect.value, 10) || 0;
      debug('fastscreen', 'target changed to %s', targetSelect.value);
      _connectStream();
    };
  }

  // 鼠标增强光标定位器复选框
  const cursorLocatorCb = $('fs-cursor-locator-cb');
  if (cursorLocatorCb) {
    cursorLocatorCb.onchange = () => _toggleCursorLocator();
  }

  // 推流方式（streamFormat）和帧率（fps）已迁移至设置面板，
  // 由 settingsStore.subscribe 实时同步到 state.fastscreen，工具栏不再提供控件
}

/**
 * 应用 FastScreen 设置变更（供 settingsStore.subscribe 实时调用）。
 *
 * 更新 state.fastscreen 对应字段，并在有活跃流连接时重连以应用新参数。
 * - remote.fsFps：帧率变更，需重连（捕获参数）
 * - remote.fsBitrate：码率变更，需重连（编码参数）
 * - remote.fsStreamFormat：推流方式变更，需重连（完全不同的流格式）
 *
 * @param {string} key 设置项 key
 * @param {*} value 新值
 */
export function applyFastScreenSetting(key, value) {
  if (key === 'remote.fsFps') {
    state.fastscreen.fps = value;
  } else if (key === 'remote.fsBitrate') {
    state.fastscreen.bitrate = value;
  } else if (key === 'remote.fsStreamFormat') {
    state.fastscreen.streamFormat = value;
  } else {
    return;
  }
  // 仅当 FastScreen tab 活跃且有流连接时重连，避免无流时多余触发
  if (state.activeTab === FASTSCREEN_TAB_ID && _activeStream) {
    debug('fastscreen', 'setting %s changed → reconnect stream', key);
    _connectStream();
  } else {
    debug('fastscreen', 'setting %s changed, no active stream to reconnect', key);
  }
}

/**
 * 初始化 FastScreen 视图（由 app.js 启动时调用）。
 * 请求初始状态以决定是否显示入口按钮。
 */
export function initFastScreenView() {
  bindFastScreenEvents();
  // 连接建立后请求 FastScreen 状态
  const prevOnOpen = window.__onWsOpen__;
  window.__onWsOpen__ = () => {
    if (typeof prevOnOpen === 'function') prevOnOpen();
    wsSend({ type: 'fs_status' });
  };
}

// ── 注册 FastScreen 会话 handler（模块加载时执行，由 import 副作用触发） ──
// handler 接口见 sessionHandlers.js。注册后 ui.js / messageHandlers.js 通过
// getHandlerBySid(sid) 分发，消除 if (sid === FASTSCREEN_TAB_ID) 特判。
registerSessionHandler('fastscreen', {
  // 切换到 FastScreen frame（隐藏 terminal/vnc frame，显示 fastscreen-frame + 连接流）
  switchTo: (sid) => switchToFastScreenFrame(),
  // 关闭时类型特定清理：标记非活跃 + 断开流 + 停止轮询（tab 移除与 nextTab 切换由 ui.js 统一处理）
  close: (sid) => { state.sessions[sid].running = false; _disconnectStream(); _stopStatusPoll(); _stopTargetsPoll(); },
  // 构建 FastScreen tab DOM 元素
  buildTab: (sid) => buildFastScreenTabElement(),
  // 页面刷新后恢复 FastScreen tab：重新请求状态与目标列表
  restore: (sid) => { wsSend({ type: 'fs_status' }); wsSend({ type: 'fs_list_targets' }); },
  // FastScreen tab 始终有效（不参与 session_list/history 清理）
  isValid: (sid) => true,
  // 打开 FastScreen tab
  open: (sid) => openFastScreenTab(),
});

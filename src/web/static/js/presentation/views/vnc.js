/**
 * 表现层：VNC 远程桌面视图
 *
 * 负责 VNC tab 的打开/关闭、控制面板渲染、iframe 加载与状态同步。
 * VNC tab 使用特殊 sid（VNC_TAB_ID）复用普通 tab 机制，切换时与 terminal-frame 互斥显示。
 *
 * 依赖：领域层 state / constants、应用层 ports、基础设施 wsClient。
 */

import { state, saveTabState } from '../../domain/state.js';
import { VNC_TAB_ID } from '../../domain/constants.js';
import { $, showToast } from '../../infrastructure/domUtils.js';
import { wsSend } from '../../infrastructure/wsClient.js';
import { debug, info, warn } from '../../domain/logger.js';
import { t, i18nError } from '../../domain/i18n.js';
import { initAutoHide, updateAutoHide } from './autohide.js';
import { registerSessionHandler, removeTabAndSelectNext } from './sessionHandlers.js';

/**
 * 构造 noVNC iframe URL。
 * WebSocket 连接通过守护进程的 /vnc/websockify 端点代理到 VNC TCP 端口，
 * 不再使用独立 websockify 端口，统一到守护进程端口。
 * @param {string} password VNC 密码
 * @returns {string} iframe src URL
 */
function buildVncIframeUrl(password) {
  const host = window.location.hostname || '127.0.0.1';
  const port = window.location.port || '18766';
  const params = new URLSearchParams({
    host: host,
    port: port,
    password: password || '',
    path: 'vnc/websockify',
    autoconnect: '1',
    resize: 'scale',
    reconnect: '0',
  });
  return '/static/novnc/vnc.html?' + params.toString();
}

/**
 * 打开 VNC tab（单例：若已存在则仅切换）。
 * 加入 tabOrder，切换显示，并请求最新 VNC 状态。
 */
export function openVncTab() {
  info('vnc', 'openVncTab, current tab=%s', state.activeTab);
  if (!state.tabOrder.includes(VNC_TAB_ID)) {
    state.tabOrder.push(VNC_TAB_ID);
    saveTabState();
  }
  // 切换到 VNC tab（ui.js switchTab 负责 vnc-frame / terminal-frame 互斥）
  switchToVncFrame();
  state.activeTab = VNC_TAB_ID;
  // 标记 VNC 会话为活跃（用于统一会话模型，与 FastScreen 一致）
  state.sessions[VNC_TAB_ID].running = true;
  saveTabState();
  // 请求最新状态（确保控制面板与后端同步）
  wsSend({ type: 'vnc_status' });
  renderVncPanel();
  updateAutoHide();
}

/**
 * 关闭 VNC tab（从 tabOrder 移除，切到下一个普通 tab）。
 * 注意：关闭 tab 不停止 VNC 进程（单例共享桌面，其他用户可能仍在使用）。
 *
 * 重构后：本函数仅设置 running=false 并委托给 ui.js closeTab 统一处理。
 * ui.js closeTab 通过 handler 分发：
 *   1. handler.close(sid) → VNC 无需类型特定清理（进程不停止，iframe 由 renderVncPanel 管理）
 *   2. removeTabAndSelectNext(sid) → 统一 tab 移除 + 选择 nextTab
 *   3. 切换到 nextTab（通过 nextHandler.switchTo 或 openSessionInTab）
 * 这样消除了原 closeVncTab 中 `if (nextTab === FASTSCREEN_TAB_ID)` 的特判分支。
 */
export function closeVncTab() {
  info('vnc', 'closeVncTab');
  // 标记 VNC 会话不再活跃
  state.sessions[VNC_TAB_ID].running = false;
  // 委托给 ui.js closeTab 通过 handler 分发统一处理
  // （动态 import 避免与 ui.js 导入本模块形成循环依赖）
  import('./ui.js').then(ui => {
    try { ui.closeTab(VNC_TAB_ID); } catch (e) { warn('vnc', 'closeTab failed: %s', e); }
  });
}

/**
 * 启动 VNC 服务（发送 vnc_start，设 starting 状态）。
 */
export function startVnc() {
  if (state.vnc.starting || state.vnc.running) {
    debug('vnc', 'startVnc skipped: starting=%s running=%s', state.vnc.starting, state.vnc.running);
    return;
  }
  info('vnc', 'startVnc: sending vnc_start');
  state.vnc.starting = true;
  state.vnc.error = null;
  renderVncPanel();
  wsSend({ type: 'vnc_start' });
}

/**
 * 停止 VNC 服务（发送 vnc_stop，设 stopping 状态）。
 */
export function stopVnc() {
  if (state.vnc.stopping || !state.vnc.running) {
    debug('vnc', 'stopVnc skipped: stopping=%s running=%s', state.vnc.stopping, state.vnc.running);
    return;
  }
  info('vnc', 'stopVnc: sending vnc_stop');
  state.vnc.stopping = true;
  state.vnc.error = null;
  renderVncPanel();
  wsSend({ type: 'vnc_stop' });
}

/**
 * 根据 state.vnc 渲染 VNC 控制面板（状态/密码/端口/按钮 + iframe/placeholder）。
 * 仅当 VNC tab 当前激活时才更新 DOM。
 */
export function renderVncPanel() {
  if (state.activeTab !== VNC_TAB_ID) {
    debug('vnc', 'renderVncPanel skipped: VNC tab not active');
    return;
  }
  const v = state.vnc;
  const statusDot = $('vnc-status-dot');
  const statusText = $('vnc-status-text');
  const infoArea = $('vnc-info-area');
  const infoPassword = $('vnc-info-password');
  const infoPort = $('vnc-info-port');
  const btnStart = $('btn-vnc-start');
  const btnStop = $('btn-vnc-stop');
  const placeholder = $('vnc-placeholder');
  const iframe = $('vnc-iframe');

  // 状态指示
  let dotClass = 'stopped';
  let text = t('vnc.notStarted');
  if (v.disabled) {
    dotClass = 'disabled';
    text = t('vnc.disabled');
  } else if (v.starting) {
    dotClass = 'starting';
    text = t('vnc.starting');
  } else if (v.stopping) {
    dotClass = 'stopping';
    text = t('vnc.stopping');
  } else if (v.running) {
    dotClass = 'running';
    text = t('vnc.running');
  } else if (v.error) {
    dotClass = 'error';
    text = t('vnc.error');
  }
  statusDot.className = 'vnc-status-dot ' + dotClass;
  statusText.textContent = text;
  statusText.title = v.error || text;

  // 密码/端口信息（仅运行中显示）
  if (v.running && v.password) {
    infoArea.style.display = 'flex';
    infoPassword.textContent = t('vnc.password', { pwd: v.password });
    infoPassword.title = t('vnc.copyPassword');
    infoPort.textContent = t('vnc.port', { port: v.vncPort });
    infoPort.title = t('vnc.portTitle', { port: v.vncPort });
  } else {
    infoArea.style.display = 'none';
  }

  // 启动/停止按钮
  if (v.disabled) {
    btnStart.style.display = 'none';
    btnStop.style.display = 'none';
  } else if (v.starting || v.stopping) {
    btnStart.style.display = 'none';
    btnStop.style.display = 'none';
  } else if (v.running) {
    btnStart.style.display = 'none';
    btnStop.style.display = 'inline-flex';
  } else {
    btnStart.style.display = 'inline-flex';
    btnStop.style.display = 'none';
  }

  // iframe / placeholder
  if (v.running && v.password) {
    const url = buildVncIframeUrl(v.password);
    if (iframe.src !== url) {
      info('vnc', 'loading iframe url=%s', url);
      iframe.src = url;
    }
    iframe.style.display = 'block';
    placeholder.style.display = 'none';
  } else {
    // 清空 iframe src 避免后台保持连接
    if (iframe.src) iframe.src = 'about:blank';
    iframe.style.display = 'none';
    placeholder.style.display = 'flex';
    if (v.error) {
      placeholder.querySelector('.vnc-placeholder-text').textContent = v.error;
    } else if (v.disabled) {
      placeholder.querySelector('.vnc-placeholder-text').textContent = t('vnc.featureDisabled');
    } else {
      placeholder.querySelector('.vnc-placeholder-text').textContent = t('vnc.clickStart');
    }
  }
}

/**
 * 更新 VNC 状态（由 messageHandlers 调用）并刷新 UI。
 * @param {object} status 后端返回的 vnc_status / vnc_started / vnc_stopped 消息
 */
export function updateVncStatus(msg) {
  const v = state.vnc;
  debug('vnc', 'updateVncStatus type=%s running=%s', msg.type, msg.running);
  switch (msg.type) {
    case 'vnc_status':
      v.disabled = !!msg.disabled;
      v.winvncAvailable = !!msg.winvnc_available;
      v.running = !!msg.running;
      v.vncPort = msg.vnc_port || null;
      v.password = msg.password || null;
      v.vncPid = msg.vnc_pid || null;
      v.starting = false;
      v.stopping = false;
      break;
    case 'vnc_started':
      v.running = true;
      v.starting = false;
      v.vncPort = msg.vnc_port || null;
      v.password = msg.password || null;
      v.vncPid = msg.vnc_pid || null;
      v.error = null;
      showToast(t('vnc.startedToast'), 'success');
      break;
    case 'vnc_stopped':
      v.running = false;
      v.stopping = false;
      v.vncPort = null;
      v.password = null;
      v.vncPid = null;
      showToast(t('vnc.stoppedToast'), 'info');
      break;
    case 'vnc_error':
      v.starting = false;
      v.stopping = false;
      v.error = i18nError(msg) || t('vnc.unknownError');
      showToast(t('vnc.errorToast', { msg: v.error }), 'error');
      break;
  }
  // VNC 功能可用性变化时刷新按钮入口显示
  updateVncButtonVisibility();
  // VNC tab 激活时刷新控制面板
  renderVncPanel();
  // 刷新 tab 栏（VNC tab 的运行状态指示）
  renderVncTab();
  updateAutoHide();
}

/**
 * 切换到 VNC frame（隐藏 terminal-frame，显示 vnc-frame）。
 * 由 ui.js switchTab 在 sid === VNC_TAB_ID 时调用。
 */
export function switchToVncFrame() {
  $('empty-state').style.display = 'none';
  $('terminal-frame').style.display = 'none';
  // 隐藏 FastScreen frame（防止从 FastScreen 切到 VNC 时画面残留）
  const fsFrame = $('fastscreen-frame');
  if (fsFrame) fsFrame.style.display = 'none';
  // 隐藏设置 frame
  const settingsFrame = $('settings-frame');
  if (settingsFrame) settingsFrame.style.display = 'none';
  // 进入贴边模式：取消 terminal-stage 的 padding，让 VNC 画面贴边填满（不留白）
  $('terminal-stage').classList.add('stage-flush');
  $('vnc-frame').style.display = 'flex';
  // 隐藏终端专属状态项
  $('status-pty').style.display = 'none';
  $('status-size').style.display = 'none';
  renderVncPanel();
  updateAutoHide();
}

/**
 * 渲染 VNC tab（由 ui.js renderTabs 调用，返回 tab DOM 元素）。
 * @returns {HTMLElement|null} VNC tab 元素，若 VNC tab 不在 tabOrder 中返回 null
 */
export function buildVncTabElement() {
  if (!state.tabOrder.includes(VNC_TAB_ID)) return null;
  const tab = document.createElement('div');
  tab.className = 'tab vnc-tab' + (state.activeTab === VNC_TAB_ID ? ' active' : '');
  tab.dataset.sid = VNC_TAB_ID;
  const dotClass = state.vnc.running ? 'running' : 'ended';
  tab.innerHTML =
    '<span class="tab-icon ' + dotClass + '"></span>' +
    '<span class="tab-title" title="' + t('session.vncTitle') + '">' + t('session.vncTitle') + '</span>' +
    '<span class="tab-close" data-sid="' + VNC_TAB_ID + '" title="' + t('common.closeTab') + '">' +
    '<svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>' +
    '</span>';
  tab.onclick = e => {
    if (e.target.closest('.tab-close')) return;
    openVncTab();
  };
  tab.oncontextmenu = e => {
    e.preventDefault();
    // VNC tab 右键菜单暂不提供额外操作，仅防止默认右键
  };
  const closeBtn = tab.querySelector('.tab-close');
  closeBtn.onclick = e => {
    e.stopPropagation();
    closeVncTab();
  };
  return tab;
}

/**
 * 请求 ui.js 重新渲染 tab 栏。
 * 必须通过动态 import 调用 ui.js：ui.js 静态导入 vnc.js，
 * 若 vnc.js 反向静态导入 ui.js 会形成循环依赖导致初始化顺序问题。
 * 动态 import 在调用时才加载（ui.js 通常已缓存，开销可忽略）。
 */
function renderVncTab() {
  import('./ui.js').then(ui => {
    try { ui.renderTabs(); } catch (e) { warn('vnc', 'renderTabs failed: %s', e); }
  });
}

/**
 * 根据 VNC 可用性更新标签栏 #btn-vnc-tab 按钮的显示。
 * disabled=true 或 winvncAvailable=false 时隐藏按钮。
 */
export function updateVncButtonVisibility() {
  // 委托给共享函数：同时检查 VNC 和 FastScreen 可用性，更新按钮和选项可见性
  updateScreenShareButtonVisibility();
}

/**
 * 绑定 VNC 相关 DOM 事件（由 events.js 调用）。
 */
export function bindVncEvents() {
  const btnVncTab = $('btn-vnc-tab');
  if (btnVncTab) {
    // 点击电脑图标：切换下拉菜单（不再直接打开 VNC tab）
    btnVncTab.onclick = (e) => {
      e.stopPropagation();
      _toggleDropdown(!_dropdownOpen);
    };
  }

  // 下拉菜单选项点击
  const vncOption = $('ss-option-vnc');
  if (vncOption) {
    vncOption.onclick = (e) => {
      e.stopPropagation();
      _toggleDropdown(false);
      openVncTab();
    };
  }
  const fsOption = $('ss-option-fastscreen');
  if (fsOption) {
    fsOption.onclick = (e) => {
      e.stopPropagation();
      _toggleDropdown(false);
      // 动态导入避免循环依赖
      import('./fastscreen.js').then(fs => {
        try { fs.openFastScreenTab(); } catch (err) { warn('vnc', 'openFastScreenTab failed: %s', err); }
      });
    };
  }

  // 点击页面其他区域关闭下拉菜单
  document.addEventListener('click', () => {
    if (_dropdownOpen) _toggleDropdown(false);
  });
  const btnStart = $('btn-vnc-start');
  if (btnStart) {
    btnStart.onclick = () => startVnc();
  }
  const btnStop = $('btn-vnc-stop');
  if (btnStop) {
    btnStop.onclick = () => stopVnc();
  }
  // 点击密码区域复制密码
  const infoPassword = $('vnc-info-password');
  if (infoPassword) {
    infoPassword.onclick = () => {
      if (state.vnc.password) {
        try {
          navigator.clipboard.writeText(state.vnc.password).then(() => {
            showToast(t('vnc.copySuccess'), 'success');
          }).catch(() => {
            showToast(t('vnc.copyFailed'), 'error');
          });
        } catch (e) {
          warn('vnc', 'clipboard write failed: %s', e);
        }
      }
    };
  }
}

/**
 * 初始化 VNC 视图（由 app.js 启动时调用）。
 * 请求初始状态以决定是否显示 VNC 入口按钮。
 */
export function initVncView() {
  bindVncEvents();
  initAutoHide();
  // 连接建立后请求 VNC 状态（由 wsClient 的 onopen 触发）
  // 这里先绑定 onWsOpen 钩子
  const prevOnOpen = window.__onWsOpen__;
  window.__onWsOpen__ = () => {
    if (typeof prevOnOpen === 'function') prevOnOpen();
    wsSend({ type: 'vnc_status' });
  };
}

// ── 屏幕共享下拉菜单 ──
// 点击电脑图标弹出下拉菜单，选择 VNC 连接 或 仅查看（FastScreen）。
// 自动隐藏逻辑已抽取到 autohide.js 共享模块。

let _dropdownOpen = false;

/** 显示/隐藏屏幕共享下拉菜单。 */
function _toggleDropdown(show) {
  const dropdown = $('screen-share-dropdown');
  if (!dropdown) return;
  _dropdownOpen = show;
  dropdown.style.display = show ? 'block' : 'none';
}

/**
 * 更新屏幕共享按钮和下拉选项的可见性。
 * VNC 和 FastScreen 各自的可用性决定对应选项是否显示；
 * 按钮本身在至少一个选项可用时显示。
 * 由 updateVncButtonVisibility() 和 fastscreen.js updateFastScreenButtonVisibility() 共同调用。
 */
export function updateScreenShareButtonVisibility() {
  const btn = $('btn-vnc-tab');
  if (!btn) return;
  const vncOption = $('ss-option-vnc');
  const fsOption = $('ss-option-fastscreen');

  // VNC 选项可见性：未禁用且 winvnc.exe 可用
  const vncAvailable = !state.vnc.disabled && state.vnc.winvncAvailable;
  // FastScreen 选项可见性：未禁用且 DLL 可用
  const fsAvailable = !state.fastscreen.disabled && state.fastscreen.available;

  if (vncOption) vncOption.style.display = vncAvailable ? 'flex' : 'none';
  if (fsOption) fsOption.style.display = fsAvailable ? 'flex' : 'none';

  // 按钮在至少一个选项可用时显示
  btn.style.display = (vncAvailable || fsAvailable) ? 'inline-flex' : 'none';
}

// ── 注册 VNC 会话 handler（模块加载时执行，由 import 副作用触发） ──
// handler 接口见 sessionHandlers.js。注册后 ui.js / messageHandlers.js 通过
// getHandlerBySid(sid) 分发，消除 if (sid === VNC_TAB_ID) 特判。
registerSessionHandler('vnc', {
  // 切换到 VNC frame（隐藏 terminal/fastscreen frame，显示 vnc-frame）
  switchTo: (sid) => switchToVncFrame(),
  // 关闭时类型特定清理：标记非活跃（VNC 进程不停止，共享桌面；iframe src 由 renderVncPanel 管理）
  close: (sid) => { state.sessions[sid].running = false; },
  // 构建 VNC tab DOM 元素
  buildTab: (sid) => buildVncTabElement(),
  // 页面刷新后恢复 VNC tab：重新请求状态
  restore: (sid) => { wsSend({ type: 'vnc_status' }); },
  // VNC 禁用时 tab 无效（参与 restoreTabs/handleSessionList 清理）
  isValid: (sid) => !state.vnc.disabled,
  // 打开 VNC tab
  open: (sid) => openVncTab(),
});

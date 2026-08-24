/**
 * 表现层：共享自动隐藏模块
 *
 * 统一管理 tab-bar 和工具条（VNC/FastScreen）的全屏自动隐藏行为。
 * 触发条件（全部满足）：对应 tab 激活 + 浏览器全屏 + sidebar 收起 + 画面区域聚焦
 * - 终端活跃会话：隐藏 tab-bar（无工具栏），要求终端容器获得焦点
 * - 终端历史会话：不隐藏（session.history=true 时跳过）
 * - VNC：不自动收起（工具条常驻）
 * - FastScreen：额外要求流已连接（state.fastscreen.connected）+ 画面区域获得焦点
 *
 * 画面区域聚焦条件：document.activeElement 落在当前激活 tab 对应的画面容器内。
 * - 终端：#terminal-container（xterm.js 内部 textarea 获得焦点）
 * - FastScreen：.fs-viewer-container（需 tabindex 才能聚焦，init 时自动添加）
 * 焦点变化通过 focusin/focusout（冒泡版）监听，触发 updateAutoHide 刷新。
 *
 * 效果：标签栏收起为 3px 细条，对应工具条高度归零；
 * 鼠标端：悬停顶部细条时展开，移开延时 1.5s 自动收起。
 * 触摸端：点击顶部细条（含 ::after 扩大触摸区）展开，点击终端区域延时收起。
 * 误触保护：收起状态下点击 tab-bar 内任何元素只展开不切换（capture 拦截）。
 *
 * 依赖：领域层 state / constants、基础设施 domUtils。
 */

import { state } from '../../domain/state.js';
import { $ } from '../../infrastructure/domUtils.js';
import { debug } from '../../domain/logger.js';

let _autoHideActive = false;    // 是否处于自动隐藏模式
let _autoHideCollapsed = false; // 当前是否已收起
let _autoHideTimer = null;      // 延时收起计时器
let _eventsBound = false;       // 事件是否已绑定（仅绑定一次）
let _contentFocused = false;    // 当前激活 tab 的画面区域是否有焦点
const AUTOHIDE_DELAY = 1500;    // 鼠标离开后延时收起毫秒数

/**
 * 检测当前是否为触摸设备。
 * 通过 body.touch-device class 判断（由 events.js 根据特征检测添加）。
 */
function _isTouchDevice() {
  return document.body.classList.contains('touch-device');
}

/**
 * 获取当前应操控的工具条元素 ID。
 * 根据当前激活 tab 的会话类型决定收起哪个工具条。
 * 终端会话返回 null（只隐藏 tab-bar，无工具栏）。
 */
function _getActiveToolbarId() {
  const type = state.sessions[state.activeTab]?.type;
  if (type === 'vnc') return 'vnc-toolbar';
  if (type === 'fastscreen') return 'fs-toolbar';
  return null;
}

/**
 * 检测当前激活 tab 的画面区域是否获得焦点。
 * 通过 document.activeElement 是否落在对应画面容器内判断。
 * - 终端：#terminal-container（xterm.js 内部 textarea 获得焦点）
 * - FastScreen：.fs-viewer-container（init 时添加 tabindex 使其可聚焦）
 * - VNC/其他：不参与自动隐藏，返回 false
 */
function _isContentFocused() {
  const uid = state.activeTab;
  const session = state.sessions[uid];
  if (!session) return false;
  const type = session.type;
  const active = document.activeElement;
  if (!active) return false;

  // 终端：activeElement 在 #terminal-container 内
  if (!type || type === 'terminal') {
    const container = $('terminal-container');
    return !!(container && container.contains(active));
  }
  // FastScreen：activeElement 在 .fs-viewer-container 内
  if (type === 'fastscreen') {
    const frame = $('fastscreen-frame');
    const viewer = frame ? frame.querySelector('.fs-viewer-container') : null;
    return !!(viewer && viewer.contains(active));
  }
  return false;
}

/**
 * 检测是否应进入自动隐藏模式。
 * 通用条件：全屏 + sidebar 收起 + 对应 tab 激活 + 画面区域聚焦。
 * - 终端活跃会话：隐藏（历史会话不隐藏），要求终端容器有焦点
 * - VNC：不自动收起（工具条需常驻）
 * - FastScreen：流已连接 + 画面区域有焦点
 */
function _shouldAutoHide() {
  if (!document.fullscreenElement || !state.sidebarCollapsed) return false;
  // 新增条件：画面区域必须获得焦点
  if (!_contentFocused) return false;
  const uid = state.activeTab;
  const session = state.sessions[uid];
  if (!session) return false;
  const type = session.type;

  // 终端会话：活跃会话隐藏，历史会话不隐藏
  if (!type || type === 'terminal') {
    return !session.history;
  }
  // VNC：不自动收起（工具条需常驻显示）
  if (type === 'vnc') {
    return false;
  }
  // FastScreen：连接后隐藏
  if (type === 'fastscreen') {
    return !!state.fastscreen && state.fastscreen.connected;
  }
  return false;
}

/** 展开：移除 collapsed class，清除计时器。 */
function _expandAutoHide() {
  clearTimeout(_autoHideTimer);
  if (!_autoHideCollapsed) return;
  _autoHideCollapsed = false;
  $('tab-bar').classList.remove('autohide-collapsed');
  const toolbarId = _getActiveToolbarId();
  const toolbar = toolbarId ? $(toolbarId) : null;
  if (toolbar) toolbar.classList.remove('autohide-collapsed');
  debug('autohide', 'expanded');
}

/** 收起：添加 collapsed class。 */
function _collapseAutoHide() {
  if (_autoHideCollapsed) return;
  _autoHideCollapsed = true;
  $('tab-bar').classList.add('autohide-collapsed');
  const toolbarId = _getActiveToolbarId();
  const toolbar = toolbarId ? $(toolbarId) : null;
  if (toolbar) toolbar.classList.add('autohide-collapsed');
  debug('autohide', 'collapsed');
}

/** 调度延时收起。 */
function _scheduleCollapse() {
  clearTimeout(_autoHideTimer);
  _autoHideTimer = setTimeout(() => {
    if (_autoHideActive && _shouldAutoHide()) {
      _collapseAutoHide();
    }
  }, AUTOHIDE_DELAY);
}

/**
 * 更新自动隐藏模式状态（进入/退出）。
 * 由各状态变化点调用：tab 切换、VNC 运行状态变化、全屏切换、sidebar 收起/展开、焦点变化。
 * 每次调用先刷新 _contentFocused（tab 切换后焦点状态可能已改变）。
 */
export function updateAutoHide() {
  // 刷新画面区域焦点状态（tab 切换等场景下 activeElement 可能已变化）
  _contentFocused = _isContentFocused();
  const should = _shouldAutoHide();
  if (should && !_autoHideActive) {
    _autoHideActive = true;
    _scheduleCollapse();
    debug('autohide', 'auto-hide mode activated');
  } else if (!should && _autoHideActive) {
    _autoHideActive = false;
    _expandAutoHide();
    debug('autohide', 'auto-hide mode deactivated');
  }
}

/**
 * 绑定自动隐藏相关 DOM 事件（仅绑定一次）。
 *
 * 鼠标端：
 * - tab-bar mouseenter/leave：展开/延时收起
 * - vnc-toolbar / fs-toolbar mouseenter/leave：取消/重新调度收起
 *
 * 触摸端：
 * - tab-bar click（capture）：收起时点击展开 + 延时自动收起（::after 伪元素扩大触摸区）
 * - vnc-toolbar / fs-toolbar click：展开时点击清除延时（保持展开）
 *
 * 通用（收起状态下误触保护）：
 * - tab-bar click（capture 阶段拦截）：收起时拦截 tab-bar 内所有点击，只展开不切换选项卡
 *
 * 通用：
 * - fullscreenchange：全屏切换时更新模式
 * - sidebar MutationObserver：sidebar 收起/展开时同步 state 并更新模式
 * - focusin/focusout：画面区域焦点变化时同步 _contentFocused 并更新模式
 */
export function initAutoHide() {
  if (_eventsBound) return;
  _eventsBound = true;

  // 给 FastScreen 画面容器添加 tabindex，使其可获得焦点。
  // 终端的 xterm.js 内部 textarea 自带 tabindex，无需处理。
  // VNC iframe 焦点在 iframe 内部（跨域不可检测），但 VNC 不参与自动隐藏。
  const fsViewer = $('fastscreen-frame')?.querySelector('.fs-viewer-container');
  if (fsViewer && !fsViewer.hasAttribute('tabindex')) {
    fsViewer.setAttribute('tabindex', '0');
  }

  const touch = _isTouchDevice();
  const tabBar = $('tab-bar');
  if (tabBar) {
    // 收起状态下拦截 tab-bar 内所有点击（capture 阶段，先于 target/bubble 执行），
    // 避免误触选项卡/关闭按钮等元素。只展开 autohide，不执行被点击元素的默认行为。
    tabBar.addEventListener('click', (e) => {
      if (_autoHideActive && _autoHideCollapsed) {
        e.stopPropagation();
        e.preventDefault();
        _expandAutoHide();
        _scheduleCollapse();
      }
    }, true);

    if (!touch) {
      // 鼠标端：悬停展开，移开延时收起（触摸端由 capture click 拦截器处理展开）
      tabBar.addEventListener('mouseenter', () => {
        if (_autoHideActive) _expandAutoHide();
      });
      tabBar.addEventListener('mouseleave', () => {
        if (_autoHideActive) _scheduleCollapse();
      });
    }
  }

  // 两个工具条都需要绑定
  for (const id of ['vnc-toolbar', 'fs-toolbar']) {
    const toolbar = $(id);
    if (toolbar) {
      if (touch) {
        // 触摸端：点击工具条清除延时（保持展开）
        toolbar.addEventListener('click', () => {
          if (_autoHideActive) clearTimeout(_autoHideTimer);
        });
      } else {
        // 鼠标端：从 tab-bar 移到工具条时不应误收起
        toolbar.addEventListener('mouseenter', () => {
          if (_autoHideActive) clearTimeout(_autoHideTimer);
        });
        toolbar.addEventListener('mouseleave', () => {
          if (_autoHideActive) _scheduleCollapse();
        });
      }
    }
  }

  // 浏览器全屏切换
  document.addEventListener('fullscreenchange', () => updateAutoHide());

  // sidebar 收起/展开（监听 class 属性变化，同步 state.sidebarCollapsed）
  const sidebar = $('sidebar');
  if (sidebar) {
    const observer = new MutationObserver(() => {
      state.sidebarCollapsed = sidebar.classList.contains('collapsed');
      updateAutoHide();
    });
    observer.observe(sidebar, { attributes: true, attributeFilter: ['class'] });
  }

  // 画面区域焦点变化（focusin/focusout 是冒泡版，绑定在 document 捕获所有焦点变化）
  // 焦点进入/离开画面区域时更新 _contentFocused 并刷新 autohide 状态
  document.addEventListener('focusin', () => {
    const focused = _isContentFocused();
    if (focused !== _contentFocused) {
      _contentFocused = focused;
      debug('autohide', 'focusin: contentFocused=%s', _contentFocused);
      updateAutoHide();
    }
  });
  document.addEventListener('focusout', () => {
    // focusout 时 activeElement 可能还未更新，用 setTimeout 延迟检查
    setTimeout(() => {
      const focused = _isContentFocused();
      if (focused !== _contentFocused) {
        _contentFocused = focused;
        debug('autohide', 'focusout: contentFocused=%s', _contentFocused);
        updateAutoHide();
      }
    }, 0);
  });
}

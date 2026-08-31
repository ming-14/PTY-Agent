/**
 * 表现层：主界面视图
 *
 * 负责标签栏、侧边栏、历史下拉、新建会话对话框等 DOM 渲染。
 * 依赖领域状态、基础设施的 DOM/传输/终端适配器。
 */

import { state, saveTabState, getSessionSizeConfigByUid, getUidBySid, getHistoryUidBySid } from '../../domain/state.js';
import { escHtml, escAttr } from '../../domain/formatters.js';
import { t } from '../../domain/i18n.js';
import { ICON_CLOSE } from '../../domain/constants.js';
import { $, showConfirm, hideConfirm } from '../../infrastructure/domUtils.js';
import { getHandlerBySid, removeTabAndSelectNext } from './sessionHandlers.js';
import { updateAutoHide } from './autohide.js';
import { wsSend, sendToSession } from '../../infrastructure/wsClient.js';
import {
  ensureTerminal,
  applyTerminalSize,
  applyTerminalFrameSize,
  replayPending,
  disposeTerminal,
  scrollTermToTop,
  applyReadonlyState,
  logCursorState,
  restartCursorBlinkIfNeeded,
  applySessionFrameRatio,
} from '../../infrastructure/terminalAdapter.js';
import { debug, info, warn } from '../../domain/logger.js';
import { formatRelativeTime, formatAbsoluteTime } from '../../domain/formatters.js';
import { onTabSwitch as rimeOnTabSwitch } from '../../infrastructure/rimeManager.js';
import { isSubprocessSession } from '../../infrastructure/terminal/shared.js';
import { getSizeStatusText, toggleSizeDropdown } from './sizeSelector.js';
// 通过 import 副作用触发 fastscreen/vnc/settings handler 注册（注册在模块底部立即执行）
import './fastscreen.js';
import './vnc.js';
import './settings.js';

export function updateStatusInfo(uid) {
  const s = state.sessions[uid];
  if (!s) return;
  // 非终端 tab（设置/VNC/FastScreen）不显示终端状态项（status-pty/status-size），
  // 它们的 switchTo*Frame 已负责隐藏；此处避免 WebSocket 消息回调（如会话列表更新）
  // 通过 updateStatusInfo(state.activeTab) 重新显示，覆盖隐藏设置
  if (getHandlerBySid(uid)) return;
  const ptyEl = $('status-pty');
  const sizeEl = $('status-size');
  if (state.activeTab !== uid) return;
  ptyEl.style.display = 'flex';
  ptyEl.textContent = formatPtyLabel(s);
  // 尺寸状态：PTY 与子进程模式均显示。
  // 子进程模式无后端 PTY，尺寸调整仅前端本地生效（term.resize 调整显示）。
  sizeEl.style.display = 'flex';
  sizeEl.textContent = getSizeStatusText(s);
  // 仅自适应模式高亮（蓝色 + "(自适应)" 标签提示正在自适应）；
  // 退出自适应后（fixed/custom/default）不高亮，避免蓝色文字干扰
  const cfg = getSessionSizeConfigByUid(uid);
  sizeEl.classList.toggle('size-active', !s.history && cfg.mode === 'adaptive');
}

export function formatPtyLabel(s) {
  let label = '';
  // 显示 PTY 标签：所有真实 PTY 后端（wezterm/conpty/win-sandbox 等）；
  // 子进程模式（subprocess）无终端、none（无后端）不显示
  if (s.ptyType && s.ptyType !== 'none' && s.ptyType !== 'subprocess') label = 'PTY';
  if (!s.running) label += t('session.endedBadge');
  return label;
}

/** 会话运行模式徽标：仅子进程模式展示，PTY（默认）不占用空间 */
export function modeBadge(s) {
  if (isSubprocessSession(s)) {
    return '<span class="mode-badge sub">' + t('session.subprocess') + '</span>';
  }
  return '';
}

/**
 * 显示空状态欢迎界面：隐藏所有 frame，显示 empty-state。
 * 由 closeTab / removeSessionTab 在无活跃标签时调用。
 */
function showEmptyState() {
  $('vnc-frame').style.display = 'none';
  $('fastscreen-frame').style.display = 'none';
  const settingsFrame = $('settings-frame');
  if (settingsFrame) settingsFrame.style.display = 'none';
  // 恢复 terminal-stage 的 padding（退出贴边模式，回到终端留白显示）
  $('terminal-stage').classList.remove('stage-flush');
  $('terminal-frame').style.display = 'none';
  $('empty-state').style.display = 'flex';
  $('status-pty').style.display = 'none';
  $('status-size').style.display = 'none';
  updateMouseModeButton(null);
}

export function switchTab(sid) {
  debug('ui', 'switchTab: %s → %s', state.activeTab, sid);
  // 统一在切换开始时移除贴边模式：若切到 settings/vnc/fastscreen，各自 switchTo 会重新加上
  $('terminal-stage').classList.remove('stage-flush');
  // 切换标签时关闭尺寸选择器下拉（避免下拉仍显示旧会话的配置）
  if (state.sizeSelectorVisible) toggleSizeDropdown(false);
  // 先清理所有终端实例的 active class，避免 dispose 后旧 div 仍显示导致 A/B 错位
  Object.values(state.termInstances).forEach(inst => {
    try { inst.div.classList.remove('active'); } catch (_) {}
  });

  // 切换前：如果当前 tab 是 handler tab（FastScreen/VNC），调用其 handler.close 清理资源
  // handler.close 只做类型特定清理（如 FastScreen 断开流+停止轮询），不做 tab 移除，
  // 可用作 deactivate 语义（切走时释放后台资源，避免流在后台持续运行）
  const prevSid = state.activeTab;
  if (prevSid && prevSid !== sid) {
    const prevHandler = getHandlerBySid(prevSid);
    if (prevHandler) {
      debug('ui', 'switchTab: deactivate prev handler tab sid=%s', prevSid);
      prevHandler.close(prevSid);
    }
  }

  state.activeTab = sid;
  saveTabState();

  // 统一 handler 分发：VNC/FastScreen 等特殊会话通过 handler 切换 frame
  // 消除原 if (sid === VNC_TAB_ID/FASTSCREEN_TAB_ID) 特判分支
  const handler = getHandlerBySid(sid);
  if (handler) {
    handler.switchTo(sid);
    renderTabs();
    renderSidebar();
    updateMouseModeButton(null);
    rimeOnTabSwitch();
    return;
  }

  // 终端会话路径
  // 从 VNC/FastScreen/Settings tab 切走时隐藏对应 frame（handler.switchTo 已互斥显示，这里仅兜底）
  $('vnc-frame').style.display = 'none';
  $('fastscreen-frame').style.display = 'none';
  const settingsFrame = $('settings-frame');
  if (settingsFrame) settingsFrame.style.display = 'none';
  // 退出自动隐藏模式（展开可能收起的标签栏/工具条）
  updateAutoHide();
  $('empty-state').style.display = 'none';
  $('terminal-frame').style.display = 'block';
  ensureTerminal(sid);
  const inst = state.termInstances[sid];
  const s = state.sessions[sid];
  const isHistory = !!(s && s.history);
  debug('ui', 'switchTab: ensureTerminal done sid=%s inst=%s div=%s', sid, !!inst, inst && !!inst.div);
  if (inst) {
    inst.div.classList.add('active');
    applyTerminalFrameSize(sid);
    // 切换标签时不强制向守护进程发送 resize（force=false）：
    // 各会话有独立 PTY，尺寸由创建时或上次用户操作决定，无需在切换时重复同步
    applyTerminalSize(sid, false);
    debug('ui', 'switchTab: frame display=%s div class=%s term rows=%s cols=%s',
          $('terminal-frame').style.display, inst.div.className, inst.term.rows, inst.term.cols);
    // 注意：cursorStyle/cursorBlink/cursorInactiveStyle 与构造函数默认值一致，
    // 不在切标签时重复赋值——options 变更会触发 xterm _handleOptionsChanged →
    // _fireOnCanvasResize（异步 rAF 视口刷新），在 renderer 已释放时抛
    // TypeError（reading 'device'）导致终端渲染/输入链挂死。历史会话的只读
    // 光标由 applyReadonlyState 的 \x1b[?25l 处理。
    logCursorState(sid);
    if (!isHistory) {
      restartCursorBlinkIfNeeded(sid);
    }
    applyReadonlyState(sid, isHistory);
    try {
      inst.term.write(isHistory ? '\x1b[?25l' : '\x1b[?25h');
    } catch (_) {}
    // visibility 切换后强制刷新画布，避免切回历史/其他标签时终端定格
    try {
      inst.term.refresh(0, inst.term.rows - 1);
    } catch (_) {}
    requestAnimationFrame(() => {
      debug('ui', 'switchTab: rAF replayPending sid=%s pendingReplay=%s pendingOutput=%d',
            sid, !!(s && s.pendingReplay), (s && s.pendingOutput) ? s.pendingOutput.length : 0);
      replayPending(sid);
      if (isHistory) {
        scrollTermToTop(inst.term);
      }
      // 用 rAF 在下一帧渲染前触发尺寸更新（比 setTimeout 更稳定）
      // 再等一帧确保 xterm 内部 dimensions 刷新后再算 frame
      requestAnimationFrame(() => {
        // 防御：单步失败不中断整条缩放链（任一异常都会导致 frame 保持
        // 未约束尺寸 → 终端框溢出 stage）
        try { applyTerminalFrameSize(sid); } catch (_) {}
        try { applyTerminalSize(sid, false); } catch (_) {}
        // 切换标签后按该会话保存的 frameRatio + 当前 stage 尺寸恢复框大小。
        // - adaptive 模式：按 ratio 设 frame 尺寸 + fit() 算 cols/rows（cols/rows 变）
        // - 非 adaptive 模式：按 ratio 反算字号（cols/rows 不变）
        // 不同会话 cols/rows 不同，ratio 不同，切换后需重新应用。
        try { applySessionFrameRatio(sid); } catch (_) {}
        try { inst.term.refresh(0, inst.term.rows - 1); } catch (_) {}
        if (isHistory) scrollTermToTop(inst.term);
      });
    });
  }
  renderTabs();
  renderSidebar();
  updateStatusInfo(sid);
  updateMouseModeButton(sid);
  rimeOnTabSwitch();
}

export function shouldShowMouseButton(sid) {
  const s = state.sessions[sid];
  if (!s) return false;
  if (s.history) return false;
  if (isSubprocessSession(s)) return false;
  return true;
}

export function updateMouseModeButton(sid) {
  const btn = $('btn-mouse-mode');
  if (!btn) return;
  const s = state.sessions[sid];
  if (!s || s.history || isSubprocessSession(s)) {
    btn.style.display = 'none';
    return;
  }
  const inst = state.termInstances[sid];
  const appMouseActive = !!(inst && inst.appMouseMode);
  if (!appMouseActive) {
    btn.style.display = 'none';
    return;
  }
  btn.style.display = 'flex';
  const override = inst ? inst.mouseInputOverride : true;
  btn.classList.toggle('active', override);
  btn.title = override
    ? t('mouseMode.titleOn')
    : t('mouseMode.titleOff');
}

export function openSessionInTab(sid) {
  // 统一 handler 分发：VNC/FastScreen 等特殊会话通过 handler 打开
  // 消除原 if (sid === VNC_TAB_ID/FASTSCREEN_TAB_ID) 特判分支
  const handler = getHandlerBySid(sid);
  if (handler) {
    handler.open(sid);
    return;
  }
  let s = state.sessions[sid];
  const inHistory = !!state.history[sid];
  const isAlreadyActive = state.activeTab === sid;
  // 用户主动重新打开：清除"已关闭"标记，恢复自动加入 tabOrder 语义
  state.closedTabs.delete(sid);
  debug('session',
        'openSessionInTab START sid=%s inSessions=%s history=%s inHistory=%s tabOrder=%o activeTab=%s',
        sid, !!s, s && s.history, inHistory, state.tabOrder, state.activeTab);

  // 1. 确保会话在 state.sessions 中（历史会话可能仅在 state.history 里）
  if (!s) {
    s = state.history[sid];
    if (!s) {
      warn('session', 'openSessionInTab: sid=%s not found in sessions/history', sid);
      return;
    }
    state.sessions[sid] = { ...s, history: true, subscribed: false };
    debug('session', 'openSessionInTab: opened history sid=%s', sid);
  } else if (inHistory) {
    // 已结束并进入历史的会话可能仍留在 state.sessions 中，需修正标记
    s.history = true;
    s.running = false;
    s.subscribed = false;
  }

  // 2. 确保会话在 tabOrder 中
  if (!state.tabOrder.includes(sid)) {
    state.tabOrder.push(sid);
    saveTabState();
  }

  // 3. 已是当前活动标签且状态正常：仅刷新显示，不重复订阅/请求
  //    （避免点击已活动标签时无谓地重新订阅，造成画面闪烁）
  if (isAlreadyActive && (state.sessions[sid].subscribed || state.sessions[sid].history)) {
    debug('session', 'openSessionInTab: sid=%s already active, skip resubscribe', sid);
    switchTab(sid);
    state.pendingSwitch = null;
    return;
  }

  // 已订阅的活跃会话切回时不再重新 subscribe
  // 后端支持多订阅，订阅状态保留，xterm.js 实例持续接收输出累积 scrollback
  // 只有"首次打开"（未订阅）的会话才需要 subscribe
  if (state.sessions[sid].subscribed && !state.sessions[sid].history) {
    debug('session', 'openSessionInTab: sid=%s already subscribed, switch only', sid);
    switchTab(sid);
    state.pendingSwitch = null;
    return;
  }

  // 4. 立即切换标签（创建终端如有需要，给用户即时高亮/标签反馈）
  //    不再 dispose 旧终端：xterm.js 隐藏后的渲染问题通过 switchTab 中的
  //    refresh + requestAnimationFrame 解决。保留终端实例可避免 dispose/recreate
  //    造成的画面闪烁与"切回后终端不切换"问题。
  // 已订阅会话不再 term.clear()+write()，scrollback 完整保留；
  //    只有首次订阅的 replayPending 会 write(snapshot) 初始化 xterm。
  state.pendingSwitch = sid;
  switchTab(sid);

  // 5. 根据会话类型请求最新数据
  if (state.sessions[sid].history) {
    // 历史会话：请求回放数据（每次切回都重新请求，确保内容新鲜）
    debug('session', 'openSessionInTab: request history_detail sid=%s', sid);
    sendToSession(sid, { type: 'history_detail' });
  } else if (state.sessions[sid].running) {
    // 活跃会话：必须重新订阅
    // （后端每个 WS 连接只维护一个订阅，切到其他会话后本会话订阅即失效）
    debug('session', 'openSessionInTab: subscribe sid=%s', sid);
    state.sessions[sid].subscribed = false;
    sendToSession(sid, { type: 'subscribe' });
  } else {
    // 已结束但未进入历史：无需请求
    state.pendingSwitch = null;
  }
}

export function closeTab(sid) {
  // 统一 handler 分发：VNC/FastScreen 等特殊会话通过 handler 关闭
  // 消除原 if (sid === VNC_TAB_ID/FASTSCREEN_TAB_ID) 特判分支
  const handler = getHandlerBySid(sid);
  if (handler) {
    debug('session', 'closeTab handler sid=%s', sid);
    // 1. 类型特定清理（如 FastScreen 断开流+停止轮询）
    handler.close(sid);
    // 2. 统一 tab 移除 + 选择 nextTab
    const nextTab = removeTabAndSelectNext(sid, null);
    saveTabState();
    // 3. 切换到 nextTab
    if (nextTab) {
      const nextHandler = getHandlerBySid(nextTab);
      if (nextHandler) {
        // 下一个也是 handler tab（如 VNC）：直接切换 frame
        nextHandler.switchTo(nextTab);
      } else {
        // 终端会话：通过 openSessionInTab 处理订阅/切换
        openSessionInTab(nextTab);
      }
    } else if (state.activeTab === null) {
      showEmptyState();
    }
    renderTabs();
    renderSidebar();
    renderHistoryDropdown();
    return;
  }
  const s = state.sessions[sid];
  if (!s) return;
  debug('session', 'closeTab sid=%s history=%s running=%s subscribed=%s', sid, s.history, s.running, s.subscribed);
  sendToSession(sid, { type: 'unsubscribe' });

  // 关闭标签时：活跃会话保留在左侧边栏，仅取消订阅并移除标签；历史/已结束会话从状态移除
  if (s.history || !s.running) {
    removeSessionTab(sid, true);
    return;
  }

  // 取消订阅后必须同步标记 subscribed=false，否则再次打开标签时会话对象仍认为已订阅，
  // 导致 openSessionInTab 不再发送 subscribe，后端不再路由 output，终端定格。
  s.subscribed = false;
  disposeTerminal(sid);
  const idx = state.tabOrder.indexOf(sid);
  if (idx >= 0) state.tabOrder.splice(idx, 1);
  saveTabState();

  // 提前更新 activeTab 到下一个标签，确保关闭后立即迁移高亮（含手机端字母左栏）
  let nextTab = null;
  if (state.activeTab === sid) {
    if (state.tabOrder.length > 0) {
      nextTab = state.tabOrder[state.tabOrder.length - 1];
      state.activeTab = nextTab;
    } else {
      state.activeTab = null;
    }
    saveTabState();
  }

  renderTabs();
  renderSidebar();
  renderHistoryDropdown();

  if (nextTab) {
    state.pendingSwitch = nextTab;
    const ns = state.sessions[nextTab];
    if (ns && ns.history) {
      if (state.termInstances[nextTab]) {
        debug('session', 'closeTab: dispose stale history terminal next=%s', nextTab);
        disposeTerminal(nextTab);
      }
      sendToSession(nextTab, { type: 'history_detail' });
    } else if (ns && !ns.subscribed && ns.running) {
      sendToSession(nextTab, { type: 'subscribe' });
    } else {
      state.pendingSwitch = null;
      switchTab(nextTab);
    }
  } else if (state.activeTab === null) {
    showEmptyState();
  }
}

export function killSession(sid) {
  const s = state.sessions[sid];
  if (!s) return;
  s.closing = true;
  info('session', 'killSession sid=%s (closing=true, send kill)', sid);
  sendToSession(sid, { type: 'kill' });
}

export function removeSessionTab(sid, render) {
  // 统一 handler 分发：VNC/FastScreen 等特殊会话通过 handler 关闭
  // 消除原 if (sid === VNC_TAB_ID/FASTSCREEN_TAB_ID) 特判分支
  const handler = getHandlerBySid(sid);
  if (handler) {
    debug('session', 'removeSessionTab handler sid=%s render=%s', sid, render);
    handler.close(sid);
    const nextTab = removeTabAndSelectNext(sid, null);
    saveTabState();
    if (nextTab) {
      const nextHandler = getHandlerBySid(nextTab);
      if (nextHandler) {
        nextHandler.switchTo(nextTab);
      } else {
        openSessionInTab(nextTab);
      }
    } else if (state.activeTab === null) {
      showEmptyState();
    }
    if (render) {
      renderTabs();
      renderSidebar();
      renderHistoryDropdown();
    }
    return;
  }
  debug('session', 'removeSessionTab sid=%s render=%s', sid, render);
  disposeTerminal(sid);
  delete state.sessions[sid];
  // 标记用户已关闭：阻止关闭后到达的 subscribe/history_detail 响应把会话
  // 重新加回 tabOrder（快速连续关闭时标签"复活"的竞态）
  state.closedTabs.add(sid);
  // 清除残留的 pendingSwitch：否则晚到的响应走 pendingSwitch 分支重新激活
  if (state.pendingSwitch === sid) state.pendingSwitch = null;
  const idx = state.tabOrder.indexOf(sid);
  if (idx >= 0) state.tabOrder.splice(idx, 1);

  // 提前更新 activeTab 到下一个标签，确保关闭后立即迁移高亮（含手机端字母左栏）
  let nextTab = null;
  if (state.activeTab === sid) {
    if (state.tabOrder.length > 0) {
      nextTab = state.tabOrder[state.tabOrder.length - 1];
      state.activeTab = nextTab;
    } else {
      state.activeTab = null;
    }
  }
  saveTabState();

  if (nextTab) {
    state.pendingSwitch = nextTab;
    const ns = state.sessions[nextTab];
    if (ns && ns.history) {
      // 切换到的历史会话若已有终端实例，先销毁，避免旧 canvas/缩放状态导致排版错乱
      if (state.termInstances[nextTab]) {
        debug('session', 'removeSessionTab: dispose stale history terminal next=%s', nextTab);
        disposeTerminal(nextTab);
      }
      sendToSession(nextTab, { type: 'history_detail' });
    } else if (ns && !ns.subscribed && ns.running) {
      sendToSession(nextTab, { type: 'subscribe' });
    } else {
      state.pendingSwitch = null;
      switchTab(nextTab);
    }
  } else if (state.activeTab === null) {
    showEmptyState();
  }

  if (render) {
    renderTabs();
    renderSidebar();
    renderHistoryDropdown();
  }
}

export function renderTabs() {
  const scroll = $('tab-scroll');
  scroll.innerHTML = '';
  state.tabOrder.forEach(key => {
    // 统一 handler 分发：VNC/FastScreen 等特殊会话通过 handler 构建 tab 元素
    // 消除原 if (key === VNC_TAB_ID/FASTSCREEN_TAB_ID) 特判分支
    const handler = getHandlerBySid(key);
    if (handler) {
      const tab = handler.buildTab(key);
      if (tab) scroll.appendChild(tab);
      return;
    }
    const s = state.sessions[key];
    if (!s) return;
    const tab = document.createElement('div');
    tab.className = 'tab' + (state.activeTab === key ? ' active' : '');
    tab.innerHTML =
      '<span class="tab-icon ' + (s.running ? 'running' : 'ended') + '"></span>' +
      '<span class="tab-title" title="' + escHtml(s.command || s.id) + '">' + escHtml(s.id) +
      modeBadge(s) + '</span>' +
      '<span class="tab-close" data-uid="' + escHtml(key) + '" title="' + t('common.closeTab') + '">' + ICON_CLOSE + '</span>';
    tab.onclick = e => {
      if (e.target.closest('.tab-close')) return;
      openSessionInTab(key);
    };
    tab.oncontextmenu = e => {
      e.preventDefault();
      showContextMenu(e, key, 'tab');
    };
    const closeBtn = tab.querySelector('.tab-close');
    closeBtn.onclick = e => {
      e.stopPropagation();
      closeTab(closeBtn.dataset.uid);
    };
    scroll.appendChild(tab);
  });
}

export function renderSidebar(animateKey) {
  const activeList = $('sidebar-active');
  const historyList = $('sidebar-history');

  const oldItems = Array.from(document.querySelectorAll('.sidebar-item'));
  const oldRects = new Map();
  oldItems.forEach(el => oldRects.set(el.dataset.uid, el.getBoundingClientRect()));

  activeList.innerHTML = '';
  historyList.innerHTML = '';

  // 排除 handler 会话（FastScreen/VNC）：它们通过标签栏入口操作，不进入侧边栏列表
  Object.entries(state.sessions)
    .filter(([key, s]) => !s.history && s.running && !getHandlerBySid(key))
    .forEach(([key, s]) => activeList.appendChild(buildSidebarItem(s, key)));

  const histEntries = Object.entries(state.history).sort((a, b) => (b[1].endTime || 0) - (a[1].endTime || 0));
  histEntries.forEach(([key, h]) => historyList.appendChild(buildSidebarItem({ ...h, history: true }, key)));

  if (animateKey) {
    const oldRect = oldRects.get(animateKey);
    const newEl = document.querySelector('.sidebar-item[data-uid="' + escAttr(animateKey) + '"]');
    if (oldRect && newEl) {
      const newRect = newEl.getBoundingClientRect();
      const dx = oldRect.left - newRect.left;
      const dy = oldRect.top - newRect.top;
      if (dx !== 0 || dy !== 0) {
        newEl.classList.add('flip-move');
        newEl.style.transform = 'translate(' + dx + 'px, ' + dy + 'px)';
        requestAnimationFrame(() => {
          newEl.style.transform = '';
          setTimeout(() => {
            newEl.classList.remove('flip-move');
            newEl.style.transform = '';
          }, 260);
        });
      }
    }
  }
}

export function buildSidebarItem(s, key) {
  const item = document.createElement('div');
  item.className = 'sidebar-item' + (state.activeTab === key ? ' active' : '');
  item.dataset.uid = key;
  item.dataset.running = s.running ? 'true' : 'false';
  let timeHtml = '';
  if (s.running && s.startTime) {
    timeHtml = '<div class="sidebar-item-time">' + escHtml(formatRelativeTime(s.startTime)) + '</div>';
  } else if (!s.running && (s.startTime || s.endTime)) {
    const ts = s.endTime || s.startTime;
    timeHtml = '<div class="sidebar-item-time">' + escHtml(formatAbsoluteTime(ts)) + '</div>';
  }
  const initial = escHtml((s.id || '?').charAt(0).toUpperCase());
  item.innerHTML =
    '<span class="sidebar-item-icon ' + (s.running ? 'running' : 'ended') + '"></span>' +
    '<span class="sidebar-item-initial">' + initial + '</span>' +
    '<div class="sidebar-item-text">' +
      '<div class="sidebar-item-label">' + escHtml(s.id) + modeBadge(s) + '</div>' +
      '<div class="sidebar-item-cmd">' + escHtml(s.command || '') + '</div>' +
      timeHtml +
    '</div>';
  item.onclick = () => openSessionInTab(key);
  item.oncontextmenu = e => {
    e.preventDefault();
    const context = s.history ? 'history-session' : 'active-session';
    showContextMenu(e, key, context);
  };
  return item;
}

export function renderHistoryDropdown() {
  const dd = $('history-dropdown');
  const histEntries = Object.entries(state.history).sort((a, b) => (b[1].endTime || 0) - (a[1].endTime || 0));
  if (histEntries.length === 0) {
    dd.innerHTML = '<div class="history-empty">' + t('session.noHistory') + '</div>';
    return;
  }
  dd.innerHTML = '';
  histEntries.forEach(([key, s]) => {
    const item = document.createElement('div');
    item.className = 'history-item';
    const opened = !!state.sessions[key];
    if (opened) item.classList.add('opened');
    item.innerHTML =
      '<span class="history-item-icon"></span>' +
      '<span class="history-item-info">' +
        '<div class="history-item-id">' + escHtml(s.id) + '</div>' +
        '<div class="history-item-cmd" title="' + escHtml(s.command || '') + '">' + escHtml(s.command || '') + '</div>' +
      '</span>' +
      '<span class="history-item-delete" data-uid="' + escHtml(key) + '" title="' + t('session.deleteHistory') + '">' + ICON_CLOSE + '</span>';
    item.onclick = e => {
      if (e.target.closest('.history-item-delete')) return;
      openSessionInTab(key);
      toggleHistory(false);
    };
    const delBtn = item.querySelector('.history-item-delete');
    if (delBtn) {
      delBtn.onclick = e => {
        e.stopPropagation();
        const delKey = delBtn.dataset.uid;
        sendToSession(delKey, { type: 'delete_history' });
        delete state.history[delKey];
        if (state.sessions[delKey] && state.sessions[delKey].history) {
          removeSessionTab(delKey, false);
        }
        renderHistoryDropdown();
        renderTabs();
        renderSidebar();
      };
    }
    dd.appendChild(item);
  });
}

export function positionHistoryDropdown() {
  const dd = $('history-dropdown');
  const btn = $('btn-history');
  const rect = btn.getBoundingClientRect();
  const ddRect = dd.getBoundingClientRect();
  let left = rect.left + rect.width - ddRect.width;
  let top = rect.bottom + 4;
  if (left < 8) left = 8;
  if (left + ddRect.width > window.innerWidth - 8) left = window.innerWidth - ddRect.width - 8;
  if (top + ddRect.height > window.innerHeight - 8) top = rect.top - ddRect.height - 4;
  dd.style.left = left + 'px';
  dd.style.top = top + 'px';
}

export function toggleHistory(show) {
  const dd = $('history-dropdown');
  if (show === undefined) show = dd.style.display === 'none';
  if (show) {
    wsSend({ type: 'history' });
    dd.style.display = 'block';
    state.historyVisible = true;
    requestAnimationFrame(positionHistoryDropdown);
  } else {
    dd.style.display = 'none';
    state.historyVisible = false;
  }
}

export function showNewSessionDialog() {
  $('dialog-overlay').style.display = 'flex';
  $('form-id').value = '';
  const cmdEl = $('form-command');
  cmdEl.value = '';
  // 工作目录默认填入守护进程工作目录
  const cwdEl = $('form-cwd');
  if (cwdEl) cwdEl.value = state.daemonCwd || '';
  // 重置 shell 选择：清除选择、按钮显示回默认文案
  const picker = $('btn-shell-picker');
  if (picker) {
    delete picker.dataset.shell;
    const label = $('shell-picker-label');
    if (label) label.textContent = t('session.shellNone');
  }
  const dd = $('shell-dropdown');
  if (dd) dd.style.display = 'none';
  const dirDd = $('dir-dropdown');
  if (dirDd) dirDd.style.display = 'none';
  setTimeout(() => cmdEl.focus(), 0);
}

export function submitNewSession() {
  const cmdEl = $('form-command');
  const command = (cmdEl.value || '').trim();
  const shell = getSelectedShell();
  // 未选 shell 时命令必填；选了 shell 时命令为空则启动该 shell 本身
  if (!command && !shell) {
    cmdEl.focus();
    return;
  }
  let sid = $('form-id').value.trim();
  if (!sid) sid = 's' + Date.now().toString(36);
  const msg = {
    type: 'create',
    session_id: sid,
    command: command || shell,
  };
  // 有 shell 选择且命令非空：daemon 用 wrap_command 包装（选 shell 但命令空 = 启动交互式 shell 本身，不传 shell）
  if (shell && command) {
    msg.shell = shell;
  }
  if ($('form-cwd').value.trim()) msg.cwd = $('form-cwd').value.trim();

  info('session', 'create sid=%s cmd=%s shell=%s', sid, msg.command, shell || '(none)');
  state.pendingCreates.add(sid);
  state.sessions[sid] = {
    id: sid,
    command: msg.command,
    running: true,
    subscribed: false,
    history: false,
    ptyType: 'conpty',
    pendingOutput: [],
    pendingSnapshot: null,
    startTime: Date.now() / 1000,
  };
  if (!state.tabOrder.includes(sid)) state.tabOrder.push(sid);
  saveTabState();
  renderTabs();
  renderSidebar();
  switchTab(sid);
  wsSend(msg);
  $('dialog-overlay').style.display = 'none';
}

// ── Shell 选择器（新建会话对话框）──
// 数据源：state.availableShells（守护进程 detect_available_shells 探测，
// 经 shell_list 消息 + localStorage 缓存注入），{ 名称: 路径 } 字典。
// 选中 shell 后把 shell 名填入命令输入框（daemon 会 which 解析路径）。

export function toggleShellDropdown() {
  const dd = $('shell-dropdown');
  if (!dd) return;
  if (dd.style.display === 'block') {
    dd.style.display = 'none';
    return;
  }
  const shells = state.availableShells || {};
  const names = Object.keys(shells).filter(n => shells[n]);
  if (names.length === 0) {
    dd.style.display = 'none';
    return;
  }
  dd.innerHTML = names.map(name =>
    '<div class="shell-dropdown-item" data-shell="' + escAttr(name) + '">' +
      '<span class="shell-item-name">' + escHtml(name) + '</span>' +
      '<span class="shell-item-path">' + escHtml(shells[name] || '') + '</span>' +
    '</div>'
  ).join('');
  dd.style.display = 'block';
  debug('ui', 'shell dropdown shown: %d shells', names.length);
}

export function hideShellDropdown() {
  const dd = $('shell-dropdown');
  if (dd) dd.style.display = 'none';
}

/** 选中 shell：记录选择并更新按钮显示（不填命令框，创建时经 shell 字段由 daemon wrap） */
export function selectShell(name) {
  const picker = $('btn-shell-picker');
  if (picker) {
    picker.dataset.shell = name;
    const label = $('shell-picker-label');
    if (label) label.textContent = name;
  }
  hideShellDropdown();
  debug('ui', 'shell selected: %s', name);
}

/** 读取当前新建会话对话框选中的 shell（未选返回空串） */
export function getSelectedShell() {
  const picker = $('btn-shell-picker');
  return picker && picker.dataset.shell ? picker.dataset.shell : '';
}

// ── 工作目录自动补全（新建会话对话框）──
// 数据源：GET /api/listdir?path=<父目录>（守护进程列目录），
// 前端按输入前缀过滤，选中后填入路径并继续列出该目录。

let _dirListSeq = 0; // 请求序号：防抖期间旧响应晚到丢弃

/**
 * 从输入值拆出父目录与输入前缀：
 * - "C:/Users/ri" → { dir: "C:/Users", prefix: "ri" }
 * - "C:/Users/"   → { dir: "C:/Users/", prefix: "" }
 * - "/opt/"       → { dir: "/opt/", prefix: "" }
 * - "C:/"         → { dir: "C:/", prefix: "" }（保留尾分隔符：Windows 下
 *   裸盘符 "C:" 解析为 C 盘当前目录而非根目录，必须带斜杠才列根）
 * - "/"           → { dir: "/", prefix: "" }
 */
function splitDirInput(value) {
  let v = (value || '').trim();
  if (!v) return null;
  const sepIdx = Math.max(v.lastIndexOf('/'), v.lastIndexOf('\\'));
  if (sepIdx < 0) return { dir: '', prefix: v };
  // 以分隔符结尾：整个输入都是父目录（前缀为空），尾分隔符保留
  if (sepIdx === v.length - 1) {
    return { dir: v, prefix: '' };
  }
  const dir = v.slice(0, sepIdx) || v.slice(0, 1); // 根 "/" 时 dir 保持
  const prefix = v.slice(sepIdx + 1);
  return { dir, prefix };
}

/** 定位目录下拉到工作目录输入框下方（body 级浮层） */
function positionDirDropdown() {
  const dd = $('dir-dropdown');
  const cwdEl = $('form-cwd');
  if (!dd || !cwdEl) return;
  const rect = cwdEl.getBoundingClientRect();
  const maxLeft = window.innerWidth - dd.offsetWidth - 4;
  dd.style.left = Math.max(4, Math.min(rect.left, maxLeft)) + 'px';
  dd.style.top = (rect.bottom + 4) + 'px';
}

/**
 * 根据当前输入触发目录列出（输入防抖 / 按钮点击共用）。
 * 无前缀时列出父目录全部子目录；有前缀时列出父目录并按前缀过滤。
 */
export function refreshDirDropdown() {
  const cwdEl = $('form-cwd');
  const dd = $('dir-dropdown');
  if (!cwdEl || !dd) return;
  const value = cwdEl.value || '';
  const parts = splitDirInput(value);
  if (!parts || !parts.dir) {
    dd.style.display = 'none';
    return;
  }
  const seq = ++_dirListSeq;
  const url = '/api/listdir?path=' + encodeURIComponent(parts.dir);
  fetch(url, { headers: { 'Accept': 'application/json' }, credentials: 'include' })
    .then(r => r.json())
    .then(data => {
      if (seq !== _dirListSeq) return; // 过期响应丢弃
      const dirs = (data && Array.isArray(data.directories)) ? data.directories : [];
      // 前缀过滤（大小写不敏感；空前缀 = 全列）
      const prefix = parts.prefix.toLowerCase();
      const filtered = prefix ? dirs.filter(d => d.toLowerCase().startsWith(prefix)) : dirs;
      if (filtered.length === 0) {
        dd.style.display = 'none';
        return;
      }
      dd.innerHTML = filtered.map(name =>
        '<div class="shell-dropdown-item" data-dir="' + escAttr(name) + '">' +
          '<span class="shell-item-name">' + escHtml(name) + '</span>' +
        '</div>'
      ).join('');
      dd.style.display = 'block';
      positionDirDropdown();
      debug('ui', 'dir dropdown: dir=%s prefix=%s shown=%d', parts.dir, parts.prefix, filtered.length);
    })
    .catch(() => {
      if (seq === _dirListSeq) dd.style.display = 'none';
    });
}

export function hideDirDropdown() {
  const dd = $('dir-dropdown');
  if (dd) dd.style.display = 'none';
}

/** 选中目录：替换输入前缀为选中目录名（带尾分隔符），并继续列出该目录 */
export function selectDir(name) {
  const cwdEl = $('form-cwd');
  if (!cwdEl) return;
  const value = cwdEl.value || '';
  // 提取父目录（最后一个分隔符之前的部分，含分隔符）
  const sepIdx = Math.max(value.lastIndexOf('/'), value.lastIndexOf('\\'));
  const base = sepIdx >= 0 ? value.slice(0, sepIdx + 1) : '';
  // 尾分隔符：refreshDirDropdown 据此列出选中目录内部（而非把目录名当输入前缀）
  const sep = base.includes('/') ? '/' : '\\';
  cwdEl.value = base + name + sep;
  debug('ui', 'dir selected: %s', cwdEl.value);
  refreshDirDropdown(); // 继续列出选中目录的子目录
}

export function showContextMenu(e, sid, context) {
  e.preventDefault();
  state.contextMenuTarget = sid;
  state.contextMenuContext = context;
  debug('ui', 'showContextMenu sid=%s context=%s at(%d,%d)', sid, context, e.clientX, e.clientY);
  const menu = $('context-menu');
  menu.innerHTML = '';
  if (context === 'tab') {
    menu.innerHTML =
      '<div class="context-menu-item" data-action="detail">' + t('session.detail') + '</div>' +
      '<div class="context-menu-item" data-action="close-tab">' + t('common.closeTab') + '</div>';
  } else if (context === 'active-session') {
    menu.innerHTML =
      '<div class="context-menu-item" data-action="detail">' + t('session.detail') + '</div>' +
      '<div class="context-menu-item danger" data-action="close-session">' + t('session.closeSession') + '</div>';
  } else if (context === 'history-session') {
    menu.innerHTML =
      '<div class="context-menu-item" data-action="detail">' + t('session.detail') + '</div>' +
      '<div class="context-menu-item" data-action="restart-session">' + t('session.restart') + '</div>' +
      '<div class="context-menu-item danger" data-action="delete-session">' + t('session.delete') + '</div>';
  }
  menu.style.display = 'block';
  const menuRect = menu.getBoundingClientRect();
  let left = e.clientX;
  let top = e.clientY;
  if (left + menuRect.width > window.innerWidth - 8) left = window.innerWidth - menuRect.width - 8;
  if (top + menuRect.height > window.innerHeight - 8) top = window.innerHeight - menuRect.height - 8;
  menu.style.left = left + 'px';
  menu.style.top = top + 'px';
}

export function hideContextMenu() {
  $('context-menu').style.display = 'none';
  state.contextMenuTarget = null;
  state.contextMenuContext = null;
}

export function showRestartDialog(uid) {
  const h = state.history[uid];
  if (!h) return;
  state.restartTargetSid = uid;
  $('restart-body').textContent = t('session.restartBody', { sid: h.id });
  $('restart-reassign-sid').checked = true;
  $('restart-sid-group').style.display = 'none';
  $('restart-sid-input').value = h.id;
  $('restart-sid-hint').style.display = 'none';
  $('restart-sid-input').style.borderColor = '';
  $('restart-overlay').style.display = 'flex';
}

export function hideRestartDialog() {
  $('restart-overlay').style.display = 'none';
  state.restartTargetSid = null;
}

export function checkRestartSidConflict() {
  const input = $('restart-sid-input');
  const hint = $('restart-sid-hint');
  const sid = (input.value || '').trim();
  if (!sid) {
    hint.textContent = t('session.sidEmpty');
    hint.style.display = 'block';
    hint.style.color = '#d13438';
    input.style.borderColor = '#d13438';
    return 'empty';
  }
  // 冲突检查基于展示名（sid）：活跃/历史会话均按展示名反查
  const activeUid = getUidBySid(sid);
  if (activeUid) {
    const activeSession = state.sessions[activeUid];
    if (activeSession && !activeSession.history && activeSession.running) {
      hint.textContent = t('session.sidActiveConflict');
      hint.style.display = 'block';
      hint.style.color = '#d13438';
      input.style.borderColor = '#d13438';
      return 'active-conflict';
    }
  }
  const historyUid = getHistoryUidBySid(sid);
  if (historyUid && historyUid !== state.restartTargetSid) {
    hint.textContent = t('session.sidHistoryConflict');
    hint.style.display = 'block';
    hint.style.color = 'var(--wt-tab-text-muted)';
    input.style.borderColor = '';
    return 'history-conflict';
  }
  hint.style.display = 'none';
  input.style.borderColor = '';
  return 'ok';
}

export function submitRestartSession() {
  const origUid = state.restartTargetSid;
  if (!origUid) return;
  const h = state.history[origUid];
  if (!h) { hideRestartDialog(); return; }

  const reassign = $('restart-reassign-sid').checked;
  let sid;
  if (reassign) {
    sid = 's' + Date.now().toString(36);
  } else {
    sid = ($('restart-sid-input').value || '').trim();
    const conflict = checkRestartSidConflict();
    if (conflict === 'empty' || conflict === 'active-conflict') return;
  }

  if (sid !== h.id) {
    const historyUid = getHistoryUidBySid(sid);
    if (historyUid) {
      sendToSession(historyUid, { type: 'delete_history' });
      delete state.history[historyUid];
    }
  }

  const msg = {
    type: 'create',
    session_id: sid,
    command: h.command,
  };
  info('session', 'restart origUid=%s newSid=%s cmd=%s', origUid, sid, h.command);
  state.pendingCreates.add(sid);
  state.sessions[sid] = {
    id: sid,
    uid: '',
    command: h.command,
    running: true,
    subscribed: false,
    history: false,
    ptyType: 'conpty',
    pendingOutput: [],
    pendingSnapshot: null,
    startTime: Date.now() / 1000,
  };
  if (!state.tabOrder.includes(sid)) state.tabOrder.push(sid);
  saveTabState();
  renderTabs();
  renderSidebar();
  switchTab(sid);
  wsSend(msg);
  hideRestartDialog();
}

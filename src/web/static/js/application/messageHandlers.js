/**
 * 应用层：WebSocket 消息处理器 / 会话用例编排
 *
 * 负责解析后端消息、更新领域状态（state）、并调用端口完成终端输出/UI渲染。
 * 不直接依赖基础设施或表现层，所有外部操作通过 application/ports.js 注入。
 *
 * 所有消息按会话 uid 路由（入站 resolveMsgUid 优先 sessionUid，
 * 否则经 sessionId/session_id/id 反查 uid）；出站通过 sendToSession 自动填充
 * sessionUid（路由键）+ sessionId（展示名）。
 */

import { state, saveTabState, getSessionSizeConfigByUid, setSessionSizeConfig, setLocalAdaptiveOwner, isLocalAdaptiveOwner, resolveMsgUid, getUidBySid } from '../domain/state.js';
import { debug, info, warn, error } from '../domain/logger.js';
import { DEFAULT_COLS, DEFAULT_ROWS } from '../domain/constants.js';
import { t, i18nError } from '../domain/i18n.js';
import { ports } from './ports.js';
import { wsSend, sendToSession } from '../infrastructure/wsClient.js';

// ── 乐观创建键迁移助手 ──────────────────────────────────────
// submitNewSession/submitRestartSession 先用用户自定义 sid 作为临时键
// 在 state.sessions/tabOrder/activeTab/pendingSwitch/pendingCreates 中暂存；
// 收到第一个带真实 uid 的消息（subscribed 或 session_created）后迁移到 uid 键。
function _migrateOptimisticKey(displaySid, uid) {
  if (!displaySid || !uid || displaySid === uid) return;
  if (state.sessions[uid] || !state.sessions[displaySid]) return;
  if (state.sessions[displaySid].uid) return; // 已迁移
  state.sessions[uid] = state.sessions[displaySid];
  delete state.sessions[displaySid];
  // 终端实例不能随键迁移：实例内闭包（onData/onResize/onBinary 等）绑定的是
  // 创建时的键（乐观 sid），迁移后输入/输出仍发往 sid → 后端按 uid 找不到会话
  // → 新建会话无法输入。销毁旧实例，由 handleSubscribed→switchTab(realUid)
  // →ensureTerminal(realUid) 用真实 uid 重建。
  if (state.termInstances[displaySid]) {
    try { state.termInstances[displaySid].term.dispose(); } catch (_) {}
    try { state.termInstances[displaySid].div.remove(); } catch (_) {}
    delete state.termInstances[displaySid];
  }
  const ti = state.tabOrder.indexOf(displaySid);
  if (ti >= 0) state.tabOrder[ti] = uid;
  if (state.activeTab === displaySid) state.activeTab = uid;
  if (state.pendingSwitch === displaySid) state.pendingSwitch = uid;
  state.pendingCreates.delete(displaySid);
  saveTabState();
}

// ── localStorage 迁移：旧版 tabOrder/activeTab 存 sid，映射为 uid ──
function _migrateTabKeys() {
  let changed = false;
  for (let i = 0; i < state.tabOrder.length; i++) {
    const key = state.tabOrder[i];
    if (!state.sessions[key] && !ports.session.isHandlerSid(key)) {
      const uid = getUidBySid(key);
      if (uid) { state.tabOrder[i] = uid; changed = true; }
    }
  }
  if (state.activeTab && !state.sessions[state.activeTab] && !ports.session.isHandlerSid(state.activeTab)) {
    const uid = getUidBySid(state.activeTab);
    if (uid) { state.activeTab = uid; changed = true; }
  }
  if (changed) saveTabState();
}

export function handleMsg(msg) {
  // 在入口解析一次路由键，挂到 _uid 供各 handler 使用
  msg._uid = resolveMsgUid(msg) || msg.sessionId || msg.session_id || msg.id || '';
  switch (msg.type) {
    case 'session_list':
      handleSessionList(msg.sessions);
      break;
    case 'history_list':
      handleHistoryList(msg.sessions);
      break;
    case 'subscribed':
      handleSubscribed(msg);
      break;
    case 'output':
      ports.terminal.handleOutput(msg);
      break;
    case 'resize_complete':
      handleResizeComplete(msg);
      break;
    case 'session_resized':
      handleSessionResized(msg);
      break;
    case 'session_ended':
      handleSessionEnded(msg);
      break;
    case 'history_detail':
      handleHistoryDetail(msg);
      break;
    case 'history_deleted':
      handleHistoryDeleted(msg);
      break;
    case 'session_created':
      handleSessionCreated(msg);
      break;
    case 'session_removed':
      handleSessionRemoved(msg);
      break;
    case 'session_detail':
      handleSessionDetail(msg);
      break;
    case 'session_event':
      handleSessionEvent(msg);
      break;
    case 'clipboard':
      handleClipboard(msg);
      break;
    case 'session_detail_refresh':
      ports.detail.applyDetailRefresh(msg);
      break;
    case 'shell_list':
      handleShellList(msg.shells);
      break;
    case 'system_stats':
      updateSystemStats(msg);
      break;
    case 'unsubscribed':
      break;
    case 'size_mode_changed':
      handleSizeModeChanged(msg);
      break;
    case 'size_mode_ack':
      handleSizeModeAck(msg);
      break;
    case 'takeover_ack':
      handleTakeoverAck(msg);
      break;
    case 'vnc_status':
    case 'vnc_started':
    case 'vnc_stopped':
    case 'vnc_error':
      if (ports.vnc && ports.vnc.updateVncStatus) ports.vnc.updateVncStatus(msg);
      break;
    case 'fs_status':
    case 'fs_targets':
    case 'fs_error':
      if (ports.fastscreen && ports.fastscreen.handleMessage) ports.fastscreen.handleMessage(msg);
      break;
    case 'cursor_locator_status':
      if (ports.fastscreen && ports.fastscreen.handleMessage) ports.fastscreen.handleMessage(msg);
      break;
    case 'error':
      handleError(msg);
      break;
    default:
      debug('session', 'unknown msg type=%s', msg.type);
  }
}

export function handleSessionList(list) {
  const seen = new Set();
  const staleRunning = [];
  list.forEach(s => {
    const uid = s.uid || s.id;
    seen.add(uid);
    if (!state.sessions[uid]) {
      state.sessions[uid] = {
        id: s.id,
        uid: uid,
        command: s.command,
        running: s.running,
        startTime: s.startTime,
        subscribed: false,
        history: false,
      };
      if (!state.tabOrder.includes(uid)) {
        // 恢复阶段（刷新首次加载，restoreState.pending=true）不自动加入：
        // 用户已关闭的标签（tabOrder 已保存为空/子集）不应被 session_list
        // 全部重新加回（会话关闭标签后仍运行，刷新会"又冒出来好多标签页"）。
        // 恢复完成后新出现的会话（其他客户端创建）在后续 list 更新时加入。
        if (!state.restoreState.pending) {
          state.tabOrder.push(uid);
          saveTabState();
        }
      }
    } else {
      const prev = state.sessions[uid];
      if (prev.running && !s.running) {
        staleRunning.push(uid);
      }
      prev.command = s.command;
      prev.running = s.running;
      if (s.uid) prev.uid = s.uid;
      if (s.startTime) prev.startTime = s.startTime;
    }
  });

  // localStorage 迁移：旧版 tabOrder 存 sid，映射为 uid
  _migrateTabKeys();

  if (state.restoreState.gotHistory) {
    // 清理已不在活跃列表中的会话（仍保留历史会话）
    for (const key of Object.keys(state.sessions)) {
      if (!seen.has(key) && !state.sessions[key].history && !state.pendingCreates.has(key)) {
        ports.ui.removeSessionTab(key, false);
      }
    }

    let tabOrderChanged = false;
    for (let i = state.tabOrder.length - 1; i >= 0; i--) {
      const key = state.tabOrder[i];
      const isHistory = !!(state.history[key] || (state.sessions[key] && state.sessions[key].history));
      if (!seen.has(key) && !isHistory && !state.pendingCreates.has(key) && !ports.session.isHandlerSid(key)) {
        state.tabOrder.splice(i, 1);
        tabOrderChanged = true;
      }
    }
    if (state.activeTab && !ports.session.isHandlerSid(state.activeTab) && !state.sessions[state.activeTab] && !state.pendingCreates.has(state.activeTab)) {
      state.activeTab = state.tabOrder.length > 0 ? state.tabOrder[state.tabOrder.length - 1] : null;
      tabOrderChanged = true;
    }
    if (tabOrderChanged) saveTabState();
  }

  ports.ui.renderTabs();
  ports.ui.renderSidebar();

  if (!state.restoreState.pending) {
    for (const key of state.tabOrder) {
      const s = state.sessions[key];
      if (!s) continue;
      if (s.running && !s.subscribed && !state.termInstances[key]) {
        sendToSession(key, { type: 'subscribe' });
      }
    }
  }

  for (const key of staleRunning) {
    const s = state.sessions[key];
    if (s) {
      s.running = false;
      s.subscribed = false;
      s.history = true;
      if (!state.history[key]) {
        state.history[key] = {
          id: s.id,
          uid: s.uid,
          command: s.command || '',
          ptyType: s.ptyType,
          encoding: s.encoding || 'utf-8',
          startTime: s.startTime,
          endTime: Date.now() / 1000,
          exitCode: s.exitCode,
          errorMessage: s.errorMessage,
          running: false,
        };
      }
      ports.ui.applyReadonlyState(key, true);
      const inst = state.termInstances[key];
      if (inst) {
        inst.term.write('\r\n\x1b[90m' + t('msg.sessionEnded') + '\x1b[0m\r\n');
      }
      if (!state.closedSessionToastSet.has(key)) {
        state.closedSessionToastSet.add(key);
        ports.notification.showToast(t('msg.sessionClosedToast', { sid: s.id }), 'info');
      }
    }
  }
  if (staleRunning.length > 0) {
    ports.ui.updateAutoHide();
    ports.transport.send({ type: 'history' });
  }

  if (state.activeTab && state.sessions[state.activeTab]) {
    ports.ui.updateStatusInfo(state.activeTab);
  }
  state.restoreState.gotList = true;
  maybeRestoreTabs();
}

export function handleHistoryList(list) {
  state.history = {};
  list.forEach(s => {
    const uid = s.uid || s.id;
    state.history[uid] = {
      id: s.id,
      uid: uid,
      command: s.command,
      ptyType: s.ptyType,
      encoding: s.encoding || 'utf-8',
      startTime: s.startTime,
      endTime: s.endTime,
      exitCode: s.exitCode,
      errorMessage: s.errorMessage,
      running: false,
    };
  });
  ports.ui.renderSidebar();
  ports.ui.renderHistoryDropdown();
  state.restoreState.gotHistory = true;
  maybeRestoreTabs();
}

export function initSessionState(key, msg, isHistory) {
  if (!state.sessions[key]) {
    state.sessions[key] = {
      id: msg.sessionId || msg.id || key,
      uid: msg.uid || msg.sessionUid || '',
      command: msg.command || '',
      running: msg.running,
      subscribed: false,
      history: isHistory,
    };
    if (!state.tabOrder.includes(key)) {
      state.tabOrder.push(key);
      saveTabState();
    }
  }
  const s = state.sessions[key];
  s.running = msg.running;
  s.mode = msg.mode || (s.ptyType === 'subprocess' ? 'subprocess' : 'pty');
  s.ptyType = msg.ptyType || s.ptyType;
  if (msg.stderrReplay !== undefined) s.pendingStderrReplay = msg.stderrReplay || null;
  if (msg.uid) s.uid = msg.uid;
  const daemonCols = msg.cols || DEFAULT_COLS;
  const daemonRows = msg.rows || DEFAULT_ROWS;
  if (s.uid) {
    setSessionSizeConfig(s.uid, { daemonCols, daemonRows });
  }
  s.cols = daemonCols;
  s.rows = daemonRows;
  s.encoding = msg.encoding || 'utf-8';
  s.subscribed = true;
  s.exitCode = msg.exitCode;
  s.errorMessage = msg.errorMessage;
  s.pendingReplay = msg.replay || null;
  s.pendingSnapshot = msg.snapshot || null;
  s.pendingScrollback = msg.scrollback || null;
  if (msg.startTime) s.startTime = msg.startTime;
  if (!Array.isArray(s.pendingOutput)) s.pendingOutput = [];
  return s;
}

export function handleSubscribed(msg) {
  const uid = msg._uid;
  const sid = msg.sessionId;
  debug('session',
        'handleSubscribed uid=%s sid=%s running=%s pendingSwitch=%s activeTab=%s replay_len=%d scrollback_len=%d',
        uid, sid, msg.running, state.pendingSwitch, state.activeTab,
        (msg.replay || '').length, (msg.scrollback || '').length);

  // 乐观创建键迁移：临时 sid 键 → 真实 uid 键
  if (sid && uid && sid !== uid) {
    _migrateOptimisticKey(sid, uid);
  }
  state.pendingCreates.delete(uid);
  state.pendingCreates.delete(sid);

  const wasSubscribed = !!(state.sessions[uid] && state.sessions[uid].subscribed);
  initSessionState(uid, msg, false);
  ports.terminal.setAppMouseMode(uid, !!msg.appMouseMode);

  // 自适应锁状态恢复
  const subS = state.sessions[uid];
  if (subS) {
    subS.adaptiveOwnerActive = !!msg.adaptiveOwnerActive;
    subS.adaptiveOwnerUid = msg.adaptiveOwnerUid || null;
    if (subS.adaptiveOwnerActive && subS.adaptiveOwnerUid === state.clientUid) {
      setLocalAdaptiveOwner(uid, true);
      info('size', 'handleSubscribed uid=%s: restored local adaptive owner', uid);
    } else {
      setLocalAdaptiveOwner(uid, false);
    }
  }

  if (state.pendingSwitch === uid) {
    state.pendingSwitch = null;
    ports.ui.switchTab(uid);
  } else if (state.activeTab === uid) {
    ports.ui.switchTab(uid);
  } else if (state.activeTab === null) {
    ports.ui.switchTab(uid);
  } else {
    ports.ui.renderTabs();
    ports.ui.renderSidebar();
  }
  ports.ui.updateStatusInfo(uid);
}

export function handleSessionEnded(msg) {
  const uid = msg._uid;
  info('session', 'session_ended uid=%s exitCode=%s errMsg=%s',
       uid, msg.exitCode, msg.errorMessage || '');
  const s = state.sessions[uid];
  if (s) {
    s.running = false;
    s.exitCode = msg.exitCode;
    s.errorMessage = msg.errorMessage;
    s.subscribed = false;
    s.history = true;
    s.adaptiveOwnerActive = false;
    s.adaptiveOwnerUid = null;
    setLocalAdaptiveOwner(uid, false);
    if (!state.history[uid]) {
      state.history[uid] = {
        id: s.id,
        uid: uid,
        command: s.command || '',
        ptyType: s.ptyType,
        encoding: s.encoding || 'utf-8',
        startTime: s.startTime,
        endTime: Date.now() / 1000,
        exitCode: msg.exitCode,
        errorMessage: msg.errorMessage,
        running: false,
      };
    }
  }

  const code = msg.exitCode;
  let note = '\r\n\x1b[90m' + t('msg.sessionEndedNote');
  if (code !== null && code !== undefined) note += t('msg.exitCode', { code });
  if (msg.errorMessage) note += ' ' + msg.errorMessage;
  note += ']\x1b[0m\r\n';

  const inst = state.termInstances[uid];
  if (inst) {
    inst.term.write(note);
  } else {
    const ses = state.sessions[uid];
    if (ses) {
      if (!Array.isArray(ses.pendingOutput)) ses.pendingOutput = [];
      ses.pendingOutput.push(note);
    }
  }

  ports.ui.renderTabs();
  ports.ui.renderSidebar(uid);
  ports.ui.updateStatusInfo(uid);
  ports.ui.applyReadonlyState(uid, true);
  ports.ui.updateAutoHide();
  ports.transport.send({ type: 'history' });
  if (!state.closedSessionToastSet.has(uid)) {
    state.closedSessionToastSet.add(uid);
    ports.notification.showToast(t('msg.sessionClosedToast', { sid: s && s.id || uid }), 'info');
  }
  if (state.closedSessionToastSet.size > 50) {
    const first = state.closedSessionToastSet.values().next().value;
    state.closedSessionToastSet.delete(first);
  }
}

export function handleHistoryDetail(msg) {
  const uid = msg._uid || msg.uid || msg.id;
  const s = initSessionState(uid, msg, true);
  s.running = false;
  s.history = true;
  s.adaptiveOwnerActive = false;
  s.adaptiveOwnerUid = null;
  setLocalAdaptiveOwner(uid, false);

  if (state.pendingSwitch === uid) {
    state.pendingSwitch = null;
    ports.ui.switchTab(uid);
  } else if (state.activeTab === uid) {
    ports.ui.switchTab(uid);
  } else if (state.activeTab === null) {
    state.activeTab = uid;
    saveTabState();
    ports.ui.switchTab(uid);
  } else {
    ports.ui.renderTabs();
    ports.ui.renderSidebar();
  }
  ports.ui.updateStatusInfo(uid);
}

export function handleHistoryDeleted(msg) {
  const uid = msg._uid;
  delete state.history[uid];
  if (state.sessions[uid] && state.sessions[uid].history) {
    ports.ui.removeSessionTab(uid, true);
  }
  ports.ui.renderSidebar();
  ports.ui.renderHistoryDropdown();
}

export function handleSessionCreated(msg) {
  const sid = msg.sessionId;
  const uid = msg.uid || '';
  info('session', 'handleSessionCreated sid=%s uid=%s', sid, uid);
  // 乐观创建键迁移：如会话已由 submitNewSession 以 sid 为临时键创建，
  // 迁移到 uid 键（若第一次迁移尚未发生）
  if (uid) {
    _migrateOptimisticKey(sid, uid);
    if (!state.sessions[uid]) {
      // 其他客户端创建的会话：初始化为空，由 list 刷新完整数据
    } else {
      // 补全 uid
      if (!state.sessions[uid].uid) state.sessions[uid].uid = uid;
    }
  }
  if (!state.sessions[uid] && !state.sessions[sid]) {
    ports.transport.send({ type: 'list' });
  }
}

export function handleSessionRemoved(msg) {
  const uid = msg._uid;
  info('session', 'session_removed uid=%s', uid);
  const s = state.sessions[uid];
  if (s) {
    s.running = false;
    s.history = true;
    s.subscribed = false;
  }
  ports.transport.send({ type: 'history' });
  ports.ui.renderTabs();
  ports.ui.renderSidebar(uid);
  ports.ui.renderHistoryDropdown();
  ports.ui.applyReadonlyState(uid, true);
  if (s && !state.closedSessionToastSet.has(uid)) {
    state.closedSessionToastSet.add(uid);
    ports.notification.showToast(t('msg.sessionClosedToast', { sid: s.id }), 'info');
  }
  if (state.closedSessionToastSet.size > 50) {
    const first = state.closedSessionToastSet.values().next().value;
    state.closedSessionToastSet.delete(first);
  }
}

export function handleError(msg) {
  const message = i18nError(msg);
  for (const key of state.pendingCreates) {
    ports.ui.removeSessionTab(key, false);
  }
  state.pendingCreates.clear();
  const m = message && message.match(/session ['"]([^'"]+)['"] not found/);
  if (m) {
    const sid = m[1];
    const uid = getUidBySid(sid) || sid;
    ports.ui.removeSessionTab(uid, true);
    return;
  }
  ports.notification.showToast(message, 'error');
  error('ws', 'ws error: %s', message);
  ports.ui.renderTabs();
  ports.ui.renderSidebar();
}

export function maybeRestoreTabs() {
  if (!state.restoreState.pending) return;
  if (!state.restoreState.gotList || !state.restoreState.gotHistory) return;
  state.restoreState.pending = false;
  restoreTabs();
}

export function restoreTabs() {
  const validTabs = [];
  let changed = false;
  for (const key of state.tabOrder) {
    if (ports.session.isHandlerSid(key)) {
      if (ports.session.restoreHandlerTab(key)) {
        validTabs.push(key);
      } else {
        changed = true;
      }
      continue;
    }
    const s = state.sessions[key];
    if (s && s.running) {
      validTabs.push(key);
      if (!s.subscribed) {
        sendToSession(key, { type: 'subscribe' });
      }
    } else if (state.history[key]) {
      validTabs.push(key);
      sendToSession(key, { type: 'history_detail' });
    } else {
      changed = true;
    }
  }
  if (changed) {
    state.tabOrder = validTabs;
    saveTabState();
  }
  if (state.activeTab && !state.tabOrder.includes(state.activeTab)) {
    state.activeTab = state.tabOrder.length > 0 ? state.tabOrder[state.tabOrder.length - 1] : null;
    saveTabState();
  }
  if (state.activeTab) {
    if (ports.session.isHandlerSid(state.activeTab)) {
      ports.ui.switchTab(state.activeTab);
    } else {
      state.pendingSwitch = state.activeTab;
    }
  }
}

export function handleSessionDetail(msg) {
  const uid = msg._uid;
  if (state.sessions[uid]) {
    if (msg.startTime) state.sessions[uid].startTime = msg.startTime;
    if (msg.ptyType) state.sessions[uid].ptyType = msg.ptyType;
    if (msg.encoding) state.sessions[uid].encoding = msg.encoding;
    const cfg = getSessionSizeConfigByUid(uid);
    if (cfg.mode === 'default') {
      if (msg.cols) state.sessions[uid].cols = msg.cols;
      if (msg.rows) state.sessions[uid].rows = msg.rows;
    }
  }
  ports.detail.showDetailDialog(uid, msg);
}

export function handleShellList(shells) {
  if (!shells || Object.keys(shells).length === 0) return;
  state.availableShells = shells;
  try {
    localStorage.setItem('pty_available_shells', JSON.stringify(shells));
  } catch (_) {}
}

export function updateSystemStats(msg) {
  ports.ui.updateSystemStats(msg);
}

export function handleSessionEvent(msg) {
  const uid = msg._uid;
  const ev = msg.event;
  if (!ev) return;
  if (ev.type === 'mouse_mode') {
    const enabled = !!(ev.detail && ev.detail.enabled);
    debug('mouse', 'recv session_event mouse_mode uid=%s enabled=%s', uid, enabled);
    ports.terminal.setAppMouseMode(uid, enabled);
  }
  ports.detail.appendDetailEvent(ev);
}

export function handleClipboard(msg) {
  const { selection, data } = msg;
  if (selection !== 'clipboard' || !data) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(data).catch(err => {
      warn('clipboard', 'OSC 52 clipboard write failed: %s', err && err.message);
    });
  }
}

export function handleResizeComplete(msg) {
  const uid = msg._uid;
  const inst = state.termInstances[uid];
  if (!inst) {
    debug('session', 'resize_complete uid=%s: termInstance not found', uid);
    return;
  }
  if (msg.cols !== inst.term.cols || msg.rows !== inst.term.rows) {
    // 响应到达时 term 已被更新的 resize 覆盖：丢弃过期响应。
    // 注意：本端发起的 resize 依赖此响应恢复 scrollback，若被丢弃
    // 且无后续响应 → scrollback 丢失（此场景需由后续 session_resized
    // 广播或再次 resize 恢复）。
    debug('resize', 'resize_complete uid=%s STALE dropped: msg=%dx%d term=%dx%d scrollback_len=%d',
          uid, msg.cols, msg.rows, inst.term.cols, inst.term.rows, (msg.scrollback || '').length);
    return;
  }
  debug('resize', 'resize_complete uid=%s %dx%d snapshot_len=%d scrollback_len=%d',
        uid, msg.cols, msg.rows, (msg.snapshot || '').length, (msg.scrollback || '').length);
  const s = state.sessions[uid];
  const isHistory = !!(s && s.history);
  const snapshot = msg.snapshot || '';
  const scrollbackAnsi = msg.scrollback || '';
  if (snapshot.length > 0 || scrollbackAnsi.length > 0) {
    try {
      const scrollbackLines = scrollbackAnsi ? scrollbackAnsi.split('\r\n') : [];
      if (scrollbackLines.length > 0 && scrollbackLines[scrollbackLines.length - 1] === '') {
        scrollbackLines.pop();
      }
      ports.terminal.restoreScrollbackAndSnapshot(inst.term, scrollbackLines, snapshot, isHistory);
    } catch (e) {
      error('session', 'resize_complete apply scrollback+snapshot failed: %s', e && e.message);
    }
  }
  // resize 期间缓冲的输出：不丢弃，重建后写入终端。
  // 缓冲内容为后端模型已 feed 的真实输出（含 resize 后的新内容），
  // snapshot 之后到达的输出不在 snapshot 中，丢弃会导致前端永久丢失
  // （后端模型有、前端无——此前"丢弃 partial repaint"的假设过度）。
  // 写入安全：缓冲字节是模型在快照后继续 feed 的流，含新尺寸的 CUP
  // 定位序列，snapshot 重建后写入与模型状态一致（repaint 部分重复写
  // 同内容，光标定位后收敛，无错位）。
  const buffered = inst._resizeBuffer;
  inst._resizePending = false;
  inst._resizeBuffer = [];
  if (buffered.length > 0) {
    try {
      inst.term.write(buffered.join(''));
      debug('session', 'resize_complete uid=%s: replayed %d buffered outputs (len=%d)',
            uid, buffered.length, buffered.reduce((a, b) => a + b.length, 0));
    } catch (e) {
      error('session', 'resize_complete replay buffered outputs failed: %s', e && e.message);
    }
  }
  requestAnimationFrame(() => {
    try { ports.terminal.applyTerminalFrameSize(uid); } catch (_) {}
  });
}

export function handleSessionResized(msg) {
  const uid = msg._uid;
  const s = state.sessions[uid];
  if (!s || s.history) {
    debug('session', 'session_resized uid=%s skipped: no session or history', uid);
    return;
  }
  debug('resize', 'handleSessionResized uid=%s cols=%d rows=%d (backend broadcast)', uid, msg.cols, msg.rows);
  const inst = state.termInstances[uid];
  if (!inst) {
    s.cols = msg.cols;
    s.rows = msg.rows;
    return;
  }
  const newCols = msg.cols;
  const newRows = msg.rows;
  const snapshot = msg.snapshot || '';
  const scrollbackAnsi = msg.scrollback || '';
  s.cols = newCols;
  s.rows = newRows;
  if (inst.term.cols !== newCols || inst.term.rows !== newRows) {
    inst._externalResize = true;
    inst.term.resize(newCols, newRows);
  }
  if (state.activeTab === uid) {
    ports.terminal.applySessionFrameRatio(uid);
  } else {
    requestAnimationFrame(() => {
      try { ports.terminal.applyTerminalFrameSize(uid); } catch (_) {}
    });
  }
  if (snapshot.length > 0 || scrollbackAnsi.length > 0) {
    try {
      const scrollbackLines = scrollbackAnsi ? scrollbackAnsi.split('\r\n') : [];
      if (scrollbackLines.length > 0 && scrollbackLines[scrollbackLines.length - 1] === '') {
        scrollbackLines.pop();
      }
      ports.terminal.restoreScrollbackAndSnapshot(inst.term, scrollbackLines, snapshot, false);
    } catch (e) {
      error('session', 'session_resized apply scrollback+snapshot failed: %s', e && e.message);
    }
  }
  if (state.activeTab === uid) {
    ports.ui.updateStatusInfo(uid);
    try { ports.ui.refreshSizeSelectorIfOpen(); } catch (_) {}
  }
}

export function handleSizeModeChanged(msg) {
  const uid = msg._uid;
  const s = state.sessions[uid];
  if (!s) {
    debug('size', 'size_mode_changed uid=%s: session not found, ignored', uid);
    return;
  }
  const ownerActive = !!msg.adaptiveOwnerActive;
  const prevActive = !!s.adaptiveOwnerActive;
  s.adaptiveOwnerActive = ownerActive;
  s.adaptiveOwnerUid = ownerActive ? (msg.adaptiveOwnerUid || null) : null;
  info('size',
       'size_mode_changed uid=%s ownerActive=%s->%s mode=%s cols=%s rows=%s localOwner=%s ownerUid=%s',
       uid, prevActive, ownerActive, msg.mode, msg.cols, msg.rows,
       state.localAdaptiveOwnerUids.has(uid), s.adaptiveOwnerUid);

  if (ownerActive) {
    const isOwnLock = !!s.adaptiveOwnerUid && s.adaptiveOwnerUid === state.clientUid;
    if (isOwnLock) {
      setLocalAdaptiveOwner(uid, true);
      info('size', 'size_mode_changed uid=%s: lock inherited by same client_uid', uid);
    } else {
      setLocalAdaptiveOwner(uid, false);
      const cfg = getSessionSizeConfigByUid(uid);
      if (cfg.mode === 'adaptive') {
        const curCols = s.cols || msg.cols || cfg.fixedCols || DEFAULT_COLS;
        const curRows = s.rows || msg.rows || cfg.fixedRows || DEFAULT_ROWS;
        setSessionSizeConfig(s.uid, { mode: 'fixed', fixedCols: curCols, fixedRows: curRows });
        try { ports.terminal.reapplyAllTerminalSizes(false, uid); } catch (_) {}
        requestAnimationFrame(() => {
          try { ports.terminal.applySessionFrameRatio(uid); } catch (e) {
            debug('size', 'demote: applySessionFrameRatio failed uid=%s: %s', uid, e);
          }
        });
      }
    }
  } else {
    const wasLocalOwner = isLocalAdaptiveOwner(uid);
    setLocalAdaptiveOwner(uid, false);
    if (wasLocalOwner) {
      const cfg = getSessionSizeConfigByUid(uid);
      if (cfg.mode === 'adaptive') {
        const curCols = s.cols || cfg.fixedCols || DEFAULT_COLS;
        const curRows = s.rows || cfg.fixedRows || DEFAULT_ROWS;
        setSessionSizeConfig(s.uid, { mode: 'fixed', fixedCols: curCols, fixedRows: curRows });
        try { ports.terminal.reapplyAllTerminalSizes(false, uid); } catch (_) {}
        requestAnimationFrame(() => {
          try { ports.terminal.applySessionFrameRatio(uid); } catch (e) {
            debug('size', 'takeover-demote: applySessionFrameRatio failed uid=%s: %s', uid, e);
          }
        });
      }
    }
  }
  if (state.activeTab === uid) {
    try { ports.ui.updateStatusInfo(uid); } catch (_) {}
  }
  try { ports.ui.refreshSizeSelectorIfOpen(); } catch (_) {}
}

export function handleSizeModeAck(msg) {
  const uid = msg._uid;
  const mode = msg.mode;
  info('size', 'size_mode_ack uid=%s mode=%s cols=%s rows=%s', uid, mode, msg.cols, msg.rows);
  const s = state.sessions[uid];
  if (mode === 'adaptive') {
    setLocalAdaptiveOwner(uid, true);
    if (s) {
      s.adaptiveOwnerActive = true;
      s.adaptiveOwnerUid = state.clientUid;
    }
  } else {
    setLocalAdaptiveOwner(uid, false);
    if (s) {
      s.adaptiveOwnerActive = false;
      s.adaptiveOwnerUid = null;
    }
  }
}

export function handleTakeoverAck(msg) {
  const uid = msg._uid;
  info('size', 'takeover_ack uid=%s: lock cleared', uid);
  const s = state.sessions[uid];
  if (s) {
    s.adaptiveOwnerActive = false;
    s.adaptiveOwnerUid = null;
  }
  setLocalAdaptiveOwner(uid, false);
  try { ports.ui.refreshSizeSelectorIfOpen(); } catch (_) {}
  if (state.activeTab === uid) {
    try { ports.ui.updateStatusInfo(uid); } catch (_) {}
  }
  ports.notification.showToast(t('msg.sizeTakeover'), 'info');
}
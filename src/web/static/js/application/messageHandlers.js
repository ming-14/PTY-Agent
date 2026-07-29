/**
 * 应用层：WebSocket 消息处理器 / 会话用例编排
 *
 * 负责解析后端消息、更新领域状态（state）、并调用端口完成终端输出/UI渲染。
 * 不直接依赖基础设施或表现层，所有外部操作通过 application/ports.js 注入。
 */

import { state, saveTabState, getSessionSizeConfigBySid, setSessionSizeConfig, setLocalAdaptiveOwner, isLocalAdaptiveOwner } from '../domain/state.js';
import { debug, info, warn } from '../domain/logger.js';
import { DEFAULT_COLS, DEFAULT_ROWS } from '../domain/constants.js';
import { ports } from './ports.js';

export function handleMsg(msg) {
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
    // 问题2：自适应排他锁相关消息
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
      handleVncMessage(msg);
      break;
    case 'fs_status':
    case 'fs_targets':
    case 'fs_error':
      handleFastScreenMessage(msg);
      break;
    case 'cursor_locator_status':
    case 'cursor_locator_started':
    case 'cursor_locator_stopped':
    case 'cursor_locator_error':
      handleFastScreenMessage(msg);
      break;
    case 'error':
      handleError(msg.message);
      break;
  }
}

export function handleSessionList(list) {
  const seen = new Set();
  const staleRunning = [];
  list.forEach(s => {
    seen.add(s.id);
    if (!state.sessions[s.id]) {
      state.sessions[s.id] = {
        id: s.id,
        uid: s.uid,
        command: s.command,
        running: s.running,
        startTime: s.startTime,
        subscribed: false,
        history: false,
      };
    } else {
      const prev = state.sessions[s.id];
      if (prev.running && !s.running) {
        staleRunning.push(s.id);
      }
      prev.command = s.command;
      prev.running = s.running;
      if (s.uid) prev.uid = s.uid;
      if (s.startTime) prev.startTime = s.startTime;
    }
  });

  // 仅在已收到历史列表后才清理标签页和会话对象，
  // 避免 session_list 先于 history_list 到达时历史会话标签被误删
  // （此时 state.history 为空，历史会话的 isHistory 判断会失败）
  // restoreTabs() 会在两个列表都收到后统一清理
  if (state.restoreState.gotHistory) {
    for (const sid of Object.keys(state.sessions)) {
      if (!seen.has(sid) && !state.sessions[sid].history && !state.pendingCreates.has(sid)) {
        ports.ui.removeSessionTab(sid, false);
      }
    }

    let tabOrderChanged = false;
    for (let i = state.tabOrder.length - 1; i >= 0; i--) {
      const sid = state.tabOrder[i];
      // 保留历史会话标签：对象标记为 history 或已持久化到 state.history 的均保留
      const isHistory = !!(state.history[sid] || (state.sessions[sid] && state.sessions[sid].history));
      // handler 会话（VNC/FastScreen/Settings）不依赖 session_list 数据，保留其标签
      if (!seen.has(sid) && !isHistory && !state.pendingCreates.has(sid) && !ports.session.isHandlerSid(sid)) {
        state.tabOrder.splice(i, 1);
        tabOrderChanged = true;
      }
    }
    // handler 会话（VNC/FastScreen/Settings）无需 state.sessions 条目，不触发 activeTab 重置
    if (state.activeTab && !ports.session.isHandlerSid(state.activeTab) && !state.sessions[state.activeTab] && !state.pendingCreates.has(state.activeTab)) {
      state.activeTab = state.tabOrder.length > 0 ? state.tabOrder[state.tabOrder.length - 1] : null;
      tabOrderChanged = true;
    }
    if (tabOrderChanged) saveTabState();
  }

  ports.ui.renderTabs();
  ports.ui.renderSidebar();

  if (!state.restoreState.pending) {
    for (const sid of state.tabOrder) {
      const s = state.sessions[sid];
      if (!s) continue;
      if (s.running && !s.subscribed && !state.termInstances[sid]) {
        ports.transport.send({ type: 'subscribe', session_id: sid });
      }
    }
  }

  for (const sid of staleRunning) {
    const s = state.sessions[sid];
    if (s) {
      s.running = false;
      s.subscribed = false;
      s.history = true;
      if (!state.history[sid]) {
        state.history[sid] = {
          id: sid,
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
      ports.ui.applyReadonlyState(sid, true);
      const inst = state.termInstances[sid];
      if (inst) {
        inst.term.write('\r\n\x1b[90m[会话已结束]\x1b[0m\r\n');
        inst.lineMode = false;
      }
      if (!state.closedSessionToastSet.has(sid)) {
        state.closedSessionToastSet.add(sid);
        ports.notification.showToast('会话已关闭: ' + sid, 'info');
      }
    }
  }
  if (staleRunning.length > 0) {
    // 会话从活跃变为历史，autohide 条件可能变化（终端历史会话不隐藏），需更新状态
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
    state.history[s.id] = {
      id: s.id,
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

export function initSessionState(sid, msg, isHistory) {
  if (!state.sessions[sid]) {
    state.sessions[sid] = {
      id: sid,
      command: msg.command || '',
      running: msg.running,
      subscribed: false,
      history: isHistory,
    };
    if (!state.tabOrder.includes(sid)) {
      state.tabOrder.push(sid);
      saveTabState();
    }
  }
  const s = state.sessions[sid];
  s.running = msg.running;
  s.ptyType = msg.ptyType;
  // v9.2: 历史会话通过 history_detail 恢复 uid，使 zoomActiveSession /
  // applySessionFrameRatio 能按 uid 读写 localStorage 的 frameRatio 记忆
  if (msg.uid) s.uid = msg.uid;
  // 守护进程上报的原始尺寸（替换硬编码 80x24）
  const daemonCols = msg.cols || DEFAULT_COLS;
  const daemonRows = msg.rows || DEFAULT_ROWS;
  // 按会话 uid 缓存守护进程首次上报的尺寸：
  // - 若该 uid 尚未缓存（daemonCols/daemonRows 为 null），写入本次值
  // - 若已缓存，保留原值（避免后续 resize 改变"默认"模式的回退基准）
  //   注：每次订阅都会更新缓存为最新守护进程值，确保 daemon 重启后默认值能刷新
  if (s.uid) {
    setSessionSizeConfig(s.uid, { daemonCols, daemonRows });
    debug('size', 'cached daemon default size for uid=%s: %dx%d', s.uid, daemonCols, daemonRows);
  }
  // 始终先用守护进程上报的尺寸更新 s.cols/s.rows：
  // - 'default' 模式下直接使用守护进程尺寸
  // - 'adaptive' 模式下 applyTerminalSize 会根据容器重新计算并覆盖
  // - 'fixed'/'custom' 模式下 applyTerminalSize 会用网页选择的尺寸覆盖，
  //   并因 s.cols(守护进程值) !== cols(模式值) 而发送 resize 同步给守护进程
  s.cols = daemonCols;
  s.rows = daemonRows;
  s.encoding = msg.encoding || 'utf-8';
  s.subscribed = true;
  s.exitCode = msg.exitCode;
  s.errorMessage = msg.errorMessage;
  s.pendingReplay = msg.replay || null;
  s.pendingSnapshot = msg.snapshot || null;
  // Phase 3: 守护进程返回的 scrollback（GridScreen 历史区）
  // 仅首次订阅非空（已订阅时为 ""），replayPending 消费后清空
  s.pendingScrollback = msg.scrollback || null;
  if (msg.startTime) s.startTime = msg.startTime;
  if (!Array.isArray(s.pendingOutput)) s.pendingOutput = [];
  return s;
}

export function handleSubscribed(msg) {
  const sid = msg.sessionId;
  debug('session',
        'handleSubscribed sid=%s running=%s pendingSwitch=%s activeTab=%s replay_len=%d scrollback_len=%d',
        sid, msg.running, state.pendingSwitch, state.activeTab,
        (msg.replay || '').length, (msg.scrollback || '').length);
  state.pendingCreates.delete(sid);

  // C2 改造（模拟 WT）：区分首次订阅和已订阅
  // - 首次订阅：msg.replay 非空（pyte snapshot），initSessionState 设置 pendingReplay，
  //   switchTab → replayPending 会 term.clear()+write(snapshot) 初始化 xterm
  // - 已订阅：msg.replay 为空（handlers.py 已订阅时返回 replay=""),
  //   不设置 pendingReplay，保留 xterm.js 实例的 scrollback
  const wasSubscribed = !!(state.sessions[sid] && state.sessions[sid].subscribed);
  initSessionState(sid, msg, false);
  ports.terminal.setLineMode(sid);
  ports.terminal.setAppMouseMode(sid, !!msg.appMouseMode);

  // v3 改造：从 ws_subscribed 响应恢复自适应锁状态。
  // 刷新后 localAdaptiveOwnerSids 为空，但后端锁仍属于本 client_uid（按 uid 持有），
  // 后端在 ws_subscribed 中携带 adaptiveOwnerActive/adaptiveOwnerUid，
  // 前端据此恢复 localAdaptiveOwnerSids 与 session.adaptiveOwnerUid。
  //   - adaptiveOwnerActive=true 且 uid===clientUid：本端持锁，恢复乐观标记
  //   - adaptiveOwnerActive=true 且 uid!==clientUid：他人持锁，确保本端标记清空（UI 灰显）
  //   - adaptiveOwnerActive=false：无人持锁，清空本端标记
  const subSid = sid;
  const subS = state.sessions[subSid];
  if (subS) {
    subS.adaptiveOwnerActive = !!msg.adaptiveOwnerActive;
    subS.adaptiveOwnerUid = msg.adaptiveOwnerUid || null;
    if (subS.adaptiveOwnerActive && subS.adaptiveOwnerUid === state.clientUid) {
      setLocalAdaptiveOwner(subSid, true);
      info('size', 'handleSubscribed sid=%s: restored local adaptive owner (uid=%s)', subSid, state.clientUid);
    } else {
      setLocalAdaptiveOwner(subSid, false);
    }
  }

  if (state.pendingSwitch === sid) {
    state.pendingSwitch = null;
    debug('session', 'handleSubscribed: pendingSwitch matches, switchTab sid=%s', sid);
    ports.ui.switchTab(sid);
  } else if (state.activeTab === sid) {
    debug('session', 'handleSubscribed: activeTab matches, switchTab sid=%s', sid);
    ports.ui.switchTab(sid);
  } else if (state.activeTab === null) {
    debug('session', 'handleSubscribed: no active tab, switchTab sid=%s', sid);
    ports.ui.switchTab(sid);
  } else {
    debug('session', 'handleSubscribed: render only sid=%s activeTab=%s', sid, state.activeTab);
    ports.ui.renderTabs();
    ports.ui.renderSidebar();
  }
  ports.ui.updateStatusInfo(sid);
}

export function handleSessionEnded(msg) {
  const sid = msg.sessionId;
  info('session', 'session_ended sid=%s exitCode=%s errMsg=%s',
       sid, msg.exitCode, msg.errorMessage || '');
  const s = state.sessions[sid];
  if (s) {
    s.running = false;
    s.exitCode = msg.exitCode;
    s.errorMessage = msg.errorMessage;
    s.subscribed = false;
    // 已结束会话即为历史会话，立即标记并乐观加入历史列表，便于 UI 立刻迁移
    s.history = true;
    // v3: 会话结束后自适应锁随之释放，清空本端持有标记与 session 状态。
    // 后端也会广播 size_mode_changed(adaptiveOwnerActive=false)，此处乐观清空避免 UI 延迟。
    s.adaptiveOwnerActive = false;
    s.adaptiveOwnerUid = null;
    setLocalAdaptiveOwner(sid, false);
    if (!state.history[sid]) {
      state.history[sid] = {
        id: sid,
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
    debug('session', 'session_ended: marked sid=%s running=false closing=%s', sid, s.closing);
  } else {
    warn('session', 'session_ended: sid=%s not in state.sessions', sid);
  }

  const code = msg.exitCode;
  let note = '\r\n\x1b[90m[会话已结束';
  if (code !== null && code !== undefined) note += ' 退出码: ' + code;
  if (msg.errorMessage) note += ' ' + msg.errorMessage;
  note += ']\x1b[0m\r\n';

  const inst = state.termInstances[sid];
  if (inst) {
    inst.term.write(note);
    inst.lineMode = false;
  } else {
    const ses = state.sessions[sid];
    if (ses) {
      if (!Array.isArray(ses.pendingOutput)) ses.pendingOutput = [];
      ses.pendingOutput.push(note);
    }
  }

  ports.ui.renderTabs();
  ports.ui.renderSidebar(sid);
  ports.ui.updateStatusInfo(sid);
  ports.ui.applyReadonlyState(sid, true);
  // 活跃会话变为历史会话后，autohide 条件可能不再满足（终端历史会话不隐藏），需更新状态
  ports.ui.updateAutoHide();
  ports.transport.send({ type: 'history' });
  if (!state.closedSessionToastSet.has(sid)) {
    state.closedSessionToastSet.add(sid);
    ports.notification.showToast('会话已关闭: ' + sid, 'info');
  }
  if (state.closedSessionToastSet.size > 50) {
    const first = state.closedSessionToastSet.values().next().value;
    state.closedSessionToastSet.delete(first);
  }
}

export function handleHistoryDetail(msg) {
  const sid = msg.id;
  const s = initSessionState(sid, msg, true);
  s.running = false;
  s.history = true;
  // v3: 历史会话只读，不持有自适应锁。清空残留状态防止 UI 误判。
  s.adaptiveOwnerActive = false;
  s.adaptiveOwnerUid = null;
  setLocalAdaptiveOwner(sid, false);

  if (state.pendingSwitch === sid) {
    state.pendingSwitch = null;
    ports.ui.switchTab(sid);
  } else if (state.activeTab === sid) {
    // 已切换到该历史标签但回放数据刚到，再次 switchTab 触发 replayPending
    ports.ui.switchTab(sid);
  } else if (state.activeTab === null) {
    state.activeTab = sid;
    saveTabState();
    ports.ui.switchTab(sid);
  } else {
    ports.ui.renderTabs();
    ports.ui.renderSidebar();
  }
  ports.ui.updateStatusInfo(sid);
}

export function handleHistoryDeleted(msg) {
  const sid = msg.sessionId;
  delete state.history[sid];
  if (state.sessions[sid] && state.sessions[sid].history) {
    ports.ui.removeSessionTab(sid, true);
  }
  ports.ui.renderSidebar();
  ports.ui.renderHistoryDropdown();
}

export function handleSessionCreated(msg) {
  const sid = msg.sessionId;
  const uid = msg.uid || '';
  info('session', 'handleSessionCreated sid=%s uid=%s', sid, uid);
  if (state.sessions[sid]) {
    // 会话已由 submitNewSession 创建：直接补全 uid（避免依赖 uid 的功能失效）
    if (uid && !state.sessions[sid].uid) {
      state.sessions[sid].uid = uid;
      debug('session', 'handleSessionCreated backfilled uid for sid=%s uid=%s', sid, uid);
    }
    return;
  }
  // 完全未知会话：发 list 请求拉取完整会话信息
  ports.transport.send({ type: 'list' });
}

export function handleSessionRemoved(msg) {
  const sid = msg.sessionId;
  info('session', 'session_removed sid=%s', sid);
  const s = state.sessions[sid];
  if (s) {
    // 会话已从后端移除：标记为历史/已结束，保留标签让用户继续查看输出
    s.running = false;
    s.history = true;
    s.subscribed = false;
  }
  // 由 history_list 刷新历史记录；不要直接删除 state.history[sid]，
  // 否则归档完成前的竞态会导致历史条目闪现或丢失。
  ports.transport.send({ type: 'history' });
  ports.ui.renderTabs();
  ports.ui.renderSidebar(sid);
  ports.ui.renderHistoryDropdown();
  ports.ui.applyReadonlyState(sid, true);
  if (s && !state.closedSessionToastSet.has(sid)) {
    state.closedSessionToastSet.add(sid);
    ports.notification.showToast('会话已关闭: ' + sid, 'info');
  }
  if (state.closedSessionToastSet.size > 50) {
    const first = state.closedSessionToastSet.values().next().value;
    state.closedSessionToastSet.delete(first);
  }
}

export function handleError(message) {
  for (const sid of state.pendingCreates) {
    ports.ui.removeSessionTab(sid, false);
  }
  state.pendingCreates.clear();

  const m = message && message.match(/session ['"]([^'"]+)['"] not found/);
  if (m) {
    const sid = m[1];
    warn('session', 'ws error: stale session tab removed sid=%s', sid);
    ports.ui.removeSessionTab(sid, true);
    return;
  }

  ports.notification.showToast(message, 'error');
  console.error('ws error:', message);
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
  for (const sid of state.tabOrder) {
    // handler 会话（VNC/FastScreen/Settings）不依赖会话/历史数据，
    // 通过 ports.session.restoreHandlerSid 判断有效性并恢复
    if (ports.session.isHandlerSid(sid)) {
      if (ports.session.restoreHandlerTab(sid)) {
        validTabs.push(sid);
      } else {
        changed = true;  // 无效的 handler 会话（如 VNC 被禁用）从 tabOrder 移除
      }
      continue;
    }
    const s = state.sessions[sid];
    if (s && s.running) {
      validTabs.push(sid);
      if (!s.subscribed) {
        ports.transport.send({ type: 'subscribe', session_id: sid });
      }
    } else if (state.history[sid]) {
      validTabs.push(sid);
      ports.transport.send({ type: 'history_detail', session_id: sid });
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
    // handler 会话（VNC/FastScreen/Settings）不依赖订阅/历史详情消息触发切换，直接 switchTab；
    // 终端会话需等待 subscribed/history_detail 消息消费 pendingSwitch 后再切换
    if (ports.session.isHandlerSid(state.activeTab)) {
      ports.ui.switchTab(state.activeTab);
    } else {
      state.pendingSwitch = state.activeTab;
    }
  }
}

export function handleSessionDetail(msg) {
  const sid = msg.id;
  if (state.sessions[sid]) {
    if (msg.startTime) state.sessions[sid].startTime = msg.startTime;
    if (msg.ptyType) state.sessions[sid].ptyType = msg.ptyType;
    if (msg.encoding) state.sessions[sid].encoding = msg.encoding;
    // 守护进程尺寸覆盖：仅在"默认"模式下接受守护进程上报的尺寸；
    // 其它模式下网页的尺寸选择优先，忽略守护进程的值（自适应/固定/自定义）
    // 按该会话自身的 uid 查询模式
    const cfg = getSessionSizeConfigBySid(sid);
    if (cfg.mode === 'default') {
      if (msg.cols) state.sessions[sid].cols = msg.cols;
      if (msg.rows) state.sessions[sid].rows = msg.rows;
    }
  }
  ports.detail.showDetailDialog(sid, msg);
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
  const sid = msg.sessionId;
  const ev = msg.event;
  if (!ev) return;
  if (ev.type === 'mouse_mode') {
    const enabled = !!(ev.detail && ev.detail.enabled);
    debug('mouse', 'recv session_event mouse_mode sid=%s enabled=%s', sid, enabled);
    ports.terminal.setAppMouseMode(sid, enabled);
  }
  ports.detail.appendDetailEvent(ev);
}

/**
 * 处理 resize_complete 消息：用后端返回的 scrollback + snapshot 完全重建 buffer。
 *
 * 方案 AK（解决隔行空行 + 保留 scrollback 历史）：
 * - 后端 GridScreen.reflow 在 resize 时已按新列宽正确重排 scrollback（参考 tmux，无隔行空行）
 * - 后端在 resize_complete 中返回 scrollback（带 SGR 颜色）+ snapshot（viewport + 光标）
 * - 前端用 restoreScrollbackAndSnapshot 完全重建 xterm.js buffer：
 *   1. \x1b[3J 清除 xterm.js reflow 产生隔行空行的 scrollback
 *   2. 重写后端 reflow 后的 scrollback（带颜色）
 *   3. \x1b[2J + snapshot 覆盖 viewport（含光标位置）
 * - 这解决了 xterm.js 自身 reflow 与后端 pyte reflow 行为不一致导致的隔行空行问题
 *
 * @param {object} msg { sessionId, cols, rows, snapshot, scrollback }
 */
export function handleResizeComplete(msg) {
  const sid = msg.sessionId;
  const inst = state.termInstances[sid];
  if (!inst) {
    debug('session', 'resize_complete sid=%s: termInstance not found', sid);
    return;
  }
  // 竞态保护：自适应模式可能连续触发多次 resize（ResizeObserver/window resize
  // 等多次触发 fit()），过期的 resize_complete（cols/rows 不匹配当前 term）若被应用，
  // 后端返回的 scrollback 行宽与当前 cols 不匹配 → xterm.js 折行 → 隔行空行。
  // 校验尺寸匹配，丢弃过期消息。
  if (msg.cols !== inst.term.cols || msg.rows !== inst.term.rows) {
    debug('session',
          'resize_complete sid=%s STALE dropped: msg=%dx%d term=%dx%d',
          sid, msg.cols, msg.rows, inst.term.cols, inst.term.rows);
    // 方案 G: STALE 时不清除 _resizePending —— 仍有匹配当前 term 尺寸的 resize 在路上，
    // 缓冲的 output 也要保留（可能包含该匹配 resize 期间的 output）。
    return;
  }
  const s = state.sessions[sid];
  const isHistory = !!(s && s.history);
  const snapshot = msg.snapshot || '';
  const scrollbackAnsi = msg.scrollback || '';
  debug('session',
        'resize_complete sid=%s %dx%d snapshot_len=%d scrollback_len=%d activeTab=%s history=%s',
        sid, msg.cols, msg.rows, snapshot.length, scrollbackAnsi.length, state.activeTab, isHistory);
  // 诊断：scrollback/snapshot 内容预览（strip ANSI 后前 2 行）
  try {
    const stripAnsi = (s) => s.replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, '');
    const sbText = stripAnsi(scrollbackAnsi);
    const sbLines = sbText.split('\r\n').filter(l => l.trim());
    const snapText = stripAnsi(snapshot);
    const snapLines = snapText.split('\r\n').filter(l => l.trim());
    debug('session',
          'resize_complete CONTENT sid=%s sb_first2=%j sb_last=%j snap_first2=%j snap_last=%j',
          sid, sbLines.slice(0, 2), sbLines[sbLines.length - 1] || '',
          snapLines.slice(0, 2), snapLines[snapLines.length - 1] || '');
  } catch (_) {}
  // 用后端返回的 scrollback + snapshot 完全重建 xterm.js buffer
  if (snapshot.length > 0 || scrollbackAnsi.length > 0) {
    try {
      // scrollback ANSI 按 \r\n 分行（Grid.capture_scrollback 每行末尾加 \r\n）
      const scrollbackLines = scrollbackAnsi ? scrollbackAnsi.split('\r\n') : [];
      // 去除末尾空字符串（capture_scrollback 末尾 \r\n 导致 split 产生空元素）
      if (scrollbackLines.length > 0 && scrollbackLines[scrollbackLines.length - 1] === '') {
        scrollbackLines.pop();
      }
      // isHistory 传入：restoreScrollbackAndSnapshot 内部会在 write 完成后
      // 通过 callback 执行正确的 scroll（修复 \x1b[3J 后视口跳顶端的问题）
      ports.terminal.restoreScrollbackAndSnapshot(inst.term, scrollbackLines, snapshot, isHistory);
      debug('session', 'resize_complete applied sid=%s scrollback=%d lines snapshot_len=%d',
            sid, scrollbackLines.length, snapshot.length);
    } catch (e) {
      console.error('resize_complete: apply scrollback+snapshot failed', e);
    }
  }

  // 方案 G: resize_complete 已到达并完成重建，清除 resize pending 标志。
  // 丢弃 resize 期间缓冲的 ConPTY output —— 这些是针对旧/中间尺寸的 partial
  // repaint（如 \e[24;34H\e[J...），snapshot 已包含 resize 后的完整正确内容，
  // 写入缓冲的 repaint 会污染重建结果导致吞输出/错位。
  // 安全性依据：后端 pyte.Screen 在 resize 期间持续 feed ConPTY output，
  // resize 期间的有效输出（如用户输入触发的命令输出）已被 pyte 记录并包含在
  // snapshot 中，丢弃缓冲不会丢失真实数据。
  const discardedCount = inst._resizeBuffer.length;
  const discardedLen = discardedCount > 0
    ? inst._resizeBuffer.reduce((a, b) => a + b.length, 0) : 0;
  inst._resizePending = false;
  inst._resizeBuffer = [];
  if (discardedCount > 0) {
    debug('session',
          'resize_complete sid=%s: discarded %d buffered outputs (total len=%d) - snapshot has correct content',
          sid, discardedCount, discardedLen);
  }

  // snapshot 应用后 frame 需跟随 xterm 实际渲染区，
  // 否则尺寸模式切换后 frame 可能保留旧尺寸导致内容错位
  // write() 后 .xterm-screen 尺寸可能变化，用 rAF 等下一帧再读
  requestAnimationFrame(() => {
    try { ports.terminal.applyTerminalFrameSize(sid); } catch (_) {}
  });
}

/**
 * 处理 session_resized 消息：其他客户端发起的 resize 已在后端完成，
 * 后端定向广播给本客户端（非发起方），需同步调整终端尺寸并重建 buffer。
 *
 * 问题1（尺寸变更通知）：
 * - 发起方收到 resize_complete（已自行处理），不收到本消息（后端 exclude_conn_id 排除）
 * - 非发起方收到本消息，被动接受新尺寸：
 *   1. term.resize(cols, rows) 调整 xterm.js 尺寸
 *   2. 用后端 reflow 后的 scrollback + snapshot 重建 buffer（与 resize_complete 同源）
 *   3. 更新 state.sessions[sid].cols/rows + 状态栏 + frame 尺寸
 *
 * 注：自适应模式多客户端抢占问题属于问题2，此处不处理。
 *
 * @param {object} msg { sessionId, cols, rows, snapshot, scrollback }
 */
export function handleSessionResized(msg) {
  const sid = msg.sessionId;
  const s = state.sessions[sid];
  // 历史会话不处理（已结束，无活跃 PTY）
  if (!s || s.history) {
    debug('session', 'session_resized sid=%s skipped: no session or history', sid);
    return;
  }
  const inst = state.termInstances[sid];
  if (!inst) {
    // 无终端实例（未订阅或未渲染），仅更新尺寸记录
    s.cols = msg.cols;
    s.rows = msg.rows;
    debug('session', 'session_resized sid=%s: no termInstance, updated size only', sid);
    return;
  }

  const newCols = msg.cols;
  const newRows = msg.rows;
  const snapshot = msg.snapshot || '';
  const scrollbackAnsi = msg.scrollback || '';
  debug('session',
        'session_resized sid=%s %dx%d cur=%dx%d snapshot_len=%d scrollback_len=%d activeTab=%s',
        sid, newCols, newRows, inst.term.cols, inst.term.rows,
        snapshot.length, scrollbackAnsi.length, state.activeTab);

  // 更新会话尺寸记录
  s.cols = newCols;
  s.rows = newRows;

  // 调整 xterm.js 尺寸（非发起方被动接受后端新尺寸）
  // 设 _externalResize 标志跳过 onResize 回调，避免向服务端发送冗余 resize
  // 消息（session_resized 已含完整 snapshot，不需要再触发 resize_complete）
  if (inst.term.cols !== newCols || inst.term.rows !== newRows) {
    inst._externalResize = true;
    inst.term.resize(newCols, newRows);
  }

  // 按本端 stage 尺寸调整字号：cols/rows 跟随主端，但字号自适应本端窗口，
  // 避免 frame 过宽溢出 stage（如主端 132 列，本端窗口很窄的场景）。
  // applySessionFrameRatio 内部会按保存的 frameRatio + 新 cols/rows + 当前 cell 尺寸反算字号，
  // 字号被 MIN_FONT_SIZE 限制时 frame 仍可能溢出（居中溢出，用户可接受）。
  if (state.activeTab === sid) {
    ports.terminal.applySessionFrameRatio(sid);
  } else {
    // 非活动标签：仅更新 frame 尺寸跟随 xterm，不调字号（字号在切到该标签时由 switchTab 处理）
    requestAnimationFrame(() => {
      try { ports.terminal.applyTerminalFrameSize(sid); } catch (_) {}
    });
  }

  // 用后端返回的 scrollback + snapshot 完全重建 xterm.js buffer
  // 逻辑与 handleResizeComplete 一致：\x1b[3J 清 scrollback + 重写 + \x1b[2J + snapshot
  if (snapshot.length > 0 || scrollbackAnsi.length > 0) {
    try {
      const scrollbackLines = scrollbackAnsi ? scrollbackAnsi.split('\r\n') : [];
      if (scrollbackLines.length > 0 && scrollbackLines[scrollbackLines.length - 1] === '') {
        scrollbackLines.pop();
      }
      ports.terminal.restoreScrollbackAndSnapshot(inst.term, scrollbackLines, snapshot, false);
      debug('session', 'session_resized applied sid=%s scrollback=%d lines snapshot_len=%d',
            sid, scrollbackLines.length, snapshot.length);
    } catch (e) {
      console.error('session_resized: apply scrollback+snapshot failed', e);
    }
  }

  // 更新状态栏（仅当该会话为活动标签时）
  if (state.activeTab === sid) {
    ports.ui.updateStatusInfo(sid);
    // 问题2：若尺寸选择器下拉正打开，刷新其内容（被锁时"默认/自定义"等描述
    // 依赖 s.cols/s.rows，被动跟随后需重新渲染以同步显示）
    try { ports.ui.refreshSizeSelectorIfOpen(); } catch (_) {}
  }
}

/**
 * 处理 VNC 相关消息（vnc_status / vnc_started / vnc_stopped / vnc_error）。
 * 委托给表现层 vnc.js 的 updateVncStatus 更新状态与 UI。
 */
export function handleVncMessage(msg) {
  if (ports.vnc && ports.vnc.updateVncStatus) {
    ports.vnc.updateVncStatus(msg);
  }
}

/**
 * 处理 FastScreen 相关消息（fs_status / fs_targets / fs_error）。
 * 委托给表现层 fastscreen.js 的 updateFastScreenStatus 更新状态与 UI。
 */
export function handleFastScreenMessage(msg) {
  if (ports.fastscreen && ports.fastscreen.handleMessage) {
    ports.fastscreen.handleMessage(msg);
  }
}

// --------------------------------------------------------------------------- //
// 问题2：自适应排他锁消息处理
// --------------------------------------------------------------------------- //

/**
 * 处理 size_mode_changed 消息：后端广播尺寸模式/锁状态变更。
 *
 * 触发场景：
 * - 其他客户端调用 set_size_mode adaptive → 本端收到 adaptiveOwnerActive=true
 *   需降级：若本端当前是 adaptive 模式，切到 fixed（固定当前尺寸）
 * - 其他客户端调用 set_size_mode 非 adaptive / takeover / 持有者断开
 *   → 本端收到 adaptiveOwnerActive=false，需解锁 UI
 *
 * v3 改造：消息携带 adaptiveOwnerUid（持锁者的 client_uid）。
 * - 同一 client_uid 的多标签页场景：另一标签页获得锁时本端应"继承"锁状态，
 *   不降级（uid === state.clientUid 时 setLocalAdaptiveOwner(true)）。
 * - 不同 client_uid 持锁时本端被降级（原逻辑）。
 *
 * 注：发起方已被后端 exclude_conn_id 排除，本消息仅广播给非发起方。
 * takeover_size_control 例外：广播给所有订阅客户端（含发起方），发起方据此解锁 UI。
 *
 * @param {object} msg { sessionId, adaptiveOwnerActive, adaptiveOwnerUid?, mode?, cols?, rows? }
 */
export function handleSizeModeChanged(msg) {
  const sid = msg.sessionId;
  const s = state.sessions[sid];
  if (!s) {
    debug('size', 'size_mode_changed sid=%s: session not found, ignored', sid);
    return;
  }
  const ownerActive = !!msg.adaptiveOwnerActive;
  const prevActive = !!s.adaptiveOwnerActive;
  s.adaptiveOwnerActive = ownerActive;
  // v3: 同步后端权威的持锁者 uid。
  // ownerActive=true 时取 msg.adaptiveOwnerUid；ownerActive=false 时锁已释放，强制 null。
  s.adaptiveOwnerUid = ownerActive ? (msg.adaptiveOwnerUid || null) : null;
  info('size',
       'size_mode_changed sid=%s ownerActive=%s→%s mode=%s cols=%s rows=%s localOwner=%s ownerUid=%s',
       sid, prevActive, ownerActive, msg.mode, msg.cols, msg.rows,
       state.localAdaptiveOwnerSids.has(sid), s.adaptiveOwnerUid);

  if (ownerActive) {
    // v3: 判断持锁者是否是本 client_uid（同 uid 多标签页继承锁的场景）
    const isOwnLock = !!s.adaptiveOwnerUid && s.adaptiveOwnerUid === state.clientUid;
    if (isOwnLock) {
      // 同 client_uid 的另一个标签页获得了锁：本端应继承锁状态，不降级。
      // 保持/恢复 localAdaptiveOwnerSids，UI 不灰显。
      setLocalAdaptiveOwner(sid, true);
      info('size', 'size_mode_changed sid=%s: lock inherited by same client_uid (%s), no demote',
           sid, state.clientUid);
    } else {
      // 其他 client_uid 持有了自适应锁：本端被降级
      // 清除本地的持有者标记（即使之前认为自己持有，后端权威状态优先）
      setLocalAdaptiveOwner(sid, false);

      // 若本端当前是 adaptive 模式，切到 fixed（固定当前尺寸）
      // 用户原话："取消自适应，变成取消自适应那一刻之前的尺寸"
      const cfg = getSessionSizeConfigBySid(sid);
      if (cfg.mode === 'adaptive') {
        const curCols = s.cols || msg.cols || cfg.fixedCols || DEFAULT_COLS;
        const curRows = s.rows || msg.rows || cfg.fixedRows || DEFAULT_ROWS;
        setSessionSizeConfig(s.uid, { mode: 'fixed', fixedCols: curCols, fixedRows: curRows });
        info('size',
             'size_mode_changed: demoted to fixed %dx%d sid=%s (was adaptive)',
             curCols, curRows, sid);
        // 重新应用终端尺寸（fixed 模式按 fixedCols/fixedRows）
        try { ports.terminal.reapplyAllTerminalSizes(false, sid); } catch (_) {}
        // 问题2：切模式后按保存的 frameRatio 恢复框/stage 占比（用户要求比例不变）。
        // reapplyAllTerminalSizes 只同步 cols/rows，applySessionFrameRatio 才会按 ratio
        // 反算字号使 frame 维持原比例。等一帧让 xterm 尺寸刷新后再读 cell 尺寸。
        requestAnimationFrame(() => {
          try { ports.terminal.applySessionFrameRatio(sid); } catch (e) {
            debug('size', 'demote-to-fixed: applySessionFrameRatio failed sid=%s: %s', sid, e);
          }
        });
      }
    }
  } else {
    // 锁释放：清除本地持有者标记。
    // 被接管场景：本端曾是持锁者（wasLocalOwner=true）但收到 adaptiveOwnerActive=false，
    // 表示其他客户端发起了 takeover_size_control 并清空了锁。
    // 按用户要求"一端接管后，其他端如果是自适应模式要立刻退出"，
    // 立即将本端从 adaptive 切到 fixed（固定当前尺寸），避免本端 FitAddon.fit() 持续
    // 触发 resize 与接管者后续选择的模式冲突。
    // 连接断开场景：持锁者断开时本端 wasLocalOwner=false（从未持锁），不触发降级。
    const wasLocalOwner = isLocalAdaptiveOwner(sid);
    setLocalAdaptiveOwner(sid, false);
    if (wasLocalOwner) {
      const cfg = getSessionSizeConfigBySid(sid);
      if (cfg.mode === 'adaptive') {
        const curCols = s.cols || cfg.fixedCols || DEFAULT_COLS;
        const curRows = s.rows || cfg.fixedRows || DEFAULT_ROWS;
        setSessionSizeConfig(s.uid, { mode: 'fixed', fixedCols: curCols, fixedRows: curRows });
        info('size',
             'size_mode_changed: demoted to fixed %dx%d sid=%s (takeover by another connection)',
             curCols, curRows, sid);
        // 重新应用终端尺寸（fixed 模式按 fixedCols/fixedRows；与当前一致则不触发 resize）
        try { ports.terminal.reapplyAllTerminalSizes(false, sid); } catch (_) {}
        // 问题2：切模式后按保存的 frameRatio 恢复框/stage 占比（用户要求比例不变）。
        // 等一帧让 xterm 尺寸刷新后再读 cell 尺寸。
        requestAnimationFrame(() => {
          try { ports.terminal.applySessionFrameRatio(sid); } catch (e) {
            debug('size', 'takeover-demote: applySessionFrameRatio failed sid=%s: %s', sid, e);
          }
        });
      }
    }
  }

  // 活动标签需刷新状态栏与尺寸选择器下拉
  if (state.activeTab === sid) {
    try { ports.ui.updateStatusInfo(sid); } catch (_) {}
  }
  // 通知 sizeSelector 重新渲染（若下拉打开）
  try { ports.ui.refreshSizeSelectorIfOpen(); } catch (_) {}
}

/**
 * 处理 size_mode_ack 消息：本端发起 set_size_mode 的应答。
 *
 * - mode=adaptive：本端已成功获取自适应锁，标记 localAdaptiveOwner
 * - 非 adaptive：本端已释放锁（若之前是持有者），清除 localAdaptiveOwner
 *
 * @param {object} msg { sessionId, mode, cols?, rows? }
 */
export function handleSizeModeAck(msg) {
  const sid = msg.sessionId;
  const mode = msg.mode;
  info('size', 'size_mode_ack sid=%s mode=%s cols=%s rows=%s', sid, mode, msg.cols, msg.rows);
  const s = state.sessions[sid];
  if (mode === 'adaptive') {
    setLocalAdaptiveOwner(sid, true);
    // v3: 同步 session 状态（防止后续 size_mode_changed 误判为"他人持有"）。
    // 本端持锁 → adaptiveOwnerUid = state.clientUid
    if (s) {
      s.adaptiveOwnerActive = true;
      s.adaptiveOwnerUid = state.clientUid;
    }
  } else {
    setLocalAdaptiveOwner(sid, false);
    // v3: 非 adaptive 模式不持锁，清空 uid
    if (s) {
      s.adaptiveOwnerActive = false;
      s.adaptiveOwnerUid = null;
    }
  }
}

/**
 * 处理 takeover_ack 消息：本端发起 takeover_size_control 的应答。
 *
 * 后端已清空自适应锁（旧持有者降级），本端可继续选择新模式。
 * 前端仅需更新 session.adaptiveOwnerActive=false（解锁 UI），
 * 不自动切模式（用户原话："接管后选模式"）。
 *
 * @param {object} msg { sessionId }
 */
export function handleTakeoverAck(msg) {
  const sid = msg.sessionId;
  info('size', 'takeover_ack sid=%s: lock cleared, waiting for mode selection', sid);
  const s = state.sessions[sid];
  // v3: 锁被清空，uid 同步置 null
  if (s) {
    s.adaptiveOwnerActive = false;
    s.adaptiveOwnerUid = null;
  }
  setLocalAdaptiveOwner(sid, false);

  // 通知 sizeSelector 重新渲染下拉（移除灰显 + 隐藏接管按钮）
  try { ports.ui.refreshSizeSelectorIfOpen(); } catch (_) {}
  if (state.activeTab === sid) {
    try { ports.ui.updateStatusInfo(sid); } catch (_) {}
  }
  ports.notification.showToast('已接管尺寸控制，请选择新模式', 'info');
}

/**
 * 基础设施层：WebSocket 传输适配器
 *
 * 封装浏览器 WebSocket API，向上层提供统一的 send / connect / setMessageHandler 接口。
 */

import { state } from '../domain/state.js';
import { setStatus, updateSystemStatsUI } from './domUtils.js';
import { debug, info, warn, error } from '../domain/logger.js';
import { t } from '../domain/i18n.js';
import { handleUnauthorized, getAuthToken } from './auth.js';

let messageHandler = null;
let systemStatsTimer = null;

// 死连接检测：记录最近一次收到消息的时间，定时器/visibilitychange 据此判断
// WS 是否已静默断开（移动端后台冻结 JS 定时器，onclose 可能不触发）
let lastMsgTime = 0;
// 超过此阈值（ms）未收到任何消息则判定连接已死，主动 close 重连
// 正常情况下 system_stats 1s/次往返，lastMsgTime 最多滞后 ~2s，10s 阈值足够安全
const _WS_DEAD_THRESHOLD = 10000;

export function setMessageHandler(fn) {
  messageHandler = fn;
}

const LS_SERVER_ADDR_KEY = 'pty_server_address';

export function connect() {
  if (state.ws && (state.ws.readyState === WebSocket.CONNECTING || state.ws.readyState === WebSocket.OPEN)) {
    return;
  }
  info('ws', 'connecting...');
  setStatus('connecting', t('status.connecting'));
  const customAddr = localStorage.getItem(LS_SERVER_ADDR_KEY);
  let wsUrl;
  if (customAddr) {
    // 用户指定了协议（http:// 或 https://）时，提取 host 并映射为 ws/wss
    var protoIdx = customAddr.indexOf('://');
    if (protoIdx !== -1) {
      var httpProto = customAddr.slice(0, protoIdx);
      var host = customAddr.slice(protoIdx + 3);
      wsUrl = (httpProto === 'https' ? 'wss:' : 'ws:') + '//' + host;
    } else {
      wsUrl = 'wss://' + customAddr;
    }
  } else {
    wsUrl = (location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + location.host;
  }
  wsUrl += '/ws?clientUid=' + encodeURIComponent(state.clientUid);
  const authToken = getAuthToken();
  if (authToken) wsUrl += '&authToken=' + encodeURIComponent(authToken);
  try {
    state.ws = new WebSocket(wsUrl);
  } catch (e) {
    error('ws', 'connect failed:', e.message);
    setStatus('disconnected', t('status.connectFailed'));
    scheduleReconnect();
    return;
  }

  state.ws.onopen = () => {
    info('ws', 'connected');
    setStatus('connected', t('status.connected'));
    lastMsgTime = Date.now();
    // 重连后清除所有活跃会话的 subscribed 标记，使后续 list→restoreTabs 逻辑
    // 重新发 subscribe（修复移动端后台 WS 断开重连后画面停更）。
    // 首次连接时 state.sessions 为空，不影响。
    _clearSubscribedOnReconnect();
    state.restoreState = { pending: true, gotList: false, gotHistory: false };
    wsSend({ type: 'list' });
    wsSend({ type: 'history' });
    _startSystemStatsTimer();
    if (typeof window.__onWsOpen__ === 'function') window.__onWsOpen__();
  };

  state.ws.onmessage = e => {
    lastMsgTime = Date.now();
    try {
      const data = JSON.parse(e.data);
      // 支持批量合并帧：JSON 数组 = 多条消息逐条分发
      const msgs = Array.isArray(data) ? data : [data];
      for (const msg of msgs) {
        debug('ws', 'recv type=%s sid=%s', msg.type, msg.sessionId || msg.session_id || '');
        if (msg.type === 'auth_required') {
          warn('ws', 'auth required, redirecting to login');
          handleUnauthorized();
          return;
        }
        if (messageHandler) messageHandler(msg);
      }
    } catch (err) {
      error('ws', 'parse error:', err);
    }
  };

  state.ws.onclose = e => {
    warn('ws', 'disconnected code=%s reason=%s', e.code, e.reason || '');
    if (e.code === 4001) {
      handleUnauthorized();
      return;
    }
    setStatus('disconnected', t('status.disconnected'));
    _stopSystemStatsTimer();
    scheduleReconnect();
  };

  state.ws.onerror = e => {
    error('ws', 'error:', e && e.message ? e.message : 'unknown');
    setStatus('disconnected', t('status.connectFailed'));
  };
}

export function scheduleReconnect() {
  if (state.reconnectTimer) return;
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null;
    connect();
  }, 3000);
}

/**
 * 检测 WebSocket 是否已静默断开（移动端后台 WS 半开场景）。
 *
 * 移动端浏览器将网页置于后台时冻结 JS 定时器，WebSocket 可能被 OS 静默关闭，
 * 但 onclose 事件在后台不触发，回到前台后 readyState 仍可能显示 OPEN 而实际已死。
 *
 * 判据：readyState=OPEN 但超过 _WS_DEAD_THRESHOLD 未收到任何消息 → 连接已死。
 * 由 system_stats 定时器（前台 1s/次）和 visibilitychange 回前台时调用。
 */
export function checkWsAlive() {
  if (!state.ws) return;
  if (state.ws.readyState === WebSocket.OPEN) {
    const idle = Date.now() - lastMsgTime;
    if (idle > _WS_DEAD_THRESHOLD) {
      warn('ws', 'dead connection: no msg for %dms, force reconnect', idle);
      try { state.ws.close(); } catch (_) {}
      // 兜底：close 未触发 onclose 时直接调度重连（scheduleReconnect 有防重入）
      scheduleReconnect();
    }
  } else if (state.ws.readyState === WebSocket.CLOSED) {
    // onclose 可能已在后台触发但 setTimeout 被冻结未执行，回前台时补触发
    scheduleReconnect();
  }
}

/**
 * 重连后清除所有活跃会话的 subscribed 标记。
 *
 * 重连后旧 subscribed=true 会使 handleSessionList/restoreTabs 的重订阅条件
 * (!s.subscribed) 不满足，导致不重新发 subscribe，后端不推送 output。
 * 清除后由 list→restoreTabs 现有逻辑重新订阅。
 */
function _clearSubscribedOnReconnect() {
  let count = 0;
  for (const uid of state.tabOrder) {
    const s = state.sessions[uid];
    if (s && s.running && s.subscribed) {
      s.subscribed = false;
      count++;
    }
  }
  if (count > 0) {
    info('ws', 'reconnect: cleared subscribed for %d active sessions', count);
  }
}

export function wsSend(msg) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    debug('ws', 'send type=%s sid=%s', msg.type, msg.session_id || msg.sessionUid || '');
    state.ws.send(JSON.stringify(msg));
  } else {
    warn('ws', 'send dropped (not open): type=%s', msg.type);
  }
}

/**
 * 向指定会话发送消息：自动填充 sessionUid + sessionId（展示名）。
 * 调用方只需提供 uid 和消息体，无需手动构造 session_id 字段。
 * 不支持 create 消息（create 只有展示名，无 uid）。
 *
 * @param {string} uid 会话 uid（或特殊 tab 固定 id）
 * @param {object} payload 消息体（不含 sessionUid/sessionId，由本函数补）
 * @param {string} [displayName] 可选展示名（覆盖 session.id 自动值）
 */
export function sendToSession(uid, payload, displayName) {
  if (!uid) return;
  payload.sessionUid = uid;
  if (displayName) {
    payload.sessionId = displayName;
  } else {
    const s = state.sessions[uid];
    if (s && s.id) payload.sessionId = s.id;
  }
  if (payload.type === 'resize') {
    console.log('[resize] sendToSession uid=%s → backend resize cols=%d rows=%d',
          uid, payload.cols, payload.rows);
  }
  wsSend(payload);
}

function _startSystemStatsTimer() {
  _stopSystemStatsTimer();
  systemStatsTimer = setInterval(() => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      // 死连接检测：前台时定时器正常运行，若连接已静默断开则主动重连
      checkWsAlive();
      wsSend({ type: 'system_stats' });
    }
  }, 1000);
}

function _stopSystemStatsTimer() {
  if (systemStatsTimer) {
    clearInterval(systemStatsTimer);
    systemStatsTimer = null;
  }
}

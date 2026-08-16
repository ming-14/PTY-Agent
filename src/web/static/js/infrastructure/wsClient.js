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
    state.restoreState = { pending: true, gotList: false, gotHistory: false };
    wsSend({ type: 'list' });
    wsSend({ type: 'history' });
    _startSystemStatsTimer();
    if (typeof window.__onWsOpen__ === 'function') window.__onWsOpen__();
  };

  state.ws.onmessage = e => {
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

export function wsSend(msg) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    debug('ws', 'send type=%s sid=%s', msg.type, msg.session_id || '');
    state.ws.send(JSON.stringify(msg));
  } else {
    warn('ws', 'send dropped (not open): type=%s', msg.type);
  }
}

function _startSystemStatsTimer() {
  _stopSystemStatsTimer();
  systemStatsTimer = setInterval(() => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
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

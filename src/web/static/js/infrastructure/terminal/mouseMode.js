/**
 * 终端基础设施：应用鼠标模式追踪
 *
 * 双信号源：
 * - DECSET 检测（detectAppMouseModeFromOutput）：从输出流嗅探 1000/1002/1003
 *   等序列，带跨块尾部缓冲（64B，对齐后端模型窗口），防分片漏检。
 * - 后端推送（setAppMouseMode）：订阅响应携带当前模式 + 后端 mouse_mode
 *   事件实时推送（后端终端模型为权威源）。
 *
 * 合并规则：
 * - appMouseMode = decset || daemon（任一为真即启用）
 * - DECSET OFF 同时清除 daemon（前后端喂同一字节流，前端看到 OFF 则后端必为 OFF，
 *   推送只是确认——修复"退出 TUI 后 daemon 标志残留导致滚轮锁死"）
 * - 后端推送 enabled=false 同时清除 decset（后端权威）
 * - 后端推送 enabled=true 置 daemon（前端 DECSET 漏检时的兜底）
 */

import { state } from '../../domain/state.js';
import { debug } from '../../domain/logger.js';
import { decodeWriteData } from './shared.js';

let _mouseModeChangeCallback = null;

const MOUSE_OVERRIDE_KEY = 'pty_mouse_override';
// 跨块缓冲长度：DECSET 序列跨 PTY read / WS 消息分片时拼接扫描
// （对齐后端 wezterm 模型的 64B mode_tail 窗口）
const MOUSE_SCAN_TAIL = 64;

function _loadMouseOverride(uid) {
  try {
    const raw = localStorage.getItem(MOUSE_OVERRIDE_KEY);
    if (!raw) return true;
    const map = JSON.parse(raw);
    return uid && map[uid] !== undefined ? !!map[uid] : true;
  } catch (_) {
    return true;
  }
}

function _saveMouseOverride(uid, value) {
  try {
    const raw = localStorage.getItem(MOUSE_OVERRIDE_KEY);
    const map = raw ? JSON.parse(raw) : {};
    if (uid) map[uid] = value;
    localStorage.setItem(MOUSE_OVERRIDE_KEY, JSON.stringify(map));
  } catch (_) {}
}

export function getInitialMouseOverride(uid) {
  return _loadMouseOverride(uid);
}

export function setMouseModeChangeCallback(cb) {
  _mouseModeChangeCallback = cb;
}

export function isMouseModeActive(inst) {
  return !!(inst && inst.appMouseMode);
}

// 合并 DECSET 检测与 daemon（后端权威推送）模式，得到最终应用鼠标模式
export function syncAppMouseMode(inst) {
  const next = !!(inst && (inst.appMouseModeDecset || inst.appMouseModeDaemon));
  if (inst.appMouseMode !== next) {
    inst.appMouseMode = next;
    if (!next) {
      inst.mouseInputOverride = true;
    }
    if (_mouseModeChangeCallback) _mouseModeChangeCallback();
  }
}

export function detectAppMouseModeFromOutput(term, inst, data) {
  const str = decodeWriteData(data);
  if (!str) return;
  // 跨块缓冲：DECSET 序列可能被 PTY read / WS 消息分片，拼接上次尾部后扫描
  const prevTail = inst._mouseScanTail || '';
  const combined = prevTail + str;
  inst._mouseScanTail = combined.slice(-MOUSE_SCAN_TAIL);

  // CSI ? Ps h/l  (DECSET/DECRST)
  const re = /\x1b\[\?(\d+(?:;\d+)*)([hl])/g;
  let changed = false;
  let m;
  while ((m = re.exec(combined)) !== null) {
    const params = m[1].split(';').map(n => parseInt(n, 10));
    const enable = m[2] === 'h';
    params.forEach(ps => {
      if (ps === 1000 || ps === 1002 || ps === 1003) {
        // 与 WT 行为一致：启用一种追踪模式时取代其它追踪模式
        debug('mouse', 'DECSET detected ps=%s enable=%s decset=%s', ps, enable, inst.appMouseModeDecset);
        if (enable) {
          inst.appMouseModeDecset = true;
          inst.appMouseModePs = ps;
          changed = true;
          debug('mouse', 'app mouse tracking ON ps=%s', ps);
        } else if (inst.appMouseModePs === ps || inst.appMouseModePs == null) {
          inst.appMouseModeDecset = false;
          inst.appMouseModePs = null;
          // 关键修复：DECSET OFF 同时清除 daemon 标志。
          // 前后端喂同一字节流，前端看到 OFF 序列则后端模型必然也已 OFF
          // （后端 mouse_mode 推送只是确认）；不清除 daemon 会导致
          // "退出 TUI 后 appMouseMode 恒 true → 滚轮/点击/右键全失效"。
          inst.appMouseModeDaemon = false;
          changed = true;
          debug('mouse', 'app mouse tracking OFF ps=%s (daemon cleared)', ps);
        }
      } else if (ps === 1007) {
        inst.appAlternateScroll = enable;
        debug('mouse', 'alternate scroll %s', enable ? 'ON' : 'OFF');
      } else if (ps === 1004) {
        // Focus Reporting（DECSET 1004）：仅当子进程主动请求时才向前端
        // 发送焦点报告；未启用时发送 \x1b[I/\x1b[O 会污染 stdin
        // （cmd 等非 VT 程序会把序列当垃圾字符显示）。
        inst.appFocusReport = enable;
        debug('mouse', 'focus report %s', enable ? 'ON' : 'OFF');
      } else if (ps === 47 || ps === 1047 || ps === 1049) {
        inst.appAlternateBuffer = enable;
        debug('mouse', 'alternate buffer %s', enable ? 'ON' : 'OFF');
      }
    });
  }
  if (changed) {
    syncAppMouseMode(inst);
  }
}

export function trackAppMouseMode(term, inst) {
  const originalWrite = term.write.bind(term);
  term.write = function(data, cb) {
    detectAppMouseModeFromOutput(term, inst, data);
    return originalWrite(data, cb);
  };
}

/**
 * 后端权威鼠标模式同步（订阅响应 + mouse_mode 事件推送）。
 * @param {string} uid 会话 uid
 * @param {boolean} enabled 后端终端模型的鼠标追踪状态
 */
export function setAppMouseMode(uid, enabled) {
  const inst = state.termInstances[uid];
  if (!inst) {
    // 订阅响应可能先于 ensureTerminal 到达（inst 尚未创建）：
    // 暂存到会话对象，ensureTerminal 创建后回填，避免模式丢失。
    const s = state.sessions[uid];
    if (s) s._pendingAppMouseMode = !!enabled;
    return;
  }
  enabled = !!enabled;
  if (inst.appMouseModeDaemon === enabled) return;
  inst.appMouseModeDaemon = enabled;
  if (enabled) {
    // 后端权威开启：DECSET 检测漏检（分片/快照重建）时兜底启用。
    // daemon 不知道具体 DECSET ps，保守按 1002（按钮+拖动）处理；
    // 若前端 DECSET 检测后续声明其它模式，会覆盖此值。
    if (!inst.appMouseModeDecset) {
      inst.appMouseModePs = 1002;
    }
  } else {
    // 后端权威关闭：同步清除 DECSET 标志（同一字节流，后端必已 OFF）
    inst.appMouseModeDecset = false;
    inst.appMouseModePs = null;
  }
  debug('mouse', 'daemon mouse mode uid=%s enabled=%s ps=%s', uid, enabled, inst.appMouseModePs);
  syncAppMouseMode(inst);
}

// WT 行为：滚轮只在应用请求了 alternate scroll (DECSET 1007) 且当前处于 alternate buffer 时
// 才转换成上下箭头键；这与普通应用鼠标追踪模式是分开的。
export function shouldSendAlternateScroll(inst, e) {
  return !!(inst && inst.mouseInputOverride && inst.appAlternateScroll && inst.appAlternateBuffer && e && !e.shiftKey);
}

// 与 WT 一致：只有当 Shift 未按下且应用请求了鼠标追踪时，才把鼠标事件作为 VT 序列发送。
// Shift 始终用于临时抑制应用鼠标模式，以便进行文本选择/滚动。
export function canSendVtMouseInput(inst, e) {
  return !!(inst && inst.appMouseMode && inst.mouseInputOverride && e && !e.shiftKey);
}

export function toggleMouseInputOverride(uid) {
  const inst = state.termInstances[uid];
  if (!inst || !inst.appMouseMode) return;
  inst.mouseInputOverride = !inst.mouseInputOverride;
  const s = state.sessions[uid];
  if (s && s.uid) _saveMouseOverride(uid, inst.mouseInputOverride);
  debug('mouse', 'toggleMouseInputOverride uid=%s override=%s', uid, inst.mouseInputOverride);
  if (_mouseModeChangeCallback) _mouseModeChangeCallback();
  return inst.mouseInputOverride;
}

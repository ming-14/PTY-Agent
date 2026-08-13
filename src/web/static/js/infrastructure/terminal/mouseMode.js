/**
 * 终端基础设施：应用鼠标模式追踪
 *
 * 监听 DECSET/DECRST 1000/1002/1003 等序列，跟踪 TUI 应用自身声明的鼠标模式。
 */

import { state } from '../../domain/state.js';
import { debug } from '../../domain/logger.js';
import { decodeWriteData } from './shared.js';

let _mouseModeChangeCallback = null;

const MOUSE_OVERRIDE_KEY = 'pty_mouse_override';

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

// 合并 DECSET 检测与 daemon 控制台模式检测，得到最终应用鼠标模式
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
  // CSI ? Ps h/l  (DECSET/DECRST)
  const re = /\x1b\[\?(\d+(?:;\d+)*)([hl])/g;
  let changed = false;
  let m;
  while ((m = re.exec(str)) !== null) {
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
          changed = true;
          debug('mouse', 'app mouse tracking OFF ps=%s', ps);
        }
      } else if (ps === 1005) {
        if (enable) inst.appMouseEncoding = 'utf8';
      } else if (ps === 1006) {
        if (enable) inst.appMouseEncoding = 'sgr';
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
    term._appMouseMode = inst.appMouseMode;
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

export function setAppMouseMode(sid, enabled) {
  const inst = state.termInstances[sid];
  if (!inst) return;
  enabled = !!enabled;
  if (inst.appMouseModeDaemon === enabled) return;
  inst.appMouseModeDaemon = enabled;
  // daemon 检测不知道具体 DECSET ps，保守按 1002（按钮+拖动）处理；
  // 若应用后续通过 DECSET 声明其它模式，会覆盖此值。
  if (enabled && !inst.appMouseModeDecset) {
    inst.appMouseModePs = 1002;
  } else if (!enabled && !inst.appMouseModeDecset) {
    inst.appMouseModePs = null;
  }
  debug('mouse', 'daemon mouse mode sid=%s enabled=%s ps=%s', sid, enabled, inst.appMouseModePs);
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

export function toggleMouseInputOverride(sid) {
  const inst = state.termInstances[sid];
  if (!inst || !inst.appMouseMode) return;
  inst.mouseInputOverride = !inst.mouseInputOverride;
  const s = state.sessions[sid];
  if (s && s.uid) _saveMouseOverride(s.uid, inst.mouseInputOverride);
  debug('mouse', 'toggleMouseInputOverride sid=%s override=%s', sid, inst.mouseInputOverride);
  if (_mouseModeChangeCallback) _mouseModeChangeCallback();
  return inst.mouseInputOverride;
}

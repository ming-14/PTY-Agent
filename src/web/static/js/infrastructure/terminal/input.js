/**
 * 终端基础设施：键盘输入、粘贴与行模式
 */

import { state } from '../../domain/state.js';
import { debug } from '../../domain/logger.js';
import { showToast } from '../domUtils.js';
import { wsSend } from '../wsClient.js';
import { isTermAtBottom, scrollTermToBottom } from './scroll.js';
import { interceptKeyDown as rimeInterceptKeyDown, isKeyboardDisabled } from '../rimeManager.js';

export function copySelection(term) {
  try {
    const sel = term.getSelection();
    if (!sel) return false;
    navigator.clipboard.writeText(sel).catch(err => {
      showToast('复制失败：请允许网站的剪贴板权限', 'error');
      debug('paste', 'copySelection failed: %s', err && err.message);
    });
    term.clearSelection();
    return true;
  } catch (e) {
    showToast('复制失败：请允许网站的剪贴板权限', 'error');
    return false;
  }
}

// ── wezterm 模式感知输入：原始键鼠事件 → 服务端编码 ──
// wezterm KeyModifiers 位定义（与 wezterm-input-types 一致）
const MOD_SHIFT = 1 << 1;
const MOD_ALT = 1 << 2;
const MOD_CTRL = 1 << 3;
const MOD_SUPER = 1 << 4;

const _SPECIAL_KEY_MAP = {
  ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right',
  Escape: 'Esc', ' ': 'Space', Enter: 'Enter', Backspace: 'Backspace',
  Tab: 'Tab', Delete: 'Delete', Insert: 'Insert', Home: 'Home', End: 'End',
  PageUp: 'PageUp', PageDown: 'PageDown',
};

/** 计算 wezterm 修饰键位掩码 */
function keyMods(e) {
  return (e.shiftKey ? MOD_SHIFT : 0)
    | (e.altKey ? MOD_ALT : 0)
    | (e.ctrlKey ? MOD_CTRL : 0)
    | (e.metaKey ? MOD_SUPER : 0);
}

/**
 * 将 DOM KeyboardEvent 映射为 wezterm 按键描述 {key, mods}。
 * 返回 null 表示无法映射（应忽略，不发送）。
 */
function mapKeyToWezterm(e) {
  let mods = keyMods(e);
  const key = e.key;

  // AltGr（组合修饰产出字符）：字符已是布局组合结果，去掉 ctrl/alt 修饰
  if (e.altGraphKey) {
    return key.length === 1 ? { key, mods: 0 } : null;
  }

  // 浏览器对 Ctrl+字母 可能给出控制字符（如 \x03），映射回 'c'+CTRL
  if (mods & MOD_CTRL && key.length === 1) {
    const cc = key.charCodeAt(0);
    if (cc >= 1 && cc <= 26) {
      return { key: String.fromCharCode(cc + 96), mods: (mods | MOD_CTRL) & ~MOD_SHIFT };
    }
  }

  if (Object.prototype.hasOwnProperty.call(_SPECIAL_KEY_MAP, key)) {
    return { key: _SPECIAL_KEY_MAP[key], mods };
  }
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(key)) {
    return { key, mods };
  }
  if (key.length === 1) {
    // 大写字母 + Shift：转小写并保留 SHIFT（让程序感知 shift 状态，kitty/CSI-u）
    if (mods & MOD_SHIFT && /^[A-Z]$/.test(key)) {
      return { key: key.toLowerCase(), mods };
    }
    return { key, mods };
  }
  return null; // 死键/无法映射（如 'Dead'）
}

/** 发送原始键盘事件 → daemon wezterm 编码 → pty */
function sendRawKey(sid, e) {
  const mapped = mapKeyToWezterm(e);
  if (!mapped) {
    debug('key', 'raw key skipped (unmappable): %s', e.key);
    return;
  }
  debug('key', 'raw key: key=%s mods=%s', mapped.key, mapped.mods);
  wsSend({ type: 'key', session_id: sid, key: mapped.key, mods: mapped.mods });
}

export function attachCustomKeyEventHandler(term, sid) {
  term.attachCustomKeyEventHandler(e => {
    if (e.type !== 'keydown') return true;

    if (isKeyboardDisabled()) {
      e.preventDefault();
      return false;
    }

    if (!/^(Shift|Control|Alt|Meta|CapsLock|ContextMenu|ScrollLock|NumLock|PrintScreen|Pause)$/.test(e.key)) {
      try {
        if (!isTermAtBottom(term)) {
          debug('scroll', 'snap-on-input: key=%s ctrl=%s shift=%s → bottom', e.key, e.ctrlKey, e.shiftKey);
          scrollTermToBottom(term);
        }
      } catch (_) {}
    }

    // Web RIME 输入法拦截：由 rimeManager 同步判断并 preventDefault，
    // 异步交给 Rime 面板处理。返回 true 表示已拦截，xterm.js 不应继续处理。
    if (rimeInterceptKeyDown(e)) {
      return false;
    }

    // IME 组合中：不发送原始键（最终文本由 RIME 提交）
    if (e.isComposing || e.keyCode === 229) {
      e.preventDefault();
      return false;
    }

    // 历史（只读）会话：不发送
    const s = state.sessions[sid];
    if (s && s.history) {
      e.preventDefault();
      return false;
    }

    const isCtrl = e.ctrlKey || e.metaKey;
    const isShift = e.shiftKey;

    if (isCtrl && isShift && (e.key === 'c' || e.key === 'C')) {
      const copied = copySelection(term);
      debug('key', 'Ctrl+Shift+C copied=%s', copied);
      return copied ? false : true;
    }

    if (isCtrl && !isShift && (e.key === 'c' || e.key === 'C')) {
      const copied = copySelection(term);
      debug('key', 'Ctrl+C copied=%s', copied);
      return copied ? false : true;
    }

    if (isCtrl && (e.key === 'v' || e.key === 'V')) {
      debug('key', 'Ctrl+V paste');
      e.preventDefault();
      doPaste(sid);
      return false;
    }

    if (isCtrl && !isShift && e.key === 'Backspace') {
      debug('key', 'Ctrl+Backspace → BS');
      term.paste('\x08');
      return false;
    }

    if (isCtrl && !isShift && e.key === 'Enter') {
      debug('key', 'Ctrl+Enter → LF');
      term.paste('\n');
      return false;
    }

    // 其余按键：阻止 xterm.js 自身编码，改发原始事件 → daemon wezterm 编码
    e.preventDefault();
    sendRawKey(sid, e);
    return false;
  });
}

export async function doPaste(sid) {
  const s = state.sessions[sid];
  const inst = state.termInstances[sid];
  if (!s || s.history || !s.running) return;
  if (inst && inst.term) {
    inst.term.focus();
    window.focus();
  }
  try {
    const text = await navigator.clipboard.readText();
    if (!text) return;
    let cleaned = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    cleaned = cleaned.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/g, '');
    // 直接发送 bracketed paste 序列：沙箱为真实 ConPTY（hpcon），
    // conhost 负责回显与行编辑，与原生 ConPTY 会话完全一致。
    cleaned = '\x1b[200~' + cleaned + '\x1b[201~';
    wsSend({ type: 'input', session_id: sid, data: cleaned });
  } catch (e) {
    debug('paste', 'doPaste failed: name=%s message=%s', e && e.name, e && e.message);
    showToast('粘贴失败：请允许网站的剪贴板权限', 'error');
  }
}

/**
 * 终端基础设施：键盘输入、粘贴与行模式
 */

import { state } from '../../domain/state.js';
import { debug } from '../../domain/logger.js';
import { showToast } from '../domUtils.js';
import { t } from '../../domain/i18n.js';
import { sendToSession } from '../wsClient.js';
import { isTermAtBottom, scrollTermToBottom } from './scroll.js';
import { interceptKeyDown as rimeInterceptKeyDown, isKeyboardDisabled } from '../rimeManager.js';
import { isSubprocessSession } from './shared.js';

export function copySelection(term) {
  try {
    const sel = term.getSelection();
    if (!sel) return false;
    navigator.clipboard.writeText(sel).catch(err => {
      showToast(t('term.copyFailed'), 'error');
      debug('paste', 'copySelection failed: %s', err && err.message);
    });
    term.clearSelection();
    return true;
  } catch (e) {
    showToast(t('term.copyFailed'), 'error');
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
function sendRawKey(uid, e) {
  const mapped = mapKeyToWezterm(e);
  if (!mapped) {
    debug('key', 'raw key skipped (unmappable): %s', e.key);
    return;
  }
  debug('key', 'raw key: key=%s mods=%s', mapped.key, mapped.mods);
  sendToSession(uid, { type: 'key', key: mapped.key, mods: mapped.mods });
}

/**
 * 子进程模式按键映射为 stdin 文本。
 *
 * 子进程模式无终端（无 wezterm 模式感知编码），按键直接映射为写入
 * stdin 的字节：
 * - 可打印字符（含中文/emoji）：原字符直接进 stdin
 * - Enter → \n（子进程模式 EOL 约定，与 exec send 语义一致）
 * - Backspace → DEL (\x7f，与 xterm 标准编码一致)
 * - Tab → \t；Escape → \x1b
 * - Ctrl+字母 → 控制字符（0x01-0x1A）
 * - Alt+字母 → ESC 前缀（meta 语义）
 * - 方向键/F 键等无 stdin 语义 → null（忽略，不发送）
 *
 * @param {KeyboardEvent} e DOM 键盘事件
 * @returns {string|null} 要写入 stdin 的文本；null 表示无 stdin 语义
 */
function mapSubprocessKey(e) {
  const key = e.key;
  // AltGr（组合修饰产出字符）：字符已是布局组合结果，直接发送
  if (e.altGraphKey) {
    return key.length === 1 ? key : null;
  }
  // Ctrl+字母：浏览器可能给出控制字符（如 Ctrl+C → \x03）或字母本身
  // （如 Ctrl+D → 'd'），统一归一为控制字符写入 stdin。
  if (e.ctrlKey && key.length === 1) {
    const cc = key.charCodeAt(0);
    if (cc >= 1 && cc <= 26) return String.fromCharCode(cc);
    if (/^[a-z]$/i.test(key)) {
      return String.fromCharCode(key.toLowerCase().charCodeAt(0) - 96);
    }
  }
  // Alt+可打印字符 → ESC 前缀（meta 语义，与终端编码一致）
  if (e.altKey && !e.ctrlKey && key.length === 1) {
    return '\x1b' + key;
  }
  // 特殊键映射
  if (key === 'Enter') return '\n';
  if (key === 'Backspace') return '\x7f';
  if (key === 'Tab') return '\t';
  if (key === 'Escape') return '\x1b';
  if (key === ' ') return ' ';
  // 可打印字符（含中文/emoji BMP）
  if (key.length === 1) return key;
  // 方向键/F 键/编辑键/修饰键：无 stdin 语义
  return null;
}

export function attachCustomKeyEventHandler(term, uid) {
  term.attachCustomKeyEventHandler(e => {
    if (e.type !== 'keydown') return true;

    if (isKeyboardDisabled()) {
      e.preventDefault();
      return false;
    }

    // Web RIME 输入法拦截：由 rimeManager 同步判断并 preventDefault，
    // 异步交给 Rime 面板处理。返回 true 表示已拦截，xterm.js 不应继续处理。
    if (rimeInterceptKeyDown(e)) {
      return false;
    }

    // IME 组合中：不发送原始键（最终文本由 compositionend → onData 上屏）
    // 不调 preventDefault：手机浏览器需要 keydown 默认行为让字符进入 xterm.js
    // 辅助 textarea，compositionend 时 xterm.js 从 textarea.value 提取提交文本。
    // 调 preventDefault 会阻止字符进入 textarea，导致 compositionend 时
    // textarea.value 为空，中文无法上屏（手机系统输入法选候选词后不上屏）。
    // 桌面浏览器 IME 由系统驱动，preventDefault 不影响 textarea.value，但为
    // 统一行为仍不调。
    // 返回 false 阻止 xterm.js 处理 keydown，避免组合中的按键被当作普通输入发送。
    if (e.isComposing || e.keyCode === 229) {
      return false;
    }

    const s = state.sessions[uid];
    const isCtrl = e.ctrlKey || e.metaKey;
    const isShift = e.shiftKey;

    // 复制快捷键（Ctrl+C / Ctrl+Shift+C）：复制不操作终端，放在最前直接 return，
    // 不会走到下方 snap-on-input 滚动到底部，也不受历史会话拦截影响
    // （历史会话同样允许复制选中文本）。
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

    // 历史（只读）会话：不允许输入/粘贴（复制已在上方处理）
    if (s && s.history) {
      e.preventDefault();
      return false;
    }

    // snap-on-input：真正输入（打字/粘贴）时，若视口不在底部则滚回底部。
    // 复制快捷键已在上方 return，不会到达这里，无需额外排除。
    if (!/^(Shift|Control|Alt|Meta|CapsLock|ContextMenu|ScrollLock|NumLock|PrintScreen|Pause)$/.test(e.key)) {
      try {
        if (!isTermAtBottom(term)) {
          debug('scroll', 'snap-on-input: key=%s ctrl=%s shift=%s → bottom', e.key, e.ctrlKey, e.shiftKey);
          scrollTermToBottom(term);
        }
      } catch (_) {}
    }

    if (isCtrl && (e.key === 'v' || e.key === 'V')) {
      debug('key', 'Ctrl+V paste');
      e.preventDefault();
      doPaste(uid);
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

    // 子进程模式：无终端，按键直接映射为 stdin 文本。
    // 不走 sendRawKey（{type:'key'} 后端会拒绝：无 wezterm 编码）。
    // Ctrl+C/V 复制粘贴已在上面处理；Ctrl+Backspace/Ctrl+Enter 保持
    // term.paste（经 onData → {type:'input'} 写入 stdin）。
    if (isSubprocessSession(s)) {
      const text = mapSubprocessKey(e);
      if (text === null) {
        // 无 stdin 语义（方向键/F 键/编辑键/修饰键）：忽略，不发送
        debug('key', 'subprocess key ignored: %s', e.key);
        e.preventDefault();
        return false;
      }
      debug('key', 'subprocess key → input: %s', JSON.stringify(text));
      e.preventDefault();
      sendToSession(uid, { type: 'input', data: text });
      return false;
    }

    // 其余按键：阻止 xterm.js 自身编码，改发原始事件 → daemon wezterm 编码
    e.preventDefault();
    sendRawKey(uid, e);
    return false;
  });
}

export async function doPaste(uid) {
  const s = state.sessions[uid];
  const inst = state.termInstances[uid];
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
    // 子进程模式无终端：不包 bracketed paste，直接写 stdin；
    // PTY 模式发送 bracketed paste 序列，conhost 负责回显与行编辑。
    if (!isSubprocessSession(s)) {
      cleaned = '\x1b[200~' + cleaned + '\x1b[201~';
    }
    sendToSession(uid, { type: 'input', data: cleaned });
  } catch (e) {
    debug('paste', 'doPaste failed: name=%s message=%s', e && e.name, e && e.message);
    showToast(t('term.pasteFailed'), 'error');
  }
}

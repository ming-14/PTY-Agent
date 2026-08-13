/**
 * 终端基础设施：键盘输入、粘贴与行模式
 */

import { state } from '../../domain/state.js';
import { debug } from '../../domain/logger.js';
import { showToast } from '../domUtils.js';
import { wsSend } from '../wsClient.js';
import { isFunctionKey } from './shared.js';
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

    if (isFunctionKey(e.key)) {
      debug('key', 'F-key passthrough: %s', e.key);
      return true;
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

    return true;
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

/**
 * 基础设施层：Web RIME 输入法管理器
 *
 * 职责：
 * - 桌面端：RimeManager(panel 模式) — RimePanel + RimeToolbar，WASM 模式
 * - 移动端：RimeManager(keyboard 模式) — RimeKeyboard + RimeToolbar
 * - 维护 3 态模式（system → web → disabled），持久化到 localStorage
 * - 桌面端提供 interceptKeyDown(e) 供 xterm 拦截物理按键：同步 shouldIntercept + preventDefault + 异步 handleKeyAsync
 * - 桌面端独立监听 document 的 Shift keydown/keyup，实现独立 Shift 切换中英文（镜像 rime-plugin.js 的 exclusiveShift 逻辑）
 * - 移动端通过 RimeKeyboard 的 onTextInsert/onTextDelete 回调发送到终端
 * - 维护按钮文案/样式与当前模式一致
 *
 * 设计说明：
 * - target 为 #terminal-frame div（非 text input），RimeKeyboard 通过回调而非值修改发送输入
 * - 桌面端重写 panel.position() 跟随 xterm 辅助 textarea 的屏幕位置
 * - onCommit/onTextInsert 回调将上屏文本通过 wsSend 发送到当前 active session
 * - 移动端不拦截物理按键，由悬浮键盘直接处理输入
 */

import { state } from '../domain/state.js';
import { debug, info, error } from '../domain/logger.js';
import { t } from '../domain/i18n.js';
import { wsSend, sendToSession } from './wsClient.js';
import { showToast } from './domUtils.js';
import * as settingsStore from '../application/settingsStore.js';

const RIME_MODE_KEY = 'pty_ime_mode';
const MODES = ['system', 'web', 'disabled', 'nokeyboard'];
const MODE_LABELS = { system: t('ime.system'), web: t('ime.web'), disabled: '—', nokeyboard: '' };
const MODE_TITLES = {
  system: t('ime.tipSystem'),
  web: t('ime.tipWeb'),
  disabled: t('ime.tipDisabledPopup'),
  nokeyboard: t('ime.tipDisabledKeyboard'),
};
const MODE_TOAST = { system: t('ime.toastSystem'), web: t('ime.toastWeb'), disabled: t('ime.toastDisabledPopup'), nokeyboard: t('ime.toastDisabledKeyboard') };
const NOKEYBOARD_SVG = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.5" fill="none"/><line x1="3.5" y1="3.5" x2="12.5" y2="12.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';

// 与 RimePanel.RIME_KEY_MAP 同步（见 rime-plugin.js）
const RIME_KEY_MAP = {
  Escape: 'Escape', Tab: 'Tab', Backspace: 'BackSpace', Enter: 'Return',
  ArrowLeft: 'Left', ArrowRight: 'Right', ArrowUp: 'Up', ArrowDown: 'Down',
  Insert: 'Insert', Delete: 'Delete', Home: 'Home', End: 'End',
  PageUp: 'Prior', PageDown: 'Next',
  F1: 'F1', F2: 'F2', F3: 'F3', F4: 'F4', F5: 'F5', F6: 'F6',
  F7: 'F7', F8: 'F8', F9: 'F9', F10: 'F10', F11: 'F11', F12: 'F12',
  Shift: 'Shift', Control: 'Control', Alt: 'Alt', Meta: 'Meta',
  CapsLock: 'Caps_Lock', ContextMenu: 'Menu',
};

const CONTROL_ALLOWLIST = ['`'];

// ─── TKL 功能键 → 终端 VT 转义序列映射 ─────────────────────────────
// 设计目的：TKL 屏幕键盘没有 xterm.js 这个"KeyboardEvent→VT"中介，
// 必须手动实现 xterm.js 内置的转换表，使 F1-F12/导航键/方向键/Escape
// 及 Ctrl+字母组合能正确发送到终端。
//
// 双发防护：以下键由 TKL 的 handleDirectKey / handleSpecialKey / RIME 走
// onTextInsert / onTextDelete 路径发送，onKeyPress 中**不得**重复处理：
//   - {BackSpace} → onTextDelete 发送 \b
//   - {Return}    → onTextInsert 发送 \r
//   - {Tab}       → onTextInsert 发送 \t
//   - {space}     → onTextInsert 发送 ' ' 或全角空格
//   - 单字符     → onTextInsert 发送
// onKeyPress 只负责：F1-F12、Insert/Delete/Home/End/PageUp/PageDown、
//                    方向键、Escape、修饰键组合（{Control+x} 等）。
//
// 序列参考 xterm.js 标准：
//   F1-F4: SS3（\x1bO P/Q/R/S），与光标键模式无关
//   F5-F12: CSI ~（\x1b[15~ 等）
//   Insert/Delete/PageUp/PageDown: CSI ~（\x1b[2~ 等），与光标键模式无关
//   方向键/Home/End: 受 applicationCursorKeysMode 影响
//     - 普通模式: CSI A/B/C/D, CSI H/F
//     - 应用模式: SS3 A/B/C/D, SS3 H/F
const RIME_KEY_TO_VT = {
  // F1-F4（SS3 序列，固定不受光标键模式影响）
  '{F1}': '\x1bOP', '{F2}': '\x1bOQ', '{F3}': '\x1bOR', '{F4}': '\x1bOS',
  // F5-F12（CSI ~ 序列）
  '{F5}': '\x1b[15~', '{F6}': '\x1b[17~', '{F7}': '\x1b[18~', '{F8}': '\x1b[19~',
  '{F9}': '\x1b[20~', '{F10}': '\x1b[21~', '{F11}': '\x1b[23~', '{F12}': '\x1b[24~',
  // 编辑键（CSI ~ 序列，固定）
  '{Insert}': '\x1b[2~', '{Delete}': '\x1b[3~',
  '{Page_Up}': '\x1b[5~', '{Page_Down}': '\x1b[6~',
  // Escape
  '{Escape}': '\x1b',
  // 以下键由 onTextInsert/onTextDelete 处理，此处不映射（避免双发）
  // {BackSpace} {Return} {Tab} {space} 单字符
};

// 方向键和 Home/End 受 applicationCursorKeysMode 影响
// 普通模式：CSI A/B/C/D, CSI H/F
// 应用模式：SS3 A/B/C/D, SS3 H/F（vim/less 等进入应用模式）
function getCursorKeyVT(rimeKey, term) {
  const appMode = !!(term && term.modes && term.modes.applicationCursorKeysMode);
  const prefix = appMode ? '\x1bO' : '\x1b[';
  switch (rimeKey) {
    case '{Up}': return prefix + 'A';
    case '{Down}': return prefix + 'B';
    case '{Right}': return prefix + 'C';
    case '{Left}': return prefix + 'D';
    case '{Home}': return prefix + 'H';
    case '{End}': return prefix + 'F';
    default: return null;
  }
}

/**
 * 解析修饰键组合 RIME 键名（如 {Control+c}, {Control+Shift+F1}, {Alt+Return}）
 * 为终端 VT 序列。
 *
 * 转换规则：
 * - Ctrl+单字母 → 控制字符 0x01-0x1A（如 Ctrl+C = \x03, Ctrl+Z = \x1A）
 * - Ctrl+Shift+单字母 → 同 Ctrl+小写字母（Shift 不改变控制字符值）
 * - Alt+key → ESC 前缀 + key（如 Alt+x = \x1bx）
 * - Alt+Ctrl+letter → ESC + 控制字符
 * - 其他组合（如 Ctrl+F1, Shift+Arrow）→ 暂不处理，返回 null
 *
 * @param {string} rimeKey 形如 {Control+a} 的 RIME 组合键名
 * @returns {string|null} VT 序列或 null（表示不支持）
 */
function parseComboKey(rimeKey) {
  // 去掉 {} 后按 + 分割
  const inner = rimeKey.slice(1, -1);
  const parts = inner.split('+');
  const last = parts[parts.length - 1];
  const mods = parts.slice(0, -1);
  const hasCtrl = mods.includes('Control');
  const hasAlt = mods.includes('Alt');
  const hasMeta = mods.includes('Meta');
  // Meta 组合终端 VT 无标准定义，跳过
  if (hasMeta) return null;

  const isSingleLetter = /^[a-zA-Z]$/.test(last);

  // Ctrl+字母（含 Shift）→ 控制字符
  if (hasCtrl && isSingleLetter) {
    const ctrlChar = String.fromCharCode(last.toLowerCase().charCodeAt(0) - 96);
    return hasAlt ? ('\x1b' + ctrlChar) : ctrlChar;
  }

  // Alt+可打印字符 → ESC 前缀
  if (hasAlt && last.length === 1) {
    return '\x1b' + last;
  }

  // Alt+功能键/Alt+方向键 等组合 VT 协议支持有限，暂不处理
  return null;
}

/**
 * 将 TKL 键盘 onKeyPress 收到的 RIME 键名转换为终端 VT 字节序列。
 *
 * @param {string} rimeKey RIME 格式键名：单字符（'a'）或 {Key} / {Control+x}
 * @param {object|null} term xterm.js Terminal 实例，用于查询 applicationCursorKeysMode
 * @returns {string|null} VT 序列；null 表示该键不应由 onKeyPress 发送
 *                        （如字母键、Backspace、Enter 等由 onTextInsert/onTextDelete 路径处理）
 */
function rimeKeyToVT(rimeKey, term) {
  if (!rimeKey) return null;

  // 单字符（字母/数字/符号）：由 onTextInsert 处理，不在此发送
  if (rimeKey.length === 1) return null;

  // {Key} 格式
  if (rimeKey.startsWith('{') && rimeKey.endsWith('}')) {
    // 修饰键组合
    if (rimeKey.includes('+')) {
      return parseComboKey(rimeKey);
    }
    // 固定序列键（F1-F12、Insert/Delete/PageUp/Down、Escape）
    if (RIME_KEY_TO_VT.hasOwnProperty(rimeKey)) {
      return RIME_KEY_TO_VT[rimeKey];
    }
    // 方向键/Home/End（依赖 applicationCursorKeysMode）
    return getCursorKeyVT(rimeKey, term);
  }

  return null;
}

let mgr = null;           // RimeManager 实例
let panelReady = false;
let panelInitPromise = null;
let currentMode = 'system';

// 终端失焦自动隐藏键盘相关状态
let keyboardHiddenByBlur = false;
let _focusSyncTimer = null;
// 标记最近的 mousedown/touchstart 是否发生在键盘/工具栏内（用于避免拖拽时误隐藏）
let _lastDownInKbOrTb = false;

// Shift 切换中英文（镜像 rime-plugin.js bindTarget 的 exclusiveShift 逻辑）
// 桌面端 externalKeyHandling:true，rime-plugin 自身的 Shift 切换不会运行，由本模块接管
let _exclusiveShift = false;
let _shiftToggleInstalled = false;

// 触摸设备检测（与 app.js isTouchDevice 一致）
const isMobile = ('ontouchstart' in window) ||
  (navigator.maxTouchPoints > 0) ||
  (window.matchMedia && window.matchMedia('(pointer: coarse)').matches);

function isPrintable(key) {
  return /^[a-z0-9!"#$%&'()*+,./:;<=>?@[\] ^_`{|}~\\-]$/i.test(key);
}

/**
 * 将 KeyboardEvent 转换为 RIME 按键字符串（镜像 RimePanel.bindTarget 的逻辑）
 * 返回 null 表示无法转换，调用方应放行。
 */
function toRimeKey(e) {
  const { key, code } = e;
  if (!key) return null;
  const isPrintableKey = isPrintable(key);
  const isAlt = key === 'Alt';
  const hasControl = e.ctrlKey || e.metaKey || e.altKey;
  const hasShift = e.shiftKey;
  const isShortcut = hasControl || (hasShift && !isPrintableKey);

  const wrap = (s) => `{${s}}`;

  if (isShortcut || !isPrintableKey) {
    let base = /^[0-9a-z]$/i.test(key) ? key : RIME_KEY_MAP[key];
    if (base === undefined) return null;
    if (isAlt && code === 'AltRight') base = 'Alt_R';
    const modifiers = [];
    if (e.ctrlKey) modifiers.push('Control');
    if (e.metaKey) modifiers.push('Meta');
    if (e.altKey && !isAlt) modifiers.push('Alt');
    if (e.shiftKey) modifiers.push('Shift');
    modifiers.push(base);
    return wrap(modifiers.join('+'));
  }
  if (code && code.startsWith('Numpad')) {
    return wrap(`KP_${code.substring(6)}`);
  }
  return key;
}

function sendToTerminal(text) {
  if (!text) return;
  const uid = state.activeTab;
  if (!uid) return;
  const s = state.sessions[uid];
  if (!s || !s.running || s.closing) return;
  const inst = state.termInstances[uid];
  if (inst && inst._readonly) return;
  // 沙箱为真实 ConPTY（hpcon），输入直接送入终端（conhost 回显/编辑）
  sendToSession(uid, { type: 'input', data: text });
  debug('rime', 'send → terminal uid=%s data=%s', uid, JSON.stringify(text));
}

function updatePosition() {
  const panel = mgr && mgr.getPanel();
  if (!panel) return;
  const uid = state.activeTab;
  const inst = uid && state.termInstances[uid];
  const ta = inst && inst.term && inst.term.textarea;
  const fw = panel.floatEl.offsetWidth || 200;
  const fh = panel.floatEl.offsetHeight || 40;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let x, y;
  if (ta) {
    const r = ta.getBoundingClientRect();
    x = r.left;
    y = r.bottom + 2;
    if (y + fh > vh - 8) y = r.top - fh - 2;
  } else {
    x = 8;
    y = vh - fh - 40;
  }
  if (x + fw > vw - 8) x = vw - fw - 8;
  if (x < 8) x = 8;
  if (y < 8) y = 8;
  panel.floatEl.style.left = x + 'px';
  panel.floatEl.style.top = y + 'px';
}

/**
 * 获取当前模式下活跃的键盘（普通键盘或 TKL 全键盘）。
 * compact 模式返回 RimeKeyboard，full 模式返回 RimeTKLKeyboard。
 * 桌面端 panel 模式下返回 null（无键盘）。
 */
function getActiveKeyboard() {
  if (!mgr) return null;
  return mgr.getKeyboard() || (mgr.getTKLKeyboard && mgr.getTKLKeyboard());
}

/**
 * 根据 ime.toolbarDisplay 设置控制工具栏显示。
 * 仅在 currentMode === 'web' 时有效，非 web 态由 setMode 控制隐藏。
 */
function applyToolbarDisplay() {
  if (!mgr) return;
  const toolbar = mgr.getToolbar();
  if (!toolbar) return;
  const display = settingsStore.get('ime.toolbarDisplay') || 'always';
  let show = false;
  if (display === 'always') {
    show = true;
  } else if (display === 'desktop_only') {
    show = !isMobile;
  }
  // 'never' → show 保持 false
  toolbar.getElement().style.display = show ? '' : 'none';
}

/**
 * 应用 IME 外观设置（透明度、大小、工具栏显示）。
 * 在 ensurePanel 完成后和 applyImeSetting 外观 key 变更时调用。
 *
 * 实现说明：
 * - toolbar 通过 display:none 控制显隐，opacity/transform 可直接用 inline style
 * - keyboard 通过 .rime-kb-hidden class 控制显隐（opacity:0 + translateY(100vh) + pointer-events:none）
 *   若用 inline style 设置 opacity/transform，会覆盖 hidden class 的样式，
 *   导致 hide() 后键盘可见但 pointer-events:none 生效（无法点击）。
 *   故 keyboard 的 opacity/transform 改用 CSS 变量，由 tabbar.css 的规则统一处理。
 * - panel（悬浮候选词面板）通过 display:none 控制显隐，opacity 用 CSS 变量
 *   --ime-panel-opacity 控制；字号通过 --ime-panel-scale 等比缩放，由 rime-plugin.js
 *   的 CSS 模板中 calc(var(--rime-*-font-size) * var(--ime-panel-scale,1)) 消费。
 */
function applyImeAppearance() {
  if (!mgr) return;
  const toolbar = mgr.getToolbar();
  const keyboard = getActiveKeyboard();
  const panel = mgr.getPanel();
  // 工具栏透明度（toolbar 用 display:none 控制显隐，inline style 不冲突）
  const tbOpacity = settingsStore.get('ime.tbOpacity');
  if (toolbar && tbOpacity != null) {
    toolbar.getElement().style.opacity = String(tbOpacity / 100);
  }
  // 工具栏大小（transform: scale，origin 设为 top left 避免位置偏移）
  const tbScale = settingsStore.get('ime.tbScale');
  if (toolbar && tbScale != null) {
    toolbar.getElement().style.transformOrigin = 'top left';
    toolbar.getElement().style.transform = 'scale(' + tbScale + ')';
  }
  // 键盘透明度/大小：通过 CSS 变量设置，避免 inline style 覆盖 .rime-kb-hidden 的 opacity/transform
  // CSS 变量由 tabbar.css 的 .rime-kb/.rime-tkl 规则消费，hidden 时由双 class 规则覆盖
  if (keyboard) {
    const kbOpacity = settingsStore.get('ime.kbOpacity');
    if (kbOpacity != null) {
      keyboard.getElement().style.setProperty('--ime-kb-opacity', String(kbOpacity / 100));
    }
    const kbScale = settingsStore.get('ime.kbScale');
    if (kbScale != null) {
      keyboard.getElement().style.setProperty('--ime-kb-scale', String(kbScale));
    }
  }
  // 候选词面板透明度/大小：通过 CSS 变量设置，由 rime-plugin.js 的 .rime-panel CSS 模板消费
  // panel 用 display:none 控制显隐，opacity 用变量不会冲突；字号通过 calc 等比缩放
  if (panel && panel.floatEl) {
    const panelOpacity = settingsStore.get('ime.panelOpacity');
    if (panelOpacity != null) {
      panel.floatEl.style.setProperty('--ime-panel-opacity', String(panelOpacity / 100));
    }
    const panelScale = settingsStore.get('ime.panelScale');
    if (panelScale != null) {
      panel.floatEl.style.setProperty('--ime-panel-scale', String(panelScale));
    }
  }
  // 工具栏显示模式（仅在 web 态时应用，非 web 态由 setMode 控制隐藏）
  if (currentMode === 'web') {
    applyToolbarDisplay();
  }
}

async function ensurePanel() {
  if (panelReady || panelInitPromise) return panelInitPromise;
  const p = (async () => {
    if (!window.RimePlugin || !window.RimePlugin.RimeManager) {
      throw new Error('RimeManager 未加载');
    }
    const target = document.getElementById('terminal-frame') || document.body;
    const wasmUrl = new URL('/vendor/rime/wasm/', location.href).href;
    // 检测当前页面实际主题，避免页面加载时 RIME 组件使用硬编码的 dark 主题
    const actualTheme = document.body.dataset.theme === 'dark' ? 'dark' : 'light';
    // 从 settingsStore 读取 IME 配置（由 app.js init 中 await settingsStore.load() 预载）
    const candidateCount = settingsStore.get('ime.candidateCount') || 5;
    const vertical = !!settingsStore.get('ime.vertical');
    const keyboardLayout = settingsStore.get('ime.keyboardLayout') || 'compact';
    const commonOpts = {
      target,
      mode: 'wasm',
      wasmUrl,
      schema: 'luna_pinyin',
      pageSize: candidateCount,
      vertical,
      theme: actualTheme,
      size: 'normal',
    };

    // 桌面端：panel 模式；移动端按键盘方案选择：
    //   compact（普通键盘）→ keyboard 模式（手机风格紧凑键盘）
    //   full（全键盘）→ tkl+panel 模式（TKL 全功能键盘 + 候选面板）
    const managerMode = isMobile
      ? (keyboardLayout === 'full' ? 'tkl+panel' : 'keyboard')
      : 'panel';

    mgr = new window.RimePlugin.RimeManager({
      ...commonOpts,
      managerMode,
      // Panel 选项（桌面端）
      panelTheme: actualTheme,
      showComment: true,
      showNavigation: true,
      externalKeyHandling: !isMobile, // 桌面端外部处理按键
      // Keyboard 选项（移动端）
      kbTheme: actualTheme,
      kbMode: 'floating',
      showOnFocus: false,
      eol: '\r',
      // TKL 选项（移动端 full 布局）
      tklTheme: actualTheme,
      // Toolbar 选项
      toolbarPosition: 'float',
    });

    // 桌面端：重写 panel.position 跟随 xterm 辅助 textarea
    const panel = mgr.getPanel();
    if (panel) {
      panel.position = updatePosition;
      // 仅在无虚拟键盘时注册 panel.onCommit：
      // panel.onCommit 委托给 ime.onCommit（panel.ts），而 tkl+panel / panel+keyboard 模式下
      // wirePanelTKL / wirePanelKeyboard 已注册 ime.onCommit → keyboard.insertText → onTextInsert，
      // 若再注册 panel.onCommit 会在 IME commitCallbacks 上形成第二个处理器，导致中文上屏双发
      // （如输入 nihao 选"你好"会得到"你好你好"）。纯 panel 模式（桌面）无虚拟键盘，
      // commit 只能由 panel.onCommit 发送到终端。
      const hasKb = !!(mgr.getKeyboard() || (mgr.getTKLKeyboard && mgr.getTKLKeyboard()));
      if (!hasKb) {
        panel.onCommit((text) => {
          if (text) {
            sendToTerminal(text);
            debug('rime', 'commit: %s', JSON.stringify(text));
          }
        });
      }
    }

    // 移动端：通过键盘回调发送到终端
    // compact 模式（keyboard）：RimeKeyboard 回调
    const keyboard = mgr.getKeyboard();
    if (keyboard) {
      // onTextInsert：字母键直通（英文模式/数字/符号）走 insertText → textInsertCallbacks
      keyboard.onTextInsert((text) => {
        sendToTerminal(text);
        debug('rime', 'kb insert: %s', JSON.stringify(text));
      });
      // onCommit：候选词选择/拼音上屏走 commitCallbacks（analyze 中 r.state==="committed"|"accepted"）
      // 必须注册，否则选中候选词后提交的中文不会发送到终端
      keyboard.onCommit((text) => {
        if (text) {
          sendToTerminal(text);
          debug('rime', 'kb commit: %s', JSON.stringify(text));
        }
      });
      keyboard.onTextDelete(() => {
        // 发送 DEL (\x7f) 而非 BS (\x08)，与 xterm.js 物理键盘 Backspace 一致
        // （xterm.js evaluateKeyboardEvent case 8 → C0.DEL = \x7f）
        // ConPTY/cmd 对 \x7f 识别为"删除前一个字符"，\x08 可能行为异常
        sendToTerminal('\x7f');
        debug('rime', 'kb delete (backspace DEL)');
      });
    }

    // full 模式（tkl+panel）：RimeTKLKeyboard 回调
    // commit 路由说明（避免双发）：
    //   wirePanelTKL 已注册 ime.onCommit → tklKeyboard.insertText，而 target 为 div，
    //   故 insertText → textInsertCallbacks。所有 commit 路径（panel 选词、TKL processKey
    //   自动上屏、changePage、setIME）均经 ime.handleResult → ime.commitCallbacks → insertText
    //   → onTextInsert 到达终端。
    //   若再注册 tklKeyboard.onCommit，TKL 自身 processKey 触发的 commit 会经 analyze 的
    //   commitCallbacks 再发一次，导致中文上屏双发（终端显示“你你”）。故此处只听
    //   onTextInsert + onTextDelete，不注册 onCommit。
    const tklKeyboard = mgr.getTKLKeyboard && mgr.getTKLKeyboard();
    if (tklKeyboard) {
      tklKeyboard.onTextInsert((text) => {
        sendToTerminal(text);
        debug('rime', 'tkl insert: %s', JSON.stringify(text));
      });
      tklKeyboard.onTextDelete(() => {
        // 发送 DEL (\x7f) 而非 BS (\x08)，与 xterm.js 物理键盘 Backspace 一致
        sendToTerminal('\x7f');
        debug('rime', 'tkl delete (backspace DEL)');
      });
      // onKeyPress：将 TKL 屏幕键盘的功能键/导航键/方向键/Escape 及修饰键组合
      // 转换为终端 VT 序列发送。物理键盘由 xterm.js 内置完成此转换，TKL 屏幕键盘
      // 无此中介，故在此手动实现（详见 rimeKeyToVT）。
      //
      // 双发防护：
      //   1. 字母/数字/符号/Backspace/Enter/Tab/Space 由 rimeKeyToVT 返回 null，
      //      仍走 onTextInsert/onTextDelete 路径，不会被这里重复发送。
      //   2. RIME 编辑态（panel.editing=true）下，Escape/方向键由 RIME 处理
      //      （取消组词/候选导航），不发送到终端，与物理键盘 shouldIntercept 行为一致。
      //      否则会导致编辑态下按方向键既切换候选词又移动光标。
      tklKeyboard.onKeyPress((rimeKey) => {
        const uid = state.activeTab;
        const inst = uid && state.termInstances[uid];
        const term = inst && inst.term;
        // RIME 编辑态下 Escape/方向键交给 RIME，不发送到终端
        const panel = mgr && mgr.getPanel();
        if (panel && panel.editing &&
            (rimeKey === '{Escape}' || /^{(Up|Down|Left|Right|Home|End)}$/.test(rimeKey))) {
          debug('rime', 'tkl keypress: %s → (skip, RIME editing)', rimeKey);
          return;
        }
        const vt = rimeKeyToVT(rimeKey, term);
        if (vt) {
          sendToTerminal(vt);
          debug('rime', 'tkl keypress: %s → %s', rimeKey, JSON.stringify(vt));
        } else {
          debug('rime', 'tkl keypress: %s → (skip, handled by insert/delete path)', rimeKey);
        }
      });
    }

    // 构造函数已创建 toolbar DOM 且默认可见，必须在 await mgr.init() 之前隐藏，
    // 否则 init 异步等待期间 toolbar 会闪现（即使 ime.toolbarDisplay=never）
    const toolbar = mgr.getToolbar();
    if (toolbar) toolbar.getElement().style.display = 'none';

    await mgr.init();

    // 应用默认中英文状态（ime.defaultState）
    // chinese → ascii_mode=false, english → ascii_mode=true, last → 读取上次状态
    _applyDefaultAsciiMode();

    panelReady = true;
    // 应用外观设置（透明度/大小/工具栏显示）
    applyImeAppearance();
    info('rime', 'RimeManager initialized (%s, luna_pinyin)', managerMode);
  })().catch((err) => {
    if (panelInitPromise === p) {
      panelInitPromise = null;
      if (mgr) { try { mgr.destroy(); } catch (_) {} mgr = null; }
      panelReady = false;
    }
    error('rime', 'init failed: %s', err && err.message || err);
    throw err;
  });
  panelInitPromise = p;
  return panelInitPromise;
}

function cancelLoading() {
  if (!panelInitPromise || panelReady) return;
  info('rime', 'loading cancelled by user');
  if (mgr) { try { mgr.destroy(); } catch (_) {} mgr = null; }
  panelInitPromise = null;
  panelReady = false;
}

function updateButton() {
  const btn = document.getElementById('btn-ime');
  if (!btn) return;
  if (currentMode === 'nokeyboard') {
    btn.innerHTML = NOKEYBOARD_SVG;
  } else {
    btn.textContent = MODE_LABELS[currentMode] || '\u4e2d';
  }
  btn.title = MODE_TITLES[currentMode] || '';
  btn.dataset.mode = currentMode;
  btn.classList.toggle('ime-disabled', currentMode === 'disabled');
  btn.classList.toggle('ime-active', currentMode === 'web');
  btn.classList.toggle('ime-nokeyboard', currentMode === 'nokeyboard');
  applyInputMode();
}

function applyInputMode() {
  const uid = state.activeTab;
  const inst = uid && state.termInstances[uid];
  if (!inst || !inst.term || !inst.term.textarea) return;
  const ta = inst.term.textarea;
  if (currentMode === 'web' || currentMode === 'disabled' || currentMode === 'nokeyboard') {
    ta.setAttribute('inputmode', 'none');
  } else {
    ta.removeAttribute('inputmode');
  }
  ta.readOnly = currentMode === 'nokeyboard';
}

function syncKeyboardVisibility() {
  if (!isMobile || currentMode !== 'web' || !panelReady || !mgr) return;
  const keyboard = getActiveKeyboard();
  const toolbar = mgr.getToolbar();
  if (!keyboard) return;
  if (_lastDownInKbOrTb) return;
  const active = document.activeElement;
  if (!active) return;
  const inTerminal = !!(active.closest && active.closest('#terminal-frame'));
  const inKb = !!(keyboard.getElement && keyboard.getElement().contains(active));
  const inTb = !!(toolbar && toolbar.getElement && toolbar.getElement().contains(active));
  if (inTerminal) {
    if (!keyboard.isVisible()) {
      keyboard.show();
      keyboardHiddenByBlur = false;
      debug('rime', 'keyboard show (terminal focused)');
    }
  } else if (inKb || inTb) {
    // 焦点在键盘/工具栏内 → 不改变可见性
  } else {
    if (keyboard.isVisible()) {
      keyboard.hide();
      keyboardHiddenByBlur = true;
      debug('rime', 'keyboard hide (terminal blur, focus moved elsewhere)');
    }
  }
}

function scheduleKeyboardSync() {
  if (!isMobile || currentMode !== 'web' || !panelReady) return;
  if (_focusSyncTimer) clearTimeout(_focusSyncTimer);
  _focusSyncTimer = setTimeout(() => {
    _focusSyncTimer = null;
    syncKeyboardVisibility();
  }, 0);
}

function isDownInKbOrTb(target) {
  if (!target || !mgr) return false;
  const keyboard = getActiveKeyboard();
  const toolbar = mgr.getToolbar();
  const panel = mgr.getPanel();
  const inKb = !!(keyboard && keyboard.getElement && keyboard.getElement().contains(target));
  const inTb = !!(toolbar && toolbar.getElement && toolbar.getElement().contains(target));
  const inPanel = !!(panel && panel.floatEl && panel.floatEl.contains(target));
  return inKb || inTb || inPanel;
}

function describeTarget(t) {
  if (!t) return 'null';
  const tag = t.tagName ? t.tagName.toLowerCase() : '?';
  const cls = t.className && typeof t.className === 'string' ? t.className : '';
  const idx = t.dataset && t.dataset.idx != null ? t.dataset.idx : '';
  return `<${tag} class="${cls}" data-idx="${idx}">`;
}

function setupTerminalFocusTracking() {
  document.addEventListener('focusin', scheduleKeyboardSync);
  document.addEventListener('focusout', scheduleKeyboardSync);
  document.addEventListener('mousedown', (e) => {
    const inIME = isDownInKbOrTb(e.target);
    _lastDownInKbOrTb = inIME;
    debug('rime-touch', 'mousedown target=%s inIME=%s willPreventDefault=%s',
      describeTarget(e.target), inIME, inIME);
    if (inIME) e.preventDefault();
  }, true);
  document.addEventListener('touchstart', (e) => {
    const inIME = isDownInKbOrTb(e.target);
    _lastDownInKbOrTb = inIME;
    debug('rime-touch', 'touchstart target=%s inIME=%s touches=%d',
      describeTarget(e.target), inIME, e.touches ? e.touches.length : -1);
  }, true);
  document.addEventListener('mouseup', () => {
    debug('rime-touch', 'mouseup _lastDownInKbOrTb=%s', _lastDownInKbOrTb);
    setTimeout(() => { _lastDownInKbOrTb = false; }, 0);
  }, true);
  document.addEventListener('touchend', () => {
    debug('rime-touch', 'touchend _lastDownInKbOrTb=%s', _lastDownInKbOrTb);
    setTimeout(() => { _lastDownInKbOrTb = false; }, 0);
  }, true);
  document.addEventListener('click', (e) => {
    const target = e.target;
    const inIME = isDownInKbOrTb(target);
    const isCand = !!(target && target.closest && target.closest('.rime-kb-cand'));
    const isCandNav = !!(target && target.closest && target.closest('.rime-kb-cand-nav-btn'));
    debug('rime-touch', 'click target=%s inIME=%s isCand=%s isCandNav=%s',
      describeTarget(target), inIME, isCand, isCandNav);
    if (!isMobile || currentMode !== 'web' || !panelReady) return;
    if (!target || !target.closest) return;
    if (target.closest('#terminal-frame')) {
      syncKeyboardVisibility();
    }
  }, true);
}

export function isActive() {
  return currentMode === 'web' && panelReady;
}

export function isDisabled() {
  return currentMode === 'disabled';
}

export function isKeyboardDisabled() {
  return currentMode === 'nokeyboard';
}

export function shouldTrackFocus(uid) {
  const keyboardEnabled = currentMode !== 'nokeyboard';
  const inst = uid && state.termInstances[uid];
  const mouseEnabled = !inst || !inst.appMouseMode || inst.mouseInputOverride;
  return keyboardEnabled || mouseEnabled;
}

export function getMode() {
  return currentMode;
}

export async function setMode(mode) {
  if (!MODES.includes(mode)) return;
  // ime.enabled=false 时拒绝切到 web 态（Web RIME 未启用，web 态无意义）
  if (mode === 'web' && settingsStore.get('ime.enabled') === false) {
    info('rime', 'setMode(web) rejected: ime.enabled=false');
    return;
  }
  const prev = currentMode;
  if (mode !== 'web' && panelInitPromise && !panelReady) {
    cancelLoading();
  }
  currentMode = mode;
  updateButton();
  if (mode === 'web' && !panelReady) {
    const btn = document.getElementById('btn-ime');
    if (btn) btn.textContent = (MODE_LABELS[mode] || '?') + '...';
    try {
      await ensurePanel();
    } catch (err) {
      if (currentMode === 'web') {
        const msg = err && err.message || String(err);
        error('rime', 'ensurePanel failed: %s', msg);
        showToast(t('ime.initFailed', { msg }), 'error');
        currentMode = 'system';
        localStorage.setItem(RIME_MODE_KEY, 'system');
        updateButton();
      }
      return;
    }
    // 竞态保护：await 期间 ime.enabled 可能已被改为 false，
    // applyImeSetting 会切回 system 并销毁 mgr（currentMode 不再是 'web'）。
    // 此时不应继续写入 localStorage 或显示 UI，直接返回。
    if (currentMode !== 'web') {
      info('rime', 'setMode(web) aborted: mode changed during ensurePanel (currentMode=%s)', currentMode);
      return;
    }
  }
  localStorage.setItem(RIME_MODE_KEY, mode);
  if (mode === 'web') {
    const toolbar = mgr && mgr.getToolbar();
    const keyboard = getActiveKeyboard();
    const panel = mgr && mgr.getPanel();
    // 工具栏显示由 applyToolbarDisplay 根据 ime.toolbarDisplay 控制
    if (toolbar) applyToolbarDisplay();
    if (isMobile && keyboard) {
      syncKeyboardVisibility();
    }
  } else {
    const toolbar = mgr && mgr.getToolbar();
    const keyboard = getActiveKeyboard();
    const panel = mgr && mgr.getPanel();
    if (toolbar) toolbar.getElement().style.display = 'none';
    if (isMobile && keyboard) {
      keyboard.hide();
      keyboardHiddenByBlur = false;
    }
    if (panel) panel.hide();
  }
  updateButton();
  debug('rime', 'mode %s → %s', prev, currentMode);
}

export async function cycleMode() {
  // ime.enabled=false 时，web 态不可用，从循环中过滤掉（4 态变 3 态）
  const imeEnabled = settingsStore.get('ime.enabled') !== false;
  const modes = imeEnabled ? MODES : MODES.filter(m => m !== 'web');
  // 当前态可能不在可用列表中（如 currentMode='web' 但 ime.enabled 刚变 false），回退到 system
  let idx = modes.indexOf(currentMode);
  if (idx === -1) idx = modes.indexOf('system');
  const next = modes[(idx + 1) % modes.length];
  await setMode(next);
  if (currentMode === next) {
    showToast(MODE_TOAST[currentMode], 'info');
  }
}

export function shouldIntercept(e) {
  if (currentMode !== 'web' || !panelReady) return false;
  if (isMobile || !mgr || !mgr.getPanel()) return false;
  if (e.type !== 'keydown') return false;

  // Shift 由 setupShiftToggle 独立处理（exclusiveShift 逻辑），
  // 不进入 Rime 按键流，否则 toRimeKey 会生成错误的 {Shift+Shift}
  if (e.key === 'Shift') return false;

  if (e.ctrlKey && (e.key === 'c' || e.key === 'C' || e.key === 'v' || e.key === 'V')) return false;
  if (e.ctrlKey && e.key === 'Backspace') return false;
  if (e.ctrlKey && e.key === 'Enter') return false;

  const key = e.key;
  const isPrintableKey = isPrintable(key);
  const hasControl = e.ctrlKey || e.metaKey || e.altKey;
  const hasShift = e.shiftKey;
  const isShortcut = hasControl || (hasShift && !isPrintableKey);

  const panel = mgr.getPanel();
  if (!panel.editing) {
    if (!isPrintableKey && key !== 'F4') return false;
    if (isShortcut && !hasShift && !(e.ctrlKey && CONTROL_ALLOWLIST.includes(key))) return false;
  }
  return true;
}

export async function handleKeyAsync(e) {
  if (!panelReady || !mgr) return;
  const panel = mgr.getPanel();
  if (!panel) return;
  const rimeKey = toRimeKey(e);
  if (rimeKey === null) {
    debug('rime', 'toRimeKey null for key=%s', e.key);
    return;
  }
  const wasEditing = panel.editing;
  try {
    updatePosition();
    const result = await panel.handleKey(rimeKey);
    if (!result) return;
    if (result.state === 'unhandled' && !wasEditing && isPrintable(rimeKey)) {
      sendToTerminal(rimeKey);
    }
    debug('rime', 'key=%s rimeKey=%s state=%s wasEditing=%s',
      e.key, JSON.stringify(rimeKey), result.state, wasEditing);
  } catch (err) {
    error('rime', 'handleKey failed: %s', err && err.message || err);
  }
}

/**
 * 同步拦截 keydown：判断是否应由 Web RIME 处理，若是则阻止浏览器默认动作
 * 并异步交给 Rime 面板处理。
 *
 * 必须在 keydown 事件分发期间同步调用 e.preventDefault()，否则浏览器会把
 * 字符插入 xterm 的辅助 textarea，触发 input 事件（inputType="insertText"），
 * xterm.js 的 _inputEvent 会把原始字母通过 triggerDataEvent 发送到终端，
 * 导致 Web RIME 模式下输入 "nihao1" 显示成 "nihao1你好"
 * （原始字母泄漏 + Rime commit 的 "你好"）。
 *
 * @returns {boolean} true 表示已拦截，调用方应阻止 xterm.js 继续处理
 */
export function interceptKeyDown(e) {
  if (!shouldIntercept(e)) return false;
  e.preventDefault();
  handleKeyAsync(e);
  return true;
}

export function onTabSwitch() {
  applyInputMode();
  const panel = mgr && mgr.getPanel();
  if (panel && panel.editing) {
    updatePosition();
  }
  if (isMobile && currentMode === 'web' && panelReady) {
    scheduleKeyboardSync();
  }
}

export function onThemeChange(theme) {
  const t = theme === 'light' ? 'light' : 'dark';
  if (mgr) {
    const panel = mgr.getPanel();
    const keyboard = getActiveKeyboard();
    const toolbar = mgr.getToolbar();
    if (panel) { try { panel.setTheme(t); } catch (_) {} }
    if (keyboard) { try { keyboard.setTheme(t); } catch (_) {} }
    if (toolbar) { try { toolbar.setTheme(t); } catch (_) {} }
  }
}

/**
 * 安装 Shift 切换中英文监听（镜像 rime-plugin.js bindTarget 的 exclusiveShift 逻辑）。
 *
 * 桌面端 externalKeyHandling:true，rime-plugin 自身的 keydown/keyup 监听不会绑定到
 * #terminal-frame，因此 Shift 切换逻辑不会运行。此处独立监听 document 的
 * keydown/keyup（capture 阶段，先于 xterm 处理），实现等效行为：
 *
 * - Shift keydown（无 Ctrl/Alt/Meta）→ 标记 _exclusiveShift
 * - 任意其他 keydown → 清除标记（即 Shift 被用作修饰键，不视为独立 Shift）
 * - Shift keyup 且 _exclusiveShift 仍为 true → 独立 Shift，切换 ascii_mode
 *
 * 仅在 Web RIME 模式且非触摸端生效；切换后通过 toast 给出视觉反馈。
 */
function setupShiftToggle() {
  if (_shiftToggleInstalled) return;
  _shiftToggleInstalled = true;

  document.addEventListener('keydown', (e) => {
    if (currentMode !== 'web' || !panelReady || isMobile) return;
    if (e.key === 'Shift' && !e.ctrlKey && !e.altKey && !e.metaKey) {
      _exclusiveShift = true;
      return;
    }
    _exclusiveShift = false;
  }, true);

  document.addEventListener('keyup', (e) => {
    if (currentMode !== 'web' || !panelReady || isMobile) return;
    if (e.key !== 'Shift' || !_exclusiveShift) return;
    _exclusiveShift = false;
    const panel = mgr && mgr.getPanel();
    if (!panel) return;
    const ime = panel.getIME && panel.getIME();
    if (!ime) return;
    const newMode = !panel.isEnglish;
    // 与工具栏 btnLang 和键盘 lang 键行为一致：
    // 切换 ascii_mode 的同时，若未锁定标点则同步切换 ascii_punct
    ime.setOption('ascii_mode', newMode).then(() => {
      if (!ime.punctLocked) {
        return ime.setOption('ascii_punct', newMode);
      }
    }).then(() => {
      debug('rime', 'shift toggle ascii_mode=%s ascii_punct=%s', newMode, ime.punctLocked ? '(locked)' : newMode);
      // 切换到英文模式时，取消当前组词（清除候选词列表）
      // 当 panel.editing=true 时有活跃组词，切换英文后候选词面板不会自动消失，
      // 且由于 ascii_mode=true 后续按键不再走 RIME 流程，导致候选词列表悬空无法控制。
      // 此处发送 Escape 取消组词，与 TKL 键盘 isDirectHandleKey 路径的处理一致。
      if (newMode && panel.editing) {
        panel.handleKey('{Escape}');
      }
      showToast(newMode ? t('ime.toastEnglish') : t('ime.toastChinese'), 'info');
    }).catch((err) => {
      error('rime', 'shift toggle failed: %s', err && err.message || err);
    });
  }, true);
}

/**
 * 应用默认中英文状态（ime.defaultState）。
 * 在 ensurePanel 内 mgr.init() 之后调用。
 * - chinese: ascii_mode=false（中文输入）
 * - english: ascii_mode=true（英文输入）
 * - last:    读取 localStorage 上次状态，默认中文
 *
 * 同时注册 onOptionChange 持久化 ascii_mode 变更，供 'last' 模式恢复。
 */
const RIME_LAST_ASCII_KEY = 'pty_ime_last_ascii';
function _applyDefaultAsciiMode() {
  if (!mgr) return;
  const defaultState = settingsStore.get('ime.defaultState') || 'chinese';
  let asciiMode;
  if (defaultState === 'english') {
    asciiMode = true;
  } else if (defaultState === 'last') {
    asciiMode = localStorage.getItem(RIME_LAST_ASCII_KEY) === 'true';
  } else {
    // 'chinese' 或未知值
    asciiMode = false;
  }
  mgr.getIME().setOption('ascii_mode', asciiMode).catch((e) => {
    debug('rime', 'apply default ascii_mode=%s failed: %s', asciiMode, e);
  });
  debug('rime', 'apply default ascii_mode=%s (defaultState=%s)', asciiMode, defaultState);

  // 持久化 ascii_mode 变更（供 'last' 模式恢复）
  mgr.onOptionChange((option, value) => {
    if (option === 'ascii_mode') {
      localStorage.setItem(RIME_LAST_ASCII_KEY, String(!!value));
    }
  });
}

export async function init() {
  // ime.enabled 总开关：关闭时按钮仍显示，但循环跳过 web 态（4 态变 3 态）
  // 仅当 saved mode 为 web 且 ime.enabled=false 时，降级为 system
  const imeEnabled = settingsStore.get('ime.enabled') !== false;
  const saved = localStorage.getItem(RIME_MODE_KEY);
  if (saved && MODES.includes(saved)) {
    currentMode = (!imeEnabled && saved === 'web') ? 'system' : saved;
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', updateButton, { once: true });
  } else {
    updateButton();
  }
  setupTerminalFocusTracking();
  setupShiftToggle();
  if (currentMode === 'web') {
    try {
      await ensurePanel();
      const toolbar = mgr && mgr.getToolbar();
      const keyboard = getActiveKeyboard();
      if (toolbar) toolbar.getElement().style.display = '';
      if (isMobile && keyboard) syncKeyboardVisibility();
      updateButton();
    } catch (err) {
      currentMode = 'system';
      localStorage.setItem(RIME_MODE_KEY, 'system');
      updateButton();
    }
  }
  info('rime', 'rimeManager init mode=%s', currentMode);
}

/**
 * 应用 IME 设置变更（供 settingsStore 订阅者实时调用）。
 *
 * 可实时应用的设置：
 * - ime.defaultState：通过 setOption('ascii_mode') 立即切换
 * - ime.candidateCount：通过 getIME().setPageSize() 立即生效
 *
 * 需要重新进入 web 模式才生效的设置（涉及 RimeManager 重建）：
 * - ime.keyboardLayout：managerMode 变更（keyboard ↔ tkl+panel）
 * - ime.vertical：panel 布局变更
 * - ime.enabled：总开关，关闭时隐藏按钮并回退系统输入法
 *
 * @param {string} key 设置项 key
 * @param {*} value 新值
 */
export function applyImeSetting(key, value) {
  // ime.enabled 总开关：关闭时按钮不隐藏，但循环跳过 web 态（4 态变 3 态）
  // 若当前正停留在 web 态，需切回 system 并销毁 RimeManager 释放资源
  if (key === 'ime.enabled') {
    if (value === false) {
      // Web RIME 关闭：若当前在 web 态，切回 system（隐藏 toolbar/panel，不拦截按键）
      if (currentMode === 'web') {
        currentMode = 'system';
        localStorage.setItem(RIME_MODE_KEY, 'system');
        const toolbar = mgr && mgr.getToolbar();
        const keyboard = getActiveKeyboard();
        const panel = mgr && mgr.getPanel();
        if (toolbar) toolbar.getElement().style.display = 'none';
        if (isMobile && keyboard) { keyboard.hide(); keyboardHiddenByBlur = false; }
        if (panel) panel.hide();
        updateButton();
      }
      // 销毁 RimeManager 释放 WASM/DOM 资源（下次切到 web 态时 ensurePanel 会重建）
      if (mgr) {
        try { mgr.destroy(); } catch (_) {}
        mgr = null;
        panelReady = false;
        panelInitPromise = null;
      }
      info('rime', 'applyImeSetting: ime.enabled=false, web mode exited & mgr destroyed (3-state cycle)');
    } else {
      // Web RIME 开启：按钮本就在显示，cycleMode 会恢复 4 态循环（含 web）
      // 用户手动 cycle 到 web 态时 ensurePanel 会重建 RimeManager，此处无需主动重建
      info('rime', 'applyImeSetting: ime.enabled=true, 4-state cycle restored');
    }
    return;
  }

  if (!panelReady || !mgr) {
    // 面板未就绪：设置已缓存，下次 ensurePanel 时读取
    debug('rime', 'applyImeSetting(%s=%s) deferred: panel not ready', key, value);
    return;
  }
  // 外观设置（透明度/大小/工具栏显示）— 实时应用，不需要重建 mgr
  if (key === 'ime.toolbarDisplay' || key === 'ime.tbOpacity' ||
      key === 'ime.kbOpacity' || key === 'ime.tbScale' || key === 'ime.kbScale' ||
      key === 'ime.panelOpacity' || key === 'ime.panelScale') {
    applyImeAppearance();
    debug('rime', 'applyImeSetting: appearance %s=%s applied', key, value);
    return;
  }
  if (key === 'ime.defaultState') {
    // 立即切换中英文状态
    let asciiMode;
    if (value === 'english') asciiMode = true;
    else if (value === 'last') asciiMode = localStorage.getItem(RIME_LAST_ASCII_KEY) === 'true';
    else asciiMode = false;
    mgr.getIME().setOption('ascii_mode', asciiMode).catch((e) => {
      debug('rime', 'applyImeSetting ascii_mode=%s failed: %s', asciiMode, e);
    });
    // 切换到英文模式时，取消当前组词（与 setupShiftToggle 一致）
    if (asciiMode) {
      const panel = mgr && mgr.getPanel();
      if (panel && panel.editing) {
        panel.handleKey('{Escape}');
      }
    }
    debug('rime', 'applyImeSetting: ascii_mode=%s (defaultState=%s)', asciiMode, value);
  } else if (key === 'ime.candidateCount') {
    // 实时调整候选词数量
    const size = Number(value) || 5;
    mgr.getIME().setPageSize(size).catch((e) => {
      debug('rime', 'applyImeSetting pageSize=%d failed: %s', size, e);
    });
    debug('rime', 'applyImeSetting: pageSize=%d', size);
  } else if (key === 'ime.keyboardLayout' || key === 'ime.vertical') {
    // 涉及 managerMode（keyboard ↔ tkl+panel）或 panel 布局变更，需销毁并重建 RimeManager
    // 无论当前是否在 web 态，都销毁 mgr（panelReady=false），
    // 这样下次 setMode('web') 会重新 ensurePanel 读取新值重建；
    // 若当前已在 web 态，立即触发 ensurePanel 重建
    if (mgr) {
      try { mgr.destroy(); } catch (_) {}
      mgr = null;
      panelReady = false;
      panelInitPromise = null;
      if (currentMode === 'web') {
        // 当前在 web 态：立即异步重建（ensurePanel 会读取最新的 keyboardLayout/vertical）
        // 重建后需恢复 UI 显示（toolbar/keyboard），因为 ensurePanel 初始会隐藏 toolbar
        ensurePanel().then(() => {
          // 恢复 UI 显示：toolbar 显隐必须走 applyToolbarDisplay，
          // 否则会覆盖 ime.toolbarDisplay=never 设置的 display:none（bug: 切换键盘方案后语言栏又出现）
          if (mgr && mgr.getToolbar()) applyToolbarDisplay();
          const keyboard = getActiveKeyboard();
          if (isMobile && keyboard) syncKeyboardVisibility();
          updateButton();
          info('rime', 'applyImeSetting: %s rebuild done, UI restored', key);
        }).catch((e) => {
          error('rime', 'rebuild after %s change failed: %s', key, e && e.message || e);
        });
        info('rime', 'applyImeSetting: %s changed, RimeManager rebuilding', key);
      } else {
        info('rime', 'applyImeSetting: %s changed, mgr destroyed (will rebuild on next web mode)', key);
      }
    } else {
      debug('rime', 'applyImeSetting(%s=%s) deferred: no mgr to rebuild', key, value);
    }
  }
}

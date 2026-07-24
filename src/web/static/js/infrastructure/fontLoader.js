/**
 * 基础设施：终端字体加载器
 *
 * 负责异步加载 MapleMono 字体资源并应用到终端。
 * 字体文件位于 /static/fonts/，包含 Regular/Bold/Italic/BoldItalic 四个 woff2 文件。
 *
 * 加载策略：
 * - 注入 @font-face CSS（font-display: swap，浏览器按需加载具体字重/样式）
 * - document.fonts.load() 等待字体就绪后再应用到终端，避免回退闪烁
 * - 多次调用共享同一个 loading promise，不重复加载
 *
 * 依赖方向：基础设施层 → 领域层 / 应用层（读 settingsStore）
 */

import { state } from '../domain/state.js';
import { debug, info, warn, error } from '../domain/logger.js';
import * as settingsStore from '../application/settingsStore.js';
import { applyTerminalFrameSize } from './terminal/scale.js';

// MapleMono 字体族名（对应 @font-face 中声明的 font-family）
const MAPLE_MONO_FONT_FAMILY = 'MapleMono NF CN';
// 默认终端字体族（lifecycle.js 原硬编码值）
const DEFAULT_FONT_FAMILY = "'Cascadia Mono', 'Cascadia Code', 'Consolas', 'Courier New', monospace";

// @font-face 是否已注入（避免重复注入）
let _styleInjected = false;
// 正在进行的加载 promise（共享，避免重复加载）
let _loadingPromise = null;

/**
 * 注入 MapleMono @font-face CSS（仅一次）。
 * 使用 font-display: swap，未加载完成时回退到默认字体。
 */
function _injectFontFace() {
  if (_styleInjected) return;
  const style = document.createElement('style');
  style.id = 'maple-mono-fontface';
  style.textContent = `
@font-face {
  font-family: '${MAPLE_MONO_FONT_FAMILY}';
  src: url('/static/fonts/MapleMono-NF-CN-Regular.woff2') format('woff2');
  font-weight: 400;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: '${MAPLE_MONO_FONT_FAMILY}';
  src: url('/static/fonts/MapleMono-NF-CN-Bold.woff2') format('woff2');
  font-weight: 700;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: '${MAPLE_MONO_FONT_FAMILY}';
  src: url('/static/fonts/MapleMono-NF-CN-Italic.woff2') format('woff2');
  font-weight: 400;
  font-style: italic;
  font-display: swap;
}
@font-face {
  font-family: '${MAPLE_MONO_FONT_FAMILY}';
  src: url('/static/fonts/MapleMono-NF-CN-BoldItalic.woff2') format('woff2');
  font-weight: 700;
  font-style: italic;
  font-display: swap;
}`;
  document.head.appendChild(style);
  _styleInjected = true;
  debug('font', '@font-face injected for %s', MAPLE_MONO_FONT_FAMILY);
}

/**
 * 异步加载 MapleMono 字体。
 * 注入 @font-face 后通过 document.fonts.load() 等待浏览器完成加载。
 * 多次调用共享同一个 loading promise，不会重复加载。
 *
 * @returns {Promise<void>} 加载完成后 resolve；失败则 reject 并允许重试
 */
export async function ensureMapleMonoLoaded() {
  if (_loadingPromise) return _loadingPromise;
  _injectFontFace();
  _loadingPromise = (async () => {
    try {
      // document.fonts.load 需要指定 "字号 字体族"，浏览器会异步加载对应字体
      // 分别加载 normal 和 bold 两个字重（italic 由浏览器按需加载）
      await Promise.all([
        document.fonts.load(`16px "${MAPLE_MONO_FONT_FAMILY}"`),
        document.fonts.load(`bold 16px "${MAPLE_MONO_FONT_FAMILY}"`),
      ]);
      info('font', 'MapleMono loaded successfully');
    } catch (e) {
      error('font', 'MapleMono load failed: %s', e && e.message || e);
      _loadingPromise = null;  // 失败后允许重试
      throw e;
    }
  })();
  return _loadingPromise;
}

/**
 * 根据当前设置返回终端字体族字符串。
 * 供 lifecycle.js 创建终端时使用。
 *
 * @returns {string} CSS font-family 值
 */
export function getTerminalFontFamily() {
  const font = settingsStore.get('basic.terminalFont') || 'default';
  if (font === 'maple-mono') {
    return `'${MAPLE_MONO_FONT_FAMILY}', ${DEFAULT_FONT_FAMILY}`;
  }
  return DEFAULT_FONT_FAMILY;
}

/**
 * 将字体应用到单个终端实例。
 * 设置 term.options.fontFamily 后，cell 尺寸可能变化，需重新计算 frame 尺寸。
 *
 * @param {string} sid 会话 id
 */
function _applyFontToSession(sid) {
  const inst = state.termInstances[sid];
  if (!inst || !inst.term) return;
  try {
    inst.term.options.fontFamily = getTerminalFontFamily();
    // 字体变更后 cell 像素尺寸可能变化，需重新计算 frame 尺寸
    requestAnimationFrame(() => {
      try { applyTerminalFrameSize(sid); } catch (_) {}
    });
    debug('font', 'font applied to sid=%s', sid);
  } catch (e) {
    warn('font', 'apply font to sid=%s failed: %s', sid, e);
  }
}

/**
 * 将当前字体设置应用到所有已打开的终端实例。
 * 供设置变更和字体加载完成后调用。
 */
export function applyTerminalFontAll() {
  for (const sid of Object.keys(state.termInstances)) {
    _applyFontToSession(sid);
  }
  info('font', 'terminal font applied to all sessions: %s',
    settingsStore.get('basic.terminalFont') || 'default');
}

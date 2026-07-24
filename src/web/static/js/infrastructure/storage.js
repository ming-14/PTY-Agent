/**
 * 基础设施层：浏览器存储适配器
 *
 * 封装 localStorage / sessionStorage 等外部持久化机制。
 * 主题、侧边栏等 UI 状态的读写均通过本模块，避免散落的 localStorage 调用。
 */

import { DARK_THEME, LIGHT_THEME, MIN_SIDEBAR_WIDTH, MAX_SIDEBAR_WIDTH } from '../domain/constants.js';
import { state } from '../domain/state.js';
import { $ } from './domUtils.js';

/**
 * 设置 body 主题（dark/light/system）。
 * system 主题根据 prefers-color-scheme 解析为实际 dark/light。
 * @param {string} theme 'dark' | 'light' | 'system'
 */
export function setBodyTheme(theme) {
  let actual = theme;
  if (theme === 'system') {
    actual = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.body.dataset.theme = actual;
}

export function isDarkTheme() {
  return document.body.dataset.theme === 'dark';
}

export function currentTheme() {
  return document.body.dataset.theme === 'dark' ? DARK_THEME : LIGHT_THEME;
}

export function applySidebarWidth() {
  const sb = $('sidebar');
  if (!sb || state.sidebarCollapsed) return;
  const w = Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, state.sidebarWidth));
  sb.style.width = w + 'px';
  sb.style.minWidth = w + 'px';
  sb.style.maxWidth = MAX_SIDEBAR_WIDTH + 'px';
}

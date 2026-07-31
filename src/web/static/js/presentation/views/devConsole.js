/**
 * 表现层：悬浮日志视窗
 *
 * 受设置项 developer.logPanelEnabled 控制：开启时挂载视窗，关闭时卸载。
 * 采集前端 domain/logger.js 输出的全部 debug/info/warn/error 日志（环形缓冲区）。
 *
 * 功能：
 *   - 等级过滤（DEBUG/INFO/WARN/ERROR 多选，默认全开）
 *   - 标签过滤（动态生成已见 tag chip，多选，默认全开）
 *   - 搜索（纯文本子串 + 正则开关，语法错误回退子串并标红）
 *   - 暂停/继续滚动（暂停时不追加渲染，但日志仍入缓冲）
 *   - 自动滚到底（默认开；关闭时保持当前滚动位置）
 *   - 计数徽标（显示条数 / 缓冲总条数）
 *   - 清空缓冲区、复制当前过滤结果、导出 txt
 *   - 拖拽标题栏移动、右下角缩放，位置/大小持久化到 localStorage
 *
 * 依赖方向：表现层 → 应用层（settingsStore）→ 领域层（logger）；不反向依赖。
 */

import { $, showToast } from '../../infrastructure/domUtils.js';
import {
  subscribe,
  unsubscribe,
  getEntries,
  clearBuffer,
  getBufferSize,
  getBufferCapacity,
} from '../../domain/logger.js';
import { debug, info, warn } from '../../domain/logger.js';
import * as settingsStore from '../../application/settingsStore.js';

// ── 常量 ──
const GEOM_STORAGE_KEY = 'pty_devconsole_geom';
const AUTOSCROLL_STORAGE_KEY = 'pty_devconsole_autoscroll';
const LEVEL_FILTER_KEY = 'pty_devconsole_level_filter';
const TAG_FILTER_KEY = 'pty_devconsole_tag_filter';
const LEVEL_DEFS = [
  { level: 0, name: 'DEBUG' },
  { level: 1, name: 'INFO' },
  { level: 2, name: 'WARN' },
  { level: 3, name: 'ERROR' },
];

// ── 视窗状态（仅在挂载后有效） ──
let _mounted = false;
let _loggerCb = null;          // logger 订阅回调
let _rootEl = null;            // 视窗根元素
let _bodyEl = null;            // 日志列表容器
let _statusEl = null;          // 标题栏计数
let _tagGroupEl = null;        // tag chip 容器
let _searchInputEl = null;     // 搜索输入框
let _searchWrapEl = null;      // 搜索框外层（用于标红）
let _regexBtnEl = null;        // 正则开关按钮
let _autoScrollBtnEl = null;   // 自动滚动按钮
let _pauseBtnEl = null;        // 暂停按钮
let _tagToggleEl = null;       // 全选/取消按钮

// 过滤状态
let _levelFilter = new Set([0, 1, 2, 3]);   // 默认全开
let _tagFilter = new Set();                  // 空集合表示「全开」
let _searchText = '';
let _useRegex = false;
let _regexValid = true;

// 从 localStorage 恢复筛选状态
try {
  const lf = localStorage.getItem(LEVEL_FILTER_KEY);
  if (lf) {
    const arr = JSON.parse(lf);
    if (Array.isArray(arr) && arr.length > 0) _levelFilter = new Set(arr);
  }
} catch (_) {}
try {
  const tf = localStorage.getItem(TAG_FILTER_KEY);
  if (tf) {
    const arr = JSON.parse(tf);
    if (Array.isArray(arr)) _tagFilter = new Set(arr);
  }
} catch (_) {}

// 运行状态
let _paused = false;
let _autoScroll = true;
let _autoScrollUserEnabled = false;  // true=用户手动开启的，不自动关闭
// 从 localStorage 恢复自动滚动偏好
try {
  const saved = localStorage.getItem(AUTOSCROLL_STORAGE_KEY);
  if (saved !== null) _autoScroll = saved === 'true';
} catch (_) {}
let _seenTags = new Set();                    // 已见 tag 集合（生成 chip 用）

/**
 * 初始化：根据设置项决定是否挂载视窗。
 * 由 app.js 启动时调用。
 */
export function initDevConsole() {
  if (settingsStore.get('developer.logPanelEnabled')) {
    mount();
  }
}

/**
 * 挂载视窗（构建 DOM + 订阅 logger + 回放缓冲）。
 */
export function mount() {
  if (_mounted) return;
  const root = $('devconsole-root');
  if (!root) {
    warn('devconsole', 'mount: #devconsole-root not found');
    return;
  }
  _buildDom(root);
  _loadGeometry();
  _applyWindowOpacity();
  _bindDevSettings();
  _bindDrag();
  _bindResize();
  _bindToolbar();
  _bindViewportClamp();

  // 智能自动滚动：用户向上滚动时自动关闭，手动开启后不再自动关闭
  _bindAutoScrollSmart();

  // 订阅 logger：实时接收新日志
  _loggerCb = (entry) => {
    if (entry && entry.type === 'clear') {
      _seenTags.clear();
      _rebuildTagChips();
      _rerenderAll();
      return;
    }
    _onNewEntry(entry);
  };
  subscribe(_loggerCb);

  // 回放已有缓冲区（首次打开能看到历史日志）
  const all = getEntries();
  for (const e of all) {
    _seenTags.add(e.tag);
  }
  _rebuildTagChips();
  _rerenderAll();
  // 回放后滚到底
  _scrollToBottom();

  _mounted = true;
  info('devconsole', 'mounted (replayed %d entries)', all.length);
}

/**
 * 卸载视窗（移除 DOM + 取消订阅，状态归位）。
 */
export function unmount() {
  if (!_mounted) return;
  if (_loggerCb) {
    unsubscribe(_loggerCb);
    _loggerCb = null;
  }
  if (_rootEl && _rootEl.parentNode) {
    _rootEl.parentNode.removeChild(_rootEl);
  }
  _rootEl = _bodyEl = _statusEl = _tagGroupEl = null;
  _searchInputEl = _searchWrapEl = _regexBtnEl = null;
  _autoScrollBtnEl = _pauseBtnEl = _tagToggleEl = null;
  _seenTags.clear();
  _levelFilter = new Set([0, 1, 2, 3]);
  _tagFilter = new Set();
  _searchText = '';
  _useRegex = false;
  _paused = false;
  _autoScroll = true;
  _mounted = false;
  _unbindDevSettings();
  _unbindViewportClamp();
  info('devconsole', 'unmounted');
}

/**
 * 设置视窗可见性（供 app.js 订阅 developer.logPanelEnabled 调用）。
 */
export function setVisible(visible) {
  if (visible) mount();
  else unmount();
}

// ──────────────────────────────────────────────
// DOM 构建
// ──────────────────────────────────────────────

function _buildDom(root) {
  const el = document.createElement('div');
  el.className = 'devconsole';
  el.innerHTML =
    '<div class="devconsole-titlebar">' +
      '<div class="dc-title">' +
        '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 4L2 8l3.5 4M10.5 4L14 8l-3.5 4"/></svg>' +
        '<span>日志视窗</span>' +
      '</div>' +
      '<span class="dc-status" id="dc-status">0 / 0</span>' +
      '<div class="dc-spacer"></div>' +
      '<button class="dc-iconbtn dc-pause" id="dc-pause" title="暂停/继续接收新日志">' + _ICON_PAUSE + '</button>' +
      '<button class="dc-iconbtn dc-autoscroll" id="dc-autoscroll" title="自动滚到底部">' + _ICON_AUTOSCROLL_ON + '</button>' +
      '<button class="dc-iconbtn dc-close" id="dc-close" title="关闭">' + _ICON_CLOSE + '</button>' +
    '</div>' +
    '<div class="devconsole-toolbar">' +
      '<div class="dc-level-group" id="dc-level-group"></div>' +
      '<div class="dc-divider"></div>' +
      '<div class="dc-search" id="dc-search">' +
        '<input type="text" id="dc-search-input" placeholder="搜索日志（支持正则）…" autocomplete="off" spellcheck="false">' +
        '<button class="dc-regex-btn" id="dc-regex-btn" title="切换正则模式">.*</button>' +
      '</div>' +
      '<div class="dc-actions">' +
        '<button class="dc-btn" id="dc-clear" title="清空日志缓冲区">' + _ICON_CLEAR + '清空</button>' +
        '<button class="dc-btn" id="dc-copy" title="复制当前过滤结果到剪贴板">' + _ICON_COPY + '复制</button>' +
        '<button class="dc-btn" id="dc-export" title="导出为 txt 文件">' + _ICON_EXPORT + '导出</button>' +
      '</div>' +
    '</div>' +
    '<div class="devconsole-body" id="dc-body">' +
      '<div class="devconsole-left" id="dc-left">' +
        '<div class="dc-left-section">' +
          '<div class="dc-left-label">模块筛选</div>' +
          '<div class="dc-left-tag-toggle" id="dc-left-tag-toggle">全选</div>' +
          '<div class="dc-left-tags" id="dc-left-tags"></div>' +
        '</div>' +
        '<div class="dc-left-spacer"></div>' +
        '<div class="dc-left-section">' +
          '<div class="dc-left-label">统计</div>' +
          '<div class="dc-left-levels" id="dc-left-levels"></div>' +
          '<div class="dc-left-buffer" id="dc-left-buffer"></div>' +
          '<div class="dc-left-bar" id="dc-left-bar"><div class="dc-left-bar-fill" id="dc-left-bar-fill"></div></div>' +
        '</div>' +
      '</div>' +
      '<div class="devconsole-right" id="dc-right"></div>' +
    '</div>' +
    '<div class="devconsole-resize" id="dc-resize"></div>';

  root.appendChild(el);
  _rootEl = el;
  _bodyEl = el.querySelector('#dc-right');
  _statusEl = el.querySelector('#dc-status');
  _tagGroupEl = el.querySelector('#dc-left-tags');
  _searchInputEl = el.querySelector('#dc-search-input');
  _searchWrapEl = el.querySelector('#dc-search');
  _regexBtnEl = el.querySelector('#dc-regex-btn');
  _autoScrollBtnEl = el.querySelector('#dc-autoscroll');
  _pauseBtnEl = el.querySelector('#dc-pause');
  _tagToggleEl = el.querySelector('#dc-left-tag-toggle');

  _buildLevelChips();
  _syncAutoScrollIcon();
  _syncPauseIcon();
  _updateLeftPanel();
}

function _buildLevelChips() {
  const group = _rootEl.querySelector('#dc-level-group');
  group.innerHTML = '';
  for (const def of LEVEL_DEFS) {
    const chip = document.createElement('div');
    chip.className = 'dc-chip' + (_levelFilter.has(def.level) ? ' active' : '');
    chip.dataset.level = String(def.level);
    chip.textContent = def.name;
    chip.onclick = () => {
      if (_levelFilter.has(def.level)) _levelFilter.delete(def.level);
      else _levelFilter.add(def.level);
      chip.classList.toggle('active', _levelFilter.has(def.level));
      _saveLevelFilter();
      _rerenderAll();
    };
    group.appendChild(chip);
  }
}

/**
 * 根据已见 tag 重建左栏 tag 筛选列表。
 * 纵向显示，每个 tag 一行，点击切换显隐。
 * _tagFilter 记录当前隐藏的 tag 集合（空 = 全部显示）。
 */
function _rebuildTagChips() {
  if (!_tagGroupEl) return;
  const tags = Array.from(_seenTags).sort();
  _tagGroupEl.innerHTML = '';
  for (const tag of tags) {
    const on = _isTagOn(tag);
    const chip = document.createElement('div');
    chip.className = 'dc-left-tag-row' + (on ? ' active' : '');
    chip.dataset.tag = tag;
    chip.innerHTML = '<span class="dc-left-tag-dot" style="color:' + _tagColor(tag) + '">●</span>' +
      '<span class="dc-left-tag-name">' + tag + '</span>';
    chip.onclick = () => {
      if (_tagFilter.has(tag)) {
        _tagFilter.delete(tag);
      } else {
        _tagFilter.add(tag);
      }
      _saveTagFilter();
      _rebuildTagChips();
      _rerenderAll();
    };
    _tagGroupEl.appendChild(chip);
  }
  // 更新全选按钮文字
  if (_tagToggleEl) {
    const allHidden = tags.length > 0 && tags.every(t => _tagFilter.has(t));
    _tagToggleEl.textContent = allHidden ? '全选' : '取消全选';
    _tagToggleEl.style.display = tags.length > 1 ? '' : 'none';
  }
}

function _isTagOn(tag) {
  if (_tagFilter.size === 0) return true;   // 空 = 全显示
  return !_tagFilter.has(tag);              // 不在隐藏集合中 = 显示
}

function _tagColor(tag) {
  // 复用 logger 的 TAG_COLORS：内联为简单映射，避免循环依赖
  const map = {
    terminal: '#4CAF50', mouse: '#FF9800', key: '#2196F3', ws: '#9C27B0',
    ui: '#00BCD4', session: '#FF5722', scroll: '#795548', paste: '#E91E63',
    cursor: '#9CCC65', touch: '#BA68C8', settings: '#FFD54F', app: '#90A4AE',
    fs: '#26C6DA', vnc: '#7E57C2', devconsole: '#FF7043',
    font: '#AB47BC', rime: '#EC407A', rimeTouch: '#EF5350',
    console: '#607D8B',
    default: '#888',
  };
  return map[tag] || map[tag.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] || map.default;
}

// ──────────────────────────────────────────────
// 拖拽 / 缩放 / 几何持久化
// ──────────────────────────────────────────────

function _loadGeometry() {
  let geom = null;
  try {
    const raw = localStorage.getItem(GEOM_STORAGE_KEY);
    if (raw) geom = JSON.parse(raw);
  } catch (_) {}
  if (!geom || typeof geom !== 'object') geom = {};
  const vw = window.innerWidth, vh = window.innerHeight;
  const w = _clamp(geom.w || 560, 320, vw - 20);
  const h = _clamp(geom.h || 360, 200, vh - 20);
  const x = _clamp(geom.x != null ? geom.x : Math.max(20, vw - w - 20), 4, Math.max(4, vw - w - 4));
  const y = _clamp(geom.y != null ? geom.y : Math.max(20, vh - h - 20), 4, Math.max(4, vh - h - 4));
  _rootEl.style.left = x + 'px';
  _rootEl.style.top = y + 'px';
  _rootEl.style.width = w + 'px';
  _rootEl.style.height = h + 'px';
  if (geom.zoom != null) {
    _rootEl.style.zoom = geom.zoom;
  }
}

function _saveGeometry() {
  const z = _rootEl.style.zoom;
  try {
    localStorage.setItem(GEOM_STORAGE_KEY, JSON.stringify({
      x: parseInt(_rootEl.style.left) || 0,
      y: parseInt(_rootEl.style.top) || 0,
      w: parseInt(_rootEl.style.width) || 560,
      h: parseInt(_rootEl.style.height) || 360,
      zoom: z ? parseFloat(z) : 1,
    }));
  } catch (e) {
    warn('devconsole', 'saveGeometry failed: %s', e);
  }
}

function _bindDrag() {
  const titlebar = _rootEl.querySelector('.devconsole-titlebar');
  let startX = 0, startY = 0, startLeft = 0, startTop = 0;

  function _dragZoom() {
    const z = _rootEl.style.zoom;
    return z ? parseFloat(z) : 1;
  }

  const onMove = (cx, cy) => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const r = _rootEl.getBoundingClientRect();
    const z = _dragZoom();
    // startLeft/startTop 是 CSS 坐标，鼠标位移需除以 zoom 换算
    let nx = startLeft + (cx - startX) / z;
    let ny = startTop + (cy - startY) / z;
    // 极限位置也需换算为 CSS 坐标
    nx = _clamp(nx, 0, (vw - r.width) / z);
    ny = _clamp(ny, 0, (vh - r.height) / z);
    _rootEl.style.left = nx + 'px';
    _rootEl.style.top = ny + 'px';
  };
  const onUp = () => {
    _rootEl.classList.remove('dragging');
    document.removeEventListener('mousemove', mm);
    document.removeEventListener('mouseup', mu);
    document.removeEventListener('touchmove', tm);
    document.removeEventListener('touchend', te);
    _saveGeometry();
  };
  const mm = (e) => onMove(e.clientX, e.clientY);
  const mu = onUp;
  const tm = (e) => { if (e.touches.length === 1) { e.preventDefault(); onMove(e.touches[0].clientX, e.touches[0].clientY); } };
  const te = onUp;

  titlebar.addEventListener('mousedown', (e) => {
    // 不拦截标题栏内的按钮点击
    if (e.target.closest('.dc-iconbtn')) return;
    e.preventDefault();
    _rootEl.classList.add('dragging');
    startX = e.clientX; startY = e.clientY;
    startLeft = parseInt(_rootEl.style.left) || 0;
    startTop = parseInt(_rootEl.style.top) || 0;
    document.addEventListener('mousemove', mm);
    document.addEventListener('mouseup', mu);
  });
  // 右键标题栏弹出菜单
  titlebar.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const menu = document.getElementById('context-menu');
    if (!menu) return;
    menu.innerHTML = '' +
      '<div class="context-menu-item danger" data-action="dc-close">关闭日志视窗</div>';
    menu.style.display = '';
    menu.style.left = Math.min(e.clientX, window.innerWidth - menu.offsetWidth - 4) + 'px';
    menu.style.top = Math.min(e.clientY, window.innerHeight - menu.offsetHeight - 4) + 'px';
    // 关闭时隐藏菜单
    const closeHandler = (ev) => {
      if (!menu.contains(ev.target)) {
        menu.style.display = 'none';
        document.removeEventListener('click', closeHandler);
      }
    };
    setTimeout(() => document.addEventListener('click', closeHandler), 0);
    // 菜单项点击
    const item = menu.querySelector('[data-action="dc-close"]');
    if (item) {
      item.onclick = () => {
        menu.style.display = 'none';
        settingsStore.set('developer.logPanelEnabled', false);
      };
    }
  });
  titlebar.addEventListener('touchstart', (e) => {
    if (e.target.closest('.dc-iconbtn')) return;
    if (e.touches.length !== 1) return;
    const t = e.touches[0];
    _rootEl.classList.add('dragging');
    startX = t.clientX; startY = t.clientY;
    startLeft = parseInt(_rootEl.style.left) || 0;
    startTop = parseInt(_rootEl.style.top) || 0;
    document.addEventListener('touchmove', tm, { passive: false });
    document.addEventListener('touchend', te);
  });
}

function _bindResize() {
  const handle = _rootEl.querySelector('.devconsole-resize');
  let startX = 0, startY = 0, startW = 0, startH = 0;

  function _getZoom() {
    const z = _rootEl.style.zoom;
    return z ? parseFloat(z) : 1;
  }

  const onMove = (cx, cy) => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const r = _rootEl.getBoundingClientRect();
    const z = _getZoom();
    let nw = _clamp(startW + (cx - startX), 320, vw - r.left - 4);
    let nh = _clamp(startH + (cy - startY), 200, vh - r.top - 4);
    _rootEl.style.width = (nw / z) + 'px';
    _rootEl.style.height = (nh / z) + 'px';
  };
  const onUp = () => {
    _rootEl.classList.remove('resizing');
    document.removeEventListener('mousemove', mm);
    document.removeEventListener('mouseup', mu);
    document.removeEventListener('touchmove', tm);
    document.removeEventListener('touchend', te);
    _saveGeometry();
    // 尺寸变化后若自动滚动开启，保持贴底
    if (_autoScroll) _scrollToBottom();
  };
  const mm = (e) => onMove(e.clientX, e.clientY);
  const mu = onUp;
  const tm = (e) => { if (e.touches.length === 1) { e.preventDefault(); onMove(e.touches[0].clientX, e.touches[0].clientY); } };
  const te = onUp;

  handle.addEventListener('mousedown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    _rootEl.classList.add('resizing');
    startX = e.clientX; startY = e.clientY;
    const r = _rootEl.getBoundingClientRect();
    startW = r.width; startH = r.height;
    document.addEventListener('mousemove', mm);
    document.addEventListener('mouseup', mu);
  });
  handle.addEventListener('touchstart', (e) => {
    if (e.touches.length !== 1) return;
    e.preventDefault();
    e.stopPropagation();
    const t = e.touches[0];
    _rootEl.classList.add('resizing');
    startX = t.clientX; startY = t.clientY;
    const r = _rootEl.getBoundingClientRect();
    startW = r.width; startH = r.height;
    document.addEventListener('touchmove', tm, { passive: false });
    document.addEventListener('touchend', te);
  });
}

/**
 * 智能自动滚动：当用户向上滚动日志列表时自动关闭自动滚动；
 * 若用户手动开启自动滚动后，不再自动关闭。
 */
function _bindAutoScrollSmart() {
  if (!_bodyEl) return;
  let isUserScroll = false;
  _bodyEl.addEventListener('scroll', () => {
    if (!_bodyEl) return;
    if (!_autoScroll || _autoScrollUserEnabled) return;
    // 仅首次向上滚动时触发
    const atBottom = _bodyEl.scrollHeight - _bodyEl.scrollTop - _bodyEl.clientHeight < 5;
    if (!atBottom && !isUserScroll) {
      isUserScroll = true;
      _autoScroll = false;
      _syncAutoScrollIcon();
      _autoScrollBtnEl.title = '开启自动滚动';
      try { localStorage.setItem(AUTOSCROLL_STORAGE_KEY, String(_autoScroll)); } catch (_) {}
    }
    // 滚回底部时重置标记（下次向上滚动仍可触发）
    if (atBottom) {
      isUserScroll = false;
    }
  });
}

/**
 * 窗口大小变化时确保视窗不溢出屏幕。
 */
let _resizeHandler = null;
function _bindViewportClamp() {
  if (_resizeHandler) return;
  _resizeHandler = () => {
    if (!_rootEl) return;
    const vw = window.innerWidth, vh = window.innerHeight;
    const r = _rootEl.getBoundingClientRect();
    const z = parseFloat(_rootEl.style.zoom) || 1;
    // 若超出右边界或下边界，修正 CSS left/top
    let changed = false;
    let left = parseInt(_rootEl.style.left) || 0;
    let top = parseInt(_rootEl.style.top) || 0;
    const maxLeft = Math.max(0, (vw - r.width) / z);
    const maxTop = Math.max(0, (vh - r.height) / z);
    if (left > maxLeft) { left = maxLeft; changed = true; }
    if (top > maxTop) { top = maxTop; changed = true; }
    if (left < 0) { left = 0; changed = true; }
    if (top < 0) { top = 0; changed = true; }
    if (changed) {
      _rootEl.style.left = left + 'px';
      _rootEl.style.top = top + 'px';
      _saveGeometry();
    }
  };
  window.addEventListener('resize', _resizeHandler);
}

function _unbindViewportClamp() {
  if (_resizeHandler) {
    window.removeEventListener('resize', _resizeHandler);
    _resizeHandler = null;
  }
}

// ──────────────────────────────────────────────
// 工具栏交互绑定
// ──────────────────────────────────────────────

function _bindToolbar() {
  // 关闭按钮：反向写回设置项（保持开关与视窗状态同步）
  _rootEl.querySelector('#dc-close').onclick = () => {
    debug('devconsole', 'close btn → developer.logPanelEnabled=false');
    settingsStore.set('developer.logPanelEnabled', false);
  };

  // 全选/取消全选 tag 筛选
  if (_tagToggleEl) {
    _tagToggleEl.onclick = () => {
      const tags = Array.from(_seenTags);
      if (tags.length === 0) return;
      const allHidden = tags.every(t => _tagFilter.has(t));
      if (allHidden) {
        // 全选：清空隐藏集合
        _tagFilter = new Set();
      } else {
        // 取消全选：隐藏所有 tag
        _tagFilter = new Set(tags);
      }
      _saveTagFilter();
      _rebuildTagChips();
      _rerenderAll();
    };
  }

  // 暂停/继续
  _pauseBtnEl.onclick = () => {
    _paused = !_paused;
    _syncPauseIcon();
    _pauseBtnEl.title = _paused ? '继续接收新日志' : '暂停接收新日志';
    _updateStatus();
  };

  // 自动滚到底
  _autoScrollBtnEl.onclick = () => {
    _autoScroll = !_autoScroll;
    _autoScrollUserEnabled = _autoScroll;  // 用户手动操作后标记为用户启用
    _syncAutoScrollIcon();
    _autoScrollBtnEl.title = _autoScroll ? '关闭自动滚动' : '开启自动滚动';
    try { localStorage.setItem(AUTOSCROLL_STORAGE_KEY, String(_autoScroll)); } catch (_) {}
    if (_autoScroll) _scrollToBottom();
  };

  // 搜索输入
  _searchInputEl.addEventListener('input', () => {
    _searchText = _searchInputEl.value;
    _validateRegex();
    _rerenderAll();
  });

  // 正则开关
  _regexBtnEl.onclick = () => {
    _useRegex = !_useRegex;
    _regexBtnEl.classList.toggle('active', _useRegex);
    _validateRegex();
    _rerenderAll();
  };

  // 清空缓冲
  _rootEl.querySelector('#dc-clear').onclick = () => {
    clearBuffer();  // logger 会回调本模块的 _loggerCb 触发重渲染
    showToast('日志已清空', 'success');
  };

  // 复制当前过滤结果
  _rootEl.querySelector('#dc-copy').onclick = () => {
    const lines = _filteredEntries().map(_formatEntryPlain);
    const text = lines.join('\n');
    _copyToClipboard(text).then((ok) => {
      showToast(ok ? ('已复制 ' + lines.length + ' 条') : '复制失败', ok ? 'success' : 'error');
    });
  };

  // 导出 txt
  _rootEl.querySelector('#dc-export').onclick = () => {
    const lines = _filteredEntries().map(_formatEntryPlain);
    const text = lines.join('\n') + '\n';
    const ts = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const fname = 'pty-agent-logs-' + ts.getFullYear() + pad(ts.getMonth() + 1) + pad(ts.getDate()) +
                  '-' + pad(ts.getHours()) + pad(ts.getMinutes()) + pad(ts.getSeconds()) + '.txt';
    try {
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      showToast('已导出 ' + fname, 'success');
    } catch (e) {
      warn('devconsole', 'export failed: %s', e);
      showToast('导出失败: ' + e, 'error');
    }
  };
}

function _syncPauseIcon() {
  if (!_pauseBtnEl) return;
  _pauseBtnEl.innerHTML = _paused ? _ICON_PLAY : _ICON_PAUSE;
}

function _syncAutoScrollIcon() {
  if (!_autoScrollBtnEl) return;
  _autoScrollBtnEl.innerHTML = _autoScroll ? _ICON_AUTOSCROLL_ON : _ICON_AUTOSCROLL_OFF;
  _autoScrollBtnEl.style.color = _autoScroll ? 'var(--wt-accent)' : 'var(--wt-tab-text-muted)';
}

// ──────────────────────────────────────────────
// 日志接收与渲染
// ──────────────────────────────────────────────

/**
 * 暂停时不接收新日志，直接丢弃；恢复后仅显示后续日志。
 */
function _onNewEntry(entry) {
  // 记录 tag（即使被过滤也要登记，以便生成 chip）
  const tagChanged = !_seenTags.has(entry.tag);
  _seenTags.add(entry.tag);
  if (tagChanged) _rebuildTagChips();

  if (_paused) {
    return;
  }
  _appendEntry(entry);
  _updateStatus();
  if (_autoScroll) _scrollToBottom();
}

/**
 * 追加单条日志到列表（通过当前过滤判断是否显示）。
 * 仅追加 DOM，不重排已有内容（高频场景友好）。
 */
function _appendEntry(entry) {
  if (!_bodyEl) return;
  if (!_passesFilter(entry)) return;
  _bodyEl.appendChild(_buildLineEl(entry));
  // 控制列表长度上限，避免 DOM 无限增长（与缓冲容量对齐）
  const cap = getBufferCapacity();
  while (_bodyEl.childElementCount > cap) {
    _bodyEl.removeChild(_bodyEl.firstChild);
  }
}

/**
 * 全量重渲染（过滤条件变化时调用）。
 * 重建列表，包含高亮搜索命中。
 */
function _rerenderAll() {
  if (!_bodyEl) return;
  const frag = document.createDocumentFragment();
  const entries = _filteredEntries();
  for (const e of entries) {
    frag.appendChild(_buildLineEl(e));
  }
  _bodyEl.innerHTML = '';
  _bodyEl.appendChild(frag);
  _updateStatus();
  if (_autoScroll) _scrollToBottom();
}

function _filteredEntries() {
  return getEntries().filter(_passesFilter);
}

function _passesFilter(entry) {
  if (!_levelFilter.has(entry.level)) return false;
  if (!_isTagOn(entry.tag)) return false;
  if (_searchText) {
    if (_useRegex && _regexValid) {
      try {
        const re = new RegExp(_searchText);
        if (!re.test(entry.text)) return false;
      } catch (_) {
        // 正则无效时按子串
        if (entry.text.indexOf(_searchText) === -1) return false;
      }
    } else {
      if (entry.text.indexOf(_searchText) === -1) return false;
    }
  }
  return true;
}

function _buildLineEl(entry) {
  const isExpandable = entry.hasStack || entry.level >= 2; // ERROR(3) 或 WARN(2) 可展开
  const line = document.createElement('div');
  line.className = 'dc-log-line' + (isExpandable ? ' has-stack' : '');
  line.dataset.level = String(entry.level);

  // 展开图标
  const expandIcon = document.createElement('span');
  expandIcon.className = 'dc-expand-icon';
  expandIcon.textContent = isExpandable ? '▶' : '';
  line.appendChild(expandIcon);

  const ts = document.createElement('span');
  ts.className = 'dc-ts';
  ts.textContent = entry.tsStr;
  const lvl = document.createElement('span');
  lvl.className = 'dc-lvl';
  lvl.textContent = entry.levelName;
  const tag = document.createElement('span');
  tag.className = 'dc-tag';
  tag.textContent = '[' + entry.tag + ']';
  tag.style.color = _tagColor(entry.tag);
  const text = document.createElement('span');
  text.className = 'dc-text';
  text.appendChild(_buildHighlighted(entry.text));
  line.appendChild(ts);
  line.appendChild(lvl);
  line.appendChild(tag);
  line.appendChild(text);

  // 展开详情（完整 stack 或全文）
  if (isExpandable) {
    const detail = document.createElement('div');
    detail.className = 'dc-line-detail';
    detail.textContent = entry.stack || entry.text;
    line.appendChild(detail);
    // 点击行切换展开/收起
    line.onclick = (e) => {
      // 不拦截图标本身点击（图标也在行内，但 click 事件在行上）
      if (e.target.closest('.dc-line-detail')) return;
      const expanded = line.classList.toggle('expanded');
      expandIcon.textContent = expanded ? '▼' : '▶';
    };
  }

  return line;
}

/**
 * 搜索命中高亮：对 text 中匹配 _searchText 的片段包 <mark>。
 * 无搜索或正则无效时返回纯文本节点。
 */
function _buildHighlighted(text) {
  const frag = document.createDocumentFragment();
  if (!_searchText) {
    frag.appendChild(document.createTextNode(text));
    return frag;
  }
  let re = null;
  if (_useRegex && _regexValid) {
    try { re = new RegExp(_searchText, 'g'); } catch (_) { re = null; }
  }
  if (!re) {
    // 子串高亮（转义正则元字符）
    const safe = _searchText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    try { re = new RegExp(safe, 'g'); } catch (_) {
      frag.appendChild(document.createTextNode(text));
      return frag;
    }
  }
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
    const mark = document.createElement('mark');
    mark.textContent = m[0];
    frag.appendChild(mark);
    last = m.index + m[0].length;
    if (m[0].length === 0) re.lastIndex++;  // 避免零宽匹配死循环
  }
  if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
  return frag;
}

function _validateRegex() {
  if (!_useRegex || !_searchText) {
    _regexValid = true;
    _searchWrapEl.classList.remove('dc-search-invalid');
    return;
  }
  try {
    // eslint-disable-next-line no-new
    new RegExp(_searchText);
    _regexValid = true;
    _searchWrapEl.classList.remove('dc-search-invalid');
  } catch (_) {
    _regexValid = false;
    _searchWrapEl.classList.add('dc-search-invalid');
  }
}

function _formatEntryPlain(entry) {
  return entry.tsStr + ' [' + entry.levelName + '] [' + entry.tag + '] ' + entry.text;
}

function _updateStatus() {
  if (!_statusEl) return;
  const shown = _bodyEl ? _bodyEl.childElementCount : 0;
  const total = getBufferSize();
  _statusEl.textContent = shown + ' / ' + total + (_paused ? ' · 暂停' : '');
  _updateLeftPanel();
}

/**
 * 更新左栏统计信息：各等级计数、缓冲使用率。
 * 从缓冲区全量扫描（开销可控，上限 5000 条）。
 */
function _updateLeftPanel() {
  const el = _rootEl && _rootEl.querySelector('#dc-left');
  if (!el) return;

  // 等级统计
  const all = getEntries();
  const counts = [0, 0, 0, 0];
  let shown = 0;
  for (const e of all) {
    counts[e.level] = (counts[e.level] || 0) + 1;
    if (_passesFilter(e)) shown++;
  }
  const levelsEl = el.querySelector('#dc-left-levels');
  if (levelsEl) {
    const names = ['DEBUG', 'INFO', 'WARN', 'ERROR'];
    const colors = ['#888', '#08f', '#f80', '#f00'];
    levelsEl.innerHTML = names.map((n, i) =>
      '<div class="dc-left-level-row">' +
        '<span class="dc-left-dot" style="color:' + colors[i] + '">●</span>' +
        '<span class="dc-left-lvl-name">' + n + '</span>' +
        '<span class="dc-left-lvl-count">' + (counts[i] || 0) + '</span>' +
      '</div>'
    ).join('');
  }

  // 缓冲使用率
  const cap = getBufferCapacity();
  const used = all.length;
  const pct = cap > 0 ? Math.min(100, Math.round(used / cap * 100)) : 0;
  const bufEl = el.querySelector('#dc-left-buffer');
  if (bufEl) {
    bufEl.textContent = '缓冲 ' + used + ' / ' + cap + '（' + pct + '%）';
  }
  const barFill = el.querySelector('#dc-left-bar-fill');
  if (barFill) {
    barFill.style.width = pct + '%';
  }
}

function _scrollToBottom() {
  if (!_bodyEl) return;
  _bodyEl.scrollTop = _bodyEl.scrollHeight;
}

// ── 筛选状态持久化 ──
function _saveLevelFilter() {
  try { localStorage.setItem(LEVEL_FILTER_KEY, JSON.stringify(Array.from(_levelFilter))); } catch (_) {}
}

function _saveTagFilter() {
  try { localStorage.setItem(TAG_FILTER_KEY, JSON.stringify(Array.from(_tagFilter))); } catch (_) {}
}

// ── 视窗大小 / 透明度 ──
const WINDOW_SIZES = {
  small:  { w: 480, h: 320, zoom: 0.85 },
  medium: { w: 560, h: 360, zoom: 1.0 },
  large:  { w: 720, h: 480, zoom: 1.15 },
  xlarge: { w: 960, h: 600, zoom: 1.3 },
};

function _applyWindowSize() {
  if (!_rootEl) return;
  const size = settingsStore.get('developer.windowSize') || 'medium';
  const dim = WINDOW_SIZES[size] || WINDOW_SIZES.medium;
  _rootEl.style.width = dim.w + 'px';
  _rootEl.style.height = dim.h + 'px';
  _rootEl.style.zoom = dim.zoom;
  _saveGeometry();
}

function _applyWindowOpacity() {
  if (!_rootEl) return;
  const opacity = settingsStore.get('developer.windowOpacity');
  if (opacity != null) {
    _rootEl.style.opacity = (opacity / 100).toFixed(2);
  }
}

// 在 mount 中订阅设置变更，实时更新视窗大小和透明度
let _settingsCb = null;
let _unsubSettings = null;
function _bindDevSettings() {
  if (_settingsCb) return;
  _settingsCb = (key, value) => {
    if (key === 'developer.windowSize') {
      _applyWindowSize();
    } else if (key === 'developer.windowOpacity') {
      _applyWindowOpacity();
    }
  };
  _unsubSettings = settingsStore.subscribe(_settingsCb);
}

function _unbindDevSettings() {
  if (_unsubSettings) {
    _unsubSettings();
    _unsubSettings = null;
    _settingsCb = null;
  }
}

// ──────────────────────────────────────────────
// 工具函数
// ──────────────────────────────────────────────

function _clamp(v, min, max) {
  if (max < min) max = min;
  if (v < min) return min;
  if (v > max) return max;
  return v;
}

function _copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).then(() => true).catch(() => _fallbackCopy(text));
  }
  return Promise.resolve(_fallbackCopy(text));
}

function _fallbackCopy(text) {
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch (_) {
    return false;
  }
}

// ── 内联 SVG 图标（避免引入额外资源） ──
const _ICON_CLOSE = '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3 3l10 10M13 3L3 13"/></svg>';
const _ICON_PAUSE = '<svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor"><rect x="4" y="3" width="3" height="10" rx="0.5"/><rect x="9" y="3" width="3" height="10" rx="0.5"/></svg>';
const _ICON_PLAY = '<svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor"><path d="M4 3l9 5-9 5z"/></svg>';
const _ICON_AUTOSCROLL_ON = '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v10M4 9l4 4 4-4"/></svg>';
const _ICON_AUTOSCROLL_OFF = '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 13V3M4 7l4-4 4 4"/></svg>';
const _ICON_CLEAR = '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M3 5h10M6 5V3.5a1 1 0 011-1h2a1 1 0 011 1V5M5 5l1 8a1 1 0 001 1h2a1 1 0 001-1l1-8"/></svg>';
const _ICON_COPY = '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5" y="5" width="8" height="8" rx="1"/><path d="M3 11V3a1 1 0 011-1h7" /></svg>';
const _ICON_EXPORT = '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2v8M5 5l3-3 3 3M3 11v2a1 1 0 001 1h8a1 1 0 001-1v-2"/></svg>';

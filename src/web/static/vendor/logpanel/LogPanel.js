/**
 * LogPanel — 通用悬浮日志视窗（Shadow DOM 隔离）。
 *
 * 设计原则：
 *   - 视窗不假设日志来源，通过 source 接口注入（subscribe/getEntries/clear/getSize/getCapacity）。
 *   - entry 结构自由，通过 rules 访问器提取元数据；hooks 提供管线变换能力。
 *   - 全部 UI 封装在 Shadow DOM 内，不污染宿主 DOM/CSS。
 *   - 持久化自带 localStorage，key 前缀可配。
 *   - 不依赖任何外部模块，零依赖。
 *
 * API：
 *   const panel = new LogPanel(opts);
 *   panel.show(); panel.hide(); panel.destroy();
 *   panel.setOption('theme', 'dark');
 *   panel.on('visibleChange', cb);
 *
 * opts 见构造函数注释。
 */

import { STYLES } from './styles.js';
import { DEFAULT_RULES, resolveTagColor } from './defaultRules.js';
import * as Icons from './icons.js';

const LEVEL_DEFS = [
  { level: 0, name: 'DEBUG' },
  { level: 1, name: 'INFO' },
  { level: 2, name: 'WARN' },
  { level: 3, name: 'ERROR' },
];

const WINDOW_SIZES = {
  small:  { w: 480, h: 320, zoom: 0.85 },
  medium: { w: 560, h: 360, zoom: 1.0 },
  large:  { w: 720, h: 480, zoom: 1.15 },
  xlarge: { w: 960, h: 600, zoom: 1.3 },
};

/**
 * @param {object} opts
 * @param {object} opts.source              日志源（必需）{ subscribe, getEntries, clear, getSize, getCapacity }
 * @param {object} [opts.rules]             解析规则访问器（覆盖默认）
 * @param {object} [opts.hooks]             钩子 { onEntry, formatText }
 * @param {string} [opts.storageKeyPrefix]  localStorage key 前缀，默认 'logpanel_'
 * @param {string} [opts.theme]             'auto' | 'light' | 'dark'，默认 'auto'
 * @param {HTMLElement} [opts.mountEl]      挂载父节点，默认 document.body
 * @param {object} [opts.initialGeometry]   初始几何 { x, y, w, h, zoom }
 * @param {boolean} [opts.initialVisible]   初始可见，默认 true
 * @param {string} [opts.windowSize]        预设尺寸 'small'|'medium'|'large'|'xlarge'
 * @param {number} [opts.windowOpacity]     透明度 0-100
 * @param {object} [opts.features]          功能开关，默认全 true
 * @param {string} [opts.title]             标题栏文字，默认 '日志视窗'
 * @param {function} [opts.t]               本地化函数 t(key, params)；未提供时回退内置中文
 */
export class LogPanel {
  constructor(opts) {
    this._opts = opts || {};
    this._source = this._opts.source;
    if (!this._source || typeof this._source.subscribe !== 'function') {
      throw new Error('LogPanel: opts.source 必须提供 subscribe 方法');
    }
    // 本地化：外部 t 优先，缺失回退内置中文（保持组件自包含）
    this._FALLBACK = {
      'logpanel.title': '日志视窗',
      'logpanel.pauseToggle': '暂停/继续接收新日志',
      'logpanel.autoScrollBottom': '自动滚到底部',
      'logpanel.close': '关闭',
      'logpanel.searchPlaceholder': '搜索日志（支持正则）…',
      'logpanel.toggleRegex': '切换正则模式',
      'logpanel.clearLog': '清空日志缓冲区',
      'logpanel.clear': '清空',
      'logpanel.copyFilter': '复制当前过滤结果到剪贴板',
      'logpanel.copy': '复制',
      'logpanel.exportTxt': '导出为 txt 文件',
      'logpanel.export': '导出',
      'logpanel.moduleFilter': '模块筛选',
      'logpanel.selectAll': '全选',
      'logpanel.selectNone': '取消全选',
      'logpanel.stats': '统计',
      'logpanel.resumeReceiving': '继续接收新日志',
      'logpanel.pauseReceiving': '暂停接收新日志',
      'logpanel.disableAutoScroll': '关闭自动滚动',
      'logpanel.enableAutoScroll': '开启自动滚动',
      'logpanel.logCleared': '日志已清空',
      'logpanel.copied': '已复制 {n} 条',
      'logpanel.copyFailed': '复制失败',
      'logpanel.exported': '已导出 {fname}',
      'logpanel.exportFailed': '导出失败: {err}',
      'logpanel.closePanel': '关闭日志视窗',
      'logpanel.pausedSuffix': ' · 暂停',
      'logpanel.bufferLabel': '缓冲 {used} / {cap} 条 {pct}%',
    };
    this._t = this._opts.t || ((key, params) => {
      let s = this._FALLBACK[key] !== undefined ? this._FALLBACK[key] : key;
      if (params) {
        for (const k of Object.keys(params)) s = s.split('{' + k + '}').join(String(params[k]));
      }
      return s;
    });

    // 解析规则：默认 + 覆盖
    this._rules = Object.assign({}, DEFAULT_RULES, this._opts.rules || {});
    if (this._opts.rules && this._opts.rules.tagColor) {
      this._rules.tagColor = Object.assign({}, DEFAULT_RULES.tagColor, this._opts.rules.tagColor);
    }

    // 钩子
    this._hooks = this._opts.hooks || {};

    // 持久化
    this._prefix = this._opts.storageKeyPrefix || 'logpanel_';

    // 功能开关
    this._features = Object.assign({
      drag: true, resize: true, export: true, copy: true, clear: true,
      search: true, regex: true, pause: true, autoScroll: true,
      tagFilter: true, levelFilter: true, stats: true, contextMenu: true,
      windowSize: true, opacity: true,
    }, this._opts.features || {});

    // 事件监听者
    this._listeners = {};

    // 过滤状态（从 localStorage 恢复）
    this._levelFilter = this._loadSet('level_filter', new Set([0, 1, 2, 3]));
    this._tagFilter = this._loadSet('tag_filter', new Set()); // 空集合=全开
    this._searchText = '';
    this._useRegex = false;
    this._regexValid = true;

    // 运行状态
    this._paused = false;
    this._autoScroll = this._loadBool('autoscroll', true);
    this._autoScrollUserEnabled = false;
    this._seenTags = new Set();
    this._visible = false;
    this._mounted = false;

    // 主题
    this._theme = this._opts.theme || 'auto';

    // 构建 Shadow DOM
    this._buildShadow();

    // 挂载到父节点
    const mountEl = this._opts.mountEl || document.body;
    mountEl.appendChild(this._host);

    // 应用初始几何
    this._loadGeometry();
    this._applyTheme();
    this._applyTouchFlag();

    if (this._opts.windowSize) this._applyWindowSize(this._opts.windowSize);
    if (this._opts.windowOpacity != null) this._applyOpacity(this._opts.windowOpacity);

    // 初始可见性
    const visible = this._opts.initialVisible !== false;
    if (visible) this.show();
  }

  // ──────────────────────────────────────────────
  // 公共 API
  // ──────────────────────────────────────────────

  show() {
    if (this._visible) return;
    this._visible = true;
    this._host.removeAttribute('data-hidden');
    if (!this._mounted) this._mount();
    this._emit('visibleChange', true);
  }

  hide() {
    if (!this._visible) return;
    this._visible = false;
    this._host.setAttribute('data-hidden', 'true');
    this._emit('visibleChange', false);
  }

  destroy() {
    this._unmount();
    if (this._host.parentNode) this._host.parentNode.removeChild(this._host);
    this._emit('destroy');
    this._listeners = {};
  }

  isVisible() { return this._visible; }

  setOption(key, value) {
    switch (key) {
      case 'theme': this._theme = value; this._applyTheme(); break;
      case 'windowSize': this._applyWindowSize(value); break;
      case 'windowOpacity': this._applyOpacity(value); break;
      case 'rules':
        this._rules = Object.assign({}, DEFAULT_RULES, value || {});
        if (value && value.tagColor) {
          this._rules.tagColor = Object.assign({}, DEFAULT_RULES.tagColor, value.tagColor);
        }
        this._rerenderAll();
        break;
      case 'hooks': this._hooks = value || {}; break;
    }
  }

  on(event, cb) {
    if (!this._listeners[event]) this._listeners[event] = new Set();
    this._listeners[event].add(cb);
    return () => this._listeners[event] && this._listeners[event].delete(cb);
  }

  _emit(event, ...args) {
    const set = this._listeners[event];
    if (!set) return;
    for (const cb of set) {
      try { cb(...args); } catch (_) {}
    }
  }

  // ──────────────────────────────────────────────
  // Shadow DOM 构建
  // ──────────────────────────────────────────────

  _buildShadow() {
    const host = document.createElement('div');
    host.setAttribute('data-hidden', 'true');
    const shadow = host.attachShadow({ mode: 'open' });

    const styleEl = document.createElement('style');
    styleEl.textContent = STYLES;
    shadow.appendChild(styleEl);

    const root = document.createElement('div');
    root.className = 'lp-root';
    root.innerHTML = this._buildInnerHtml();
    shadow.appendChild(root);

    // toast 容器
    const toastContainer = document.createElement('div');
    toastContainer.className = 'lp-toast-container';
    root.appendChild(toastContainer);

    this._host = host;
    this._shadow = shadow;
    this._root = root;
    this._toastContainer = toastContainer;

    // 缓存元素引用
    this._el = {
      status:       shadow.querySelector('#lp-status'),
      pauseBtn:     shadow.querySelector('#lp-pause'),
      autoScrollBtn:shadow.querySelector('#lp-autoscroll'),
      closeBtn:     shadow.querySelector('#lp-close'),
      levelGroup:   shadow.querySelector('#lp-level-group'),
      searchInput:  shadow.querySelector('#lp-search-input'),
      searchWrap:   shadow.querySelector('#lp-search'),
      regexBtn:     shadow.querySelector('#lp-regex-btn'),
      clearBtn:     shadow.querySelector('#lp-clear'),
      copyBtn:      shadow.querySelector('#lp-copy'),
      exportBtn:    shadow.querySelector('#lp-export'),
      body:         shadow.querySelector('#lp-right'),
      left:         shadow.querySelector('#lp-left'),
      tagGroup:     shadow.querySelector('#lp-left-tags'),
      tagToggle:    shadow.querySelector('#lp-left-tag-toggle'),
      levels:       shadow.querySelector('#lp-left-levels'),
      buffer:       shadow.querySelector('#lp-left-buffer'),
      barFill:      shadow.querySelector('#lp-left-bar-fill'),
      resizeHandle: shadow.querySelector('#lp-resize'),
      titlebar:     shadow.querySelector('.lp-titlebar'),
      titleText:    shadow.querySelector('.lp-title span'),
    };

    // 标题文字
    if (this._opts.title) this._el.titleText.textContent = this._opts.title;

    this._buildLevelChips();
    this._syncAutoScrollIcon();
    this._syncPauseIcon();
  }

  _buildInnerHtml() {
    const f = this._features;
    let html = '<div class="lp-titlebar">' +
      '<div class="lp-title">' + Icons.ICON_TITLE + '<span>' + this._t('logpanel.title') + '</span></div>' +
      '<span class="lp-status" id="lp-status">0 / 0</span>' +
      '<div class="lp-spacer"></div>';
    if (f.pause)      html += '<button class="lp-iconbtn lp-pause" id="lp-pause" title="' + this._t('logpanel.pauseToggle') + '">' + Icons.ICON_PAUSE + '</button>';
    if (f.autoScroll) html += '<button class="lp-iconbtn lp-autoscroll" id="lp-autoscroll" title="' + this._t('logpanel.autoScrollBottom') + '">' + Icons.ICON_AUTOSCROLL_ON + '</button>';
    html += '<button class="lp-iconbtn lp-close" id="lp-close" title="' + this._t('logpanel.close') + '">' + Icons.ICON_CLOSE + '</button>';
    html += '</div>';

    // 工具栏
    html += '<div class="lp-toolbar">';
    if (f.levelFilter) html += '<div class="lp-level-group" id="lp-level-group"></div><div class="lp-divider"></div>';
    if (f.search) {
      html += '<div class="lp-search" id="lp-search">' +
        '<input type="text" id="lp-search-input" placeholder="' + this._t('logpanel.searchPlaceholder') + '" autocomplete="off" spellcheck="false">';
      if (f.regex) html += '<button class="lp-regex-btn" id="lp-regex-btn" title="' + this._t('logpanel.toggleRegex') + '">.*</button>';
      html += '</div>';
    }
    html += '<div class="lp-actions">';
    if (f.clear)  html += '<button class="lp-btn" id="lp-clear" title="' + this._t('logpanel.clearLog') + '">' + Icons.ICON_CLEAR + this._t('logpanel.clear') + '</button>';
    if (f.copy)   html += '<button class="lp-btn" id="lp-copy" title="' + this._t('logpanel.copyFilter') + '">' + Icons.ICON_COPY + this._t('logpanel.copy') + '</button>';
    if (f.export) html += '<button class="lp-btn" id="lp-export" title="' + this._t('logpanel.exportTxt') + '">' + Icons.ICON_EXPORT + this._t('logpanel.export') + '</button>';
    html += '</div></div>';

    // 主体
    html += '<div class="lp-body">';
    html += '<div class="lp-left" id="lp-left">';
    if (f.tagFilter) {
      html += '<div class="lp-left-section">' +
        '<div class="lp-left-label">' + this._t('logpanel.moduleFilter') + '</div>' +
        '<div class="lp-left-tag-toggle" id="lp-left-tag-toggle">' + this._t('logpanel.selectAll') + '</div>' +
        '<div class="lp-left-tags" id="lp-left-tags"></div>' +
      '</div>';
    }
    html += '<div class="lp-left-spacer"></div>';
    if (f.stats) {
      html += '<div class="lp-left-section">' +
        '<div class="lp-left-label">' + this._t('logpanel.stats') + '</div>' +
        '<div class="lp-left-levels" id="lp-left-levels"></div>' +
        '<div class="lp-left-buffer" id="lp-left-buffer"></div>' +
        '<div class="lp-left-bar"><div class="lp-left-bar-fill" id="lp-left-bar-fill"></div></div>' +
      '</div>';
    }
    html += '</div>'; // lp-left
    html += '<div class="lp-right" id="lp-right"></div>';
    html += '</div>'; // lp-body

    if (f.resize) html += '<div class="lp-resize" id="lp-resize"></div>';
    return html;
  }

  // ──────────────────────────────────────────────
  // 挂载/卸载（订阅 source）
  // ──────────────────────────────────────────────

  _mount() {
    this._bindDrag();
    this._bindResize();
    this._bindToolbar();
    this._bindViewportClamp();
    this._bindAutoScrollSmart();

    // 订阅 source
    this._sourceCb = (entry) => {
      if (entry && entry.type === 'clear') {
        this._seenTags.clear();
        this._rebuildTagChips();
        this._rerenderAll();
        return;
      }
      this._onNewEntry(entry);
    };
    this._sourceUnsub = this._source.subscribe(this._sourceCb);

    // 回放历史
    const all = this._safeGetEntries();
    for (const e of all) this._seenTags.add(this._rules.tag(e));
    this._rebuildTagChips();
    this._rerenderAll();
    this._scrollToBottom();

    this._mounted = true;
  }

  _unmount() {
    if (this._sourceUnsub) { this._sourceUnsub(); this._sourceUnsub = null; }
    this._sourceCb = null;
    this._unbindViewportClamp();
    this._unbindContextMenu();
    this._mounted = false;
  }

  _safeGetEntries() {
    try { return this._source.getEntries() || []; } catch (_) { return []; }
  }

  // ──────────────────────────────────────────────
  // 等级 chip 构建
  // ──────────────────────────────────────────────

  _buildLevelChips() {
    const group = this._el.levelGroup;
    if (!group) return;
    group.innerHTML = '';
    for (const def of LEVEL_DEFS) {
      const chip = document.createElement('div');
      chip.className = 'lp-chip' + (this._levelFilter.has(def.level) ? ' active' : '');
      chip.dataset.level = String(def.level);
      chip.textContent = def.name;
      chip.onclick = () => {
        if (this._levelFilter.has(def.level)) this._levelFilter.delete(def.level);
        else this._levelFilter.add(def.level);
        chip.classList.toggle('active', this._levelFilter.has(def.level));
        this._saveSet('level_filter', this._levelFilter);
        this._rerenderAll();
      };
      group.appendChild(chip);
    }
  }

  // ──────────────────────────────────────────────
  // tag 筛选
  // ──────────────────────────────────────────────

  _rebuildTagChips() {
    const group = this._el.tagGroup;
    if (!group) return;
    const tags = Array.from(this._seenTags).sort();
    group.innerHTML = '';
    for (const tag of tags) {
      const on = this._isTagOn(tag);
      const chip = document.createElement('div');
      chip.className = 'lp-left-tag-row' + (on ? ' active' : '');
      chip.dataset.tag = tag;
      const color = resolveTagColor(tag, this._rules.tagColor);
      chip.innerHTML = '<span class="lp-left-tag-dot" style="color:' + color + '">●</span>' +
        '<span class="lp-left-tag-name">' + tag + '</span>';
      chip.onclick = () => {
        if (this._tagFilter.has(tag)) this._tagFilter.delete(tag);
        else this._tagFilter.add(tag);
        this._saveSet('tag_filter', this._tagFilter);
        this._rebuildTagChips();
        this._rerenderAll();
      };
      group.appendChild(chip);
    }
    const toggle = this._el.tagToggle;
    if (toggle) {
      const allHidden = tags.length > 0 && tags.every(t => this._tagFilter.has(t));
      toggle.textContent = allHidden ? this._t('logpanel.selectAll') : this._t('logpanel.selectNone');
      toggle.style.display = tags.length > 1 ? '' : 'none';
    }
  }

  _isTagOn(tag) {
    if (this._tagFilter.size === 0) return true;
    return !this._tagFilter.has(tag);
  }

  // ──────────────────────────────────────────────
  // 拖拽
  // ──────────────────────────────────────────────

  _bindDrag() {
    if (!this._features.drag) return;
    const titlebar = this._el.titlebar;
    let startX = 0, startY = 0, startLeft = 0, startTop = 0;

    const zoom = () => parseFloat(this._host.style.zoom) || 1;

    const onMove = (cx, cy) => {
      const vw = window.innerWidth, vh = window.innerHeight;
      const r = this._host.getBoundingClientRect();
      const z = zoom();
      let nx = startLeft + (cx - startX) / z;
      let ny = startTop + (cy - startY) / z;
      nx = this._clamp(nx, 0, (vw - r.width) / z);
      ny = this._clamp(ny, 0, (vh - r.height) / z);
      this._host.style.left = nx + 'px';
      this._host.style.top = ny + 'px';
    };
    const onUp = () => {
      this._root.classList.remove('dragging');
      document.removeEventListener('mousemove', mm);
      document.removeEventListener('mouseup', mu);
      document.removeEventListener('touchmove', tm);
      document.removeEventListener('touchend', te);
      this._saveGeometry();
    };
    const mm = (e) => onMove(e.clientX, e.clientY);
    const mu = onUp;
    const tm = (e) => { if (e.touches.length === 1) { e.preventDefault(); onMove(e.touches[0].clientX, e.touches[0].clientY); } };
    const te = onUp;

    titlebar.addEventListener('mousedown', (e) => {
      if (e.target.closest('.lp-iconbtn')) return;
      e.preventDefault();
      this._root.classList.add('dragging');
      startX = e.clientX; startY = e.clientY;
      startLeft = parseInt(this._host.style.left) || 0;
      startTop = parseInt(this._host.style.top) || 0;
      document.addEventListener('mousemove', mm);
      document.addEventListener('mouseup', mu);
    });

    // 右键菜单
    if (this._features.contextMenu) {
      titlebar.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this._showContextMenu(e.clientX, e.clientY);
      });
    }

    titlebar.addEventListener('touchstart', (e) => {
      if (e.target.closest('.lp-iconbtn')) return;
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      this._root.classList.add('dragging');
      startX = t.clientX; startY = t.clientY;
      startLeft = parseInt(this._host.style.left) || 0;
      startTop = parseInt(this._host.style.top) || 0;
      document.addEventListener('touchmove', tm, { passive: false });
      document.addEventListener('touchend', te);
    });
  }

  // ──────────────────────────────────────────────
  // 缩放
  // ──────────────────────────────────────────────

  _bindResize() {
    if (!this._features.resize) return;
    const handle = this._el.resizeHandle;
    if (!handle) return;
    let startX = 0, startY = 0, startW = 0, startH = 0;

    const zoom = () => parseFloat(this._host.style.zoom) || 1;

    const onMove = (cx, cy) => {
      const vw = window.innerWidth, vh = window.innerHeight;
      const r = this._host.getBoundingClientRect();
      const z = zoom();
      let nw = this._clamp(startW + (cx - startX), 320, vw - r.left - 4);
      let nh = this._clamp(startH + (cy - startY), 200, vh - r.top - 4);
      this._host.style.width = (nw / z) + 'px';
      this._host.style.height = (nh / z) + 'px';
    };
    const onUp = () => {
      this._root.classList.remove('resizing');
      document.removeEventListener('mousemove', mm);
      document.removeEventListener('mouseup', mu);
      document.removeEventListener('touchmove', tm);
      document.removeEventListener('touchend', te);
      this._saveGeometry();
      if (this._autoScroll) this._scrollToBottom();
    };
    const mm = (e) => onMove(e.clientX, e.clientY);
    const mu = onUp;
    const tm = (e) => { if (e.touches.length === 1) { e.preventDefault(); onMove(e.touches[0].clientX, e.touches[0].clientY); } };
    const te = onUp;

    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this._root.classList.add('resizing');
      startX = e.clientX; startY = e.clientY;
      const r = this._host.getBoundingClientRect();
      startW = r.width; startH = r.height;
      document.addEventListener('mousemove', mm);
      document.addEventListener('mouseup', mu);
    });
    handle.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      e.preventDefault();
      e.stopPropagation();
      const t = e.touches[0];
      this._root.classList.add('resizing');
      startX = t.clientX; startY = t.clientY;
      const r = this._host.getBoundingClientRect();
      startW = r.width; startH = r.height;
      document.addEventListener('touchmove', tm, { passive: false });
      document.addEventListener('touchend', te);
    });
  }

  // ──────────────────────────────────────────────
  // 智能自动滚动
  // ──────────────────────────────────────────────

  _bindAutoScrollSmart() {
    if (!this._features.autoScroll) return;
    const body = this._el.body;
    if (!body) return;
    let isUserScroll = false;
    body.addEventListener('scroll', () => {
      if (!this._autoScroll || this._autoScrollUserEnabled) return;
      const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 5;
      if (!atBottom && !isUserScroll) {
        isUserScroll = true;
        this._autoScroll = false;
        this._syncAutoScrollIcon();
        this._saveBool('autoscroll', false);
      }
      if (atBottom) isUserScroll = false;
    });
  }

  // ──────────────────────────────────────────────
  // 视窗夹紧
  // ──────────────────────────────────────────────

  _bindViewportClamp() {
    this._resizeHandler = () => {
      const vw = window.innerWidth, vh = window.innerHeight;
      const r = this._host.getBoundingClientRect();
      const z = parseFloat(this._host.style.zoom) || 1;
      let changed = false;
      let left = parseInt(this._host.style.left) || 0;
      let top = parseInt(this._host.style.top) || 0;
      const maxLeft = Math.max(0, (vw - r.width) / z);
      const maxTop = Math.max(0, (vh - r.height) / z);
      if (left > maxLeft) { left = maxLeft; changed = true; }
      if (top > maxTop) { top = maxTop; changed = true; }
      if (left < 0) { left = 0; changed = true; }
      if (top < 0) { top = 0; changed = true; }
      if (changed) {
        this._host.style.left = left + 'px';
        this._host.style.top = top + 'px';
        this._saveGeometry();
      }
    };
    window.addEventListener('resize', this._resizeHandler);
  }

  _unbindViewportClamp() {
    if (this._resizeHandler) {
      window.removeEventListener('resize', this._resizeHandler);
      this._resizeHandler = null;
    }
  }

  // ──────────────────────────────────────────────
  // 工具栏交互
  // ──────────────────────────────────────────────

  _bindToolbar() {
    const closeBtn = this._el.closeBtn;
    if (closeBtn) closeBtn.onclick = () => this.hide();

    const tagToggle = this._el.tagToggle;
    if (tagToggle) {
      tagToggle.onclick = () => {
        const tags = Array.from(this._seenTags);
        if (tags.length === 0) return;
        const allHidden = tags.every(t => this._tagFilter.has(t));
        this._tagFilter = allHidden ? new Set() : new Set(tags);
        this._saveSet('tag_filter', this._tagFilter);
        this._rebuildTagChips();
        this._rerenderAll();
      };
    }

    const pauseBtn = this._el.pauseBtn;
    if (pauseBtn) {
      pauseBtn.onclick = () => {
        this._paused = !this._paused;
        this._syncPauseIcon();
        pauseBtn.title = this._paused ? this._t('logpanel.resumeReceiving') : this._t('logpanel.pauseReceiving');
        this._updateStatus();
      };
    }

    const autoBtn = this._el.autoScrollBtn;
    if (autoBtn) {
      autoBtn.onclick = () => {
        this._autoScroll = !this._autoScroll;
        this._autoScrollUserEnabled = this._autoScroll;
        this._syncAutoScrollIcon();
        autoBtn.title = this._autoScroll ? this._t('logpanel.disableAutoScroll') : this._t('logpanel.enableAutoScroll');
        this._saveBool('autoscroll', this._autoScroll);
        if (this._autoScroll) this._scrollToBottom();
      };
    }

    const searchInput = this._el.searchInput;
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        this._searchText = searchInput.value;
        this._validateRegex();
        this._rerenderAll();
      });
    }

    const regexBtn = this._el.regexBtn;
    if (regexBtn) {
      regexBtn.onclick = () => {
        this._useRegex = !this._useRegex;
        regexBtn.classList.toggle('active', this._useRegex);
        this._validateRegex();
        this._rerenderAll();
      };
    }

    const clearBtn = this._el.clearBtn;
    if (clearBtn) {
      clearBtn.onclick = () => {
        try { this._source.clear && this._source.clear(); } catch (_) {}
        this._toast(this._t('logpanel.logCleared'), 'success');
      };
    }

    const copyBtn = this._el.copyBtn;
    if (copyBtn) {
      copyBtn.onclick = () => {
        const lines = this._filteredEntries().map((e) => this._formatEntryPlain(e));
        const text = lines.join('\n');
        this._copyToClipboard(text).then((ok) => {
          this._toast(ok ? this._t('logpanel.copied', { n: lines.length }) : this._t('logpanel.copyFailed'), ok ? 'success' : 'error');
        });
      };
    }

    const exportBtn = this._el.exportBtn;
    if (exportBtn) {
      exportBtn.onclick = () => {
        const lines = this._filteredEntries().map((e) => this._formatEntryPlain(e));
        const text = lines.join('\n') + '\n';
        const ts = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        const fname = 'logs-' + ts.getFullYear() + pad(ts.getMonth() + 1) + pad(ts.getDate()) +
                      '-' + pad(ts.getHours()) + pad(ts.getMinutes()) + pad(ts.getSeconds()) + '.txt';
        try {
          const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url; a.download = fname;
          document.body.appendChild(a); a.click();
          document.body.removeChild(a);
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          this._toast(this._t('logpanel.exported', { fname }), 'success');
        } catch (e) {
          this._toast(this._t('logpanel.exportFailed', { err: e }), 'error');
        }
      };
    }
  }

  _syncPauseIcon() {
    const btn = this._el.pauseBtn;
    if (!btn) return;
    btn.innerHTML = this._paused ? Icons.ICON_PLAY : Icons.ICON_PAUSE;
  }

  _syncAutoScrollIcon() {
    const btn = this._el.autoScrollBtn;
    if (!btn) return;
    btn.innerHTML = this._autoScroll ? Icons.ICON_AUTOSCROLL_ON : Icons.ICON_AUTOSCROLL_OFF;
    btn.style.color = this._autoScroll ? 'var(--lp-accent)' : 'var(--lp-muted)';
  }

  // ──────────────────────────────────────────────
  // 右键菜单（Shadow DOM 内自实现）
  // ──────────────────────────────────────────────

  _showContextMenu(x, y) {
    this._unbindContextMenu();
    const menu = document.createElement('div');
    menu.className = 'lp-context-menu';
    menu.innerHTML = '<div class="lp-context-menu-item danger" data-action="close">this._t('logpanel.closePanel') + '</div>';
    this._shadow.appendChild(menu);
    menu.style.left = Math.min(x, window.innerWidth - menu.offsetWidth - 4) + 'px';
    menu.style.top = Math.min(y, window.innerHeight - menu.offsetHeight - 4) + 'px';

    const closeHandler = (ev) => {
      const target = ev.composedPath && ev.composedPath()[0];
      if (!menu.contains(target)) {
        this._unbindContextMenu();
      }
    };
    const item = menu.querySelector('[data-action="close"]');
    if (item) item.onclick = () => { this._unbindContextMenu(); this.hide(); };

    this._contextMenu = menu;
    this._contextMenuHandler = closeHandler;
    setTimeout(() => document.addEventListener('click', closeHandler), 0);
  }

  _unbindContextMenu() {
    if (this._contextMenu) {
      if (this._contextMenu.parentNode) this._contextMenu.parentNode.removeChild(this._contextMenu);
      this._contextMenu = null;
    }
    if (this._contextMenuHandler) {
      document.removeEventListener('click', this._contextMenuHandler);
      this._contextMenuHandler = null;
    }
  }

  // ──────────────────────────────────────────────
  // 日志接收与渲染
  // ──────────────────────────────────────────────

  _onNewEntry(entry) {
    // onEntry 钩子：可变换或丢弃
    if (this._hooks.onEntry) {
      try { entry = this._hooks.onEntry(entry); } catch (_) {}
      if (!entry) return;
    }

    const tag = this._rules.tag(entry);
    const tagChanged = !this._seenTags.has(tag);
    this._seenTags.add(tag);
    if (tagChanged) this._rebuildTagChips();

    if (this._paused) return;
    this._appendEntry(entry);
    this._updateStatus();
    if (this._autoScroll) this._scrollToBottom();
  }

  _appendEntry(entry) {
    const body = this._el.body;
    if (!body) return;
    if (!this._passesFilter(entry)) return;
    body.appendChild(this._buildLineEl(entry));
    const cap = this._safeGetCapacity();
    while (body.childElementCount > cap) body.removeChild(body.firstChild);
  }

  _rerenderAll() {
    const body = this._el.body;
    if (!body) return;
    const frag = document.createDocumentFragment();
    const entries = this._filteredEntries();
    for (const e of entries) frag.appendChild(this._buildLineEl(e));
    body.innerHTML = '';
    body.appendChild(frag);
    this._updateStatus();
    if (this._autoScroll) this._scrollToBottom();
  }

  _filteredEntries() {
    return this._safeGetEntries().filter((e) => this._passesFilter(e));
  }

  _passesFilter(entry) {
    const level = this._rules.level(entry);
    if (!this._levelFilter.has(level)) return false;
    const tag = this._rules.tag(entry);
    if (!this._isTagOn(tag)) return false;
    if (this._searchText) {
      const text = this._getDisplayText(entry);
      if (this._useRegex && this._regexValid) {
        try {
          const re = new RegExp(this._searchText);
          if (!re.test(text)) return false;
        } catch (_) {
          if (text.indexOf(this._searchText) === -1) return false;
        }
      } else {
        if (text.indexOf(this._searchText) === -1) return false;
      }
    }
    return true;
  }

  _getDisplayText(entry) {
    let text = this._rules.text(entry);
    if (this._hooks.formatText) {
      try { text = this._hooks.formatText(text, entry); } catch (_) {}
    }
    return text;
  }

  _buildLineEl(entry) {
    const level = this._rules.level(entry);
    const levelName = this._rules.levelName(entry);
    const tag = this._rules.tag(entry);
    const text = this._getDisplayText(entry);
    const tsStr = this._rules.tsStr(entry);
    const stack = this._rules.stack(entry);
    const isExpandable = this._rules.isExpandable(entry);

    const line = document.createElement('div');
    line.className = 'lp-log-line' + (isExpandable ? ' has-stack' : '');
    line.dataset.level = String(level);

    const expandIcon = document.createElement('span');
    expandIcon.className = 'lp-expand-icon';
    expandIcon.textContent = isExpandable ? '▶' : '';
    line.appendChild(expandIcon);

    const ts = document.createElement('span');
    ts.className = 'lp-ts';
    ts.textContent = tsStr;
    line.appendChild(ts);

    const lvl = document.createElement('span');
    lvl.className = 'lp-lvl';
    lvl.textContent = levelName;
    line.appendChild(lvl);

    const tagEl = document.createElement('span');
    tagEl.className = 'lp-tag';
    tagEl.textContent = '[' + tag + ']';
    tagEl.style.color = resolveTagColor(tag, this._rules.tagColor);
    line.appendChild(tagEl);

    const textEl = document.createElement('span');
    textEl.className = 'lp-text';
    textEl.appendChild(this._buildHighlighted(text));
    line.appendChild(textEl);

    if (isExpandable) {
      const detail = document.createElement('div');
      detail.className = 'lp-line-detail';
      detail.textContent = stack || text;
      line.appendChild(detail);
      line.onclick = (e) => {
        if (e.target.closest('.lp-line-detail')) return;
        const expanded = line.classList.toggle('expanded');
        expandIcon.textContent = expanded ? '▼' : '▶';
      };
    }

    return line;
  }

  _buildHighlighted(text) {
    const frag = document.createDocumentFragment();
    if (!this._searchText) {
      frag.appendChild(document.createTextNode(text));
      return frag;
    }
    let re = null;
    if (this._useRegex && this._regexValid) {
      try { re = new RegExp(this._searchText, 'g'); } catch (_) { re = null; }
    }
    if (!re) {
      const safe = this._searchText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
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
      if (m[0].length === 0) re.lastIndex++;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    return frag;
  }

  _validateRegex() {
    const wrap = this._el.searchWrap;
    if (!this._useRegex || !this._searchText) {
      this._regexValid = true;
      if (wrap) wrap.classList.remove('lp-search-invalid');
      return;
    }
    try {
      new RegExp(this._searchText);
      this._regexValid = true;
      if (wrap) wrap.classList.remove('lp-search-invalid');
    } catch (_) {
      this._regexValid = false;
      if (wrap) wrap.classList.add('lp-search-invalid');
    }
  }

  _formatEntryPlain(entry) {
    const tsStr = this._rules.tsStr(entry);
    const levelName = this._rules.levelName(entry);
    const tag = this._rules.tag(entry);
    const text = this._getDisplayText(entry);
    return tsStr + ' [' + levelName + '] [' + tag + '] ' + text;
  }

  _updateStatus() {
    const el = this._el.status;
    if (!el) return;
    const shown = this._el.body ? this._el.body.childElementCount : 0;
    const total = this._safeGetSize();
    el.textContent = shown + ' / ' + total + (this._paused ? this._t('logpanel.pausedSuffix') : '');
    this._updateLeftPanel();
  }

  _updateLeftPanel() {
    if (!this._features.stats) return;
    const all = this._safeGetEntries();
    const counts = [0, 0, 0, 0];
    for (const e of all) {
      const lvl = this._rules.level(e);
      counts[lvl] = (counts[lvl] || 0) + 1;
    }
    const levelsEl = this._el.levels;
    if (levelsEl) {
      const names = ['DEBUG', 'INFO', 'WARN', 'ERROR'];
      const colors = ['#888', '#08f', '#f80', '#f00'];
      levelsEl.innerHTML = names.map((n, i) =>
        '<div class="lp-left-level-row">' +
          '<span class="lp-left-dot" style="color:' + colors[i] + '">●</span>' +
          '<span class="lp-left-lvl-name">' + n + '</span>' +
          '<span class="lp-left-lvl-count">' + (counts[i] || 0) + '</span>' +
        '</div>'
      ).join('');
    }
    const cap = this._safeGetCapacity();
    const used = all.length;
    const pct = cap > 0 ? Math.min(100, Math.round(used / cap * 100)) : 0;
    const bufEl = this._el.buffer;
    if (bufEl) bufEl.textContent = this._t('logpanel.bufferLabel', { used, cap, pct });
    const barFill = this._el.barFill;
    if (barFill) barFill.style.width = pct + '%';
  }

  _scrollToBottom() {
    const body = this._el.body;
    if (!body) return;
    body.scrollTop = body.scrollHeight;
  }

  _safeGetSize() { try { return this._source.getSize ? this._source.getSize() : 0; } catch (_) { return 0; } }
  _safeGetCapacity() { try { return this._source.getCapacity ? this._source.getCapacity() : 1000; } catch (_) { return 1000; } }

  // ──────────────────────────────────────────────
  // 几何持久化
  // ──────────────────────────────────────────────

  _loadGeometry() {
    let geom = this._opts.initialGeometry || null;
    if (!geom) {
      try {
        const raw = localStorage.getItem(this._prefix + 'geom');
        if (raw) geom = JSON.parse(raw);
      } catch (_) {}
    }
    if (!geom || typeof geom !== 'object') geom = {};
    const vw = window.innerWidth, vh = window.innerHeight;
    const w = this._clamp(geom.w || 560, 320, vw - 20);
    const h = this._clamp(geom.h || 360, 200, vh - 20);
    const x = this._clamp(geom.x != null ? geom.x : Math.max(20, vw - w - 20), 4, Math.max(4, vw - w - 4));
    const y = this._clamp(geom.y != null ? geom.y : Math.max(20, vh - h - 20), 4, Math.max(4, vh - h - 4));
    this._host.style.left = x + 'px';
    this._host.style.top = y + 'px';
    this._host.style.width = w + 'px';
    this._host.style.height = h + 'px';
    if (geom.zoom != null) this._host.style.zoom = geom.zoom;
  }

  _saveGeometry() {
    try {
      localStorage.setItem(this._prefix + 'geom', JSON.stringify({
        x: parseInt(this._host.style.left) || 0,
        y: parseInt(this._host.style.top) || 0,
        w: parseInt(this._host.style.width) || 560,
        h: parseInt(this._host.style.height) || 360,
        zoom: parseFloat(this._host.style.zoom) || 1,
      }));
    } catch (_) {}
  }

  _applyWindowSize(size) {
    const dim = WINDOW_SIZES[size] || WINDOW_SIZES.medium;
    this._host.style.width = dim.w + 'px';
    this._host.style.height = dim.h + 'px';
    this._host.style.zoom = dim.zoom;
    this._saveGeometry();
  }

  _applyOpacity(opacity) {
    if (opacity != null) this._host.style.opacity = (opacity / 100).toFixed(2);
  }

  // ──────────────────────────────────────────────
  // 主题
  // ──────────────────────────────────────────────

  _applyTheme() {
    let theme = this._theme;
    if (theme === 'auto') {
      // 优先跟随宿主 body[data-theme]，否则跟随系统偏好
      const bodyTheme = document.body && document.body.getAttribute('data-theme');
      if (bodyTheme === 'dark' || bodyTheme === 'light') {
        theme = bodyTheme;
      } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        theme = 'dark';
      } else {
        theme = 'light';
      }
    }
    if (theme === 'dark') this._host.setAttribute('data-theme', 'dark');
    else this._host.removeAttribute('data-theme');
  }

  _applyTouchFlag() {
    const isTouch = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0);
    if (isTouch) this._host.setAttribute('data-touch', 'true');
  }

  // ──────────────────────────────────────────────
  // toast（Shadow DOM 内自带）
  // ──────────────────────────────────────────────

  _toast(message, type) {
    const toast = document.createElement('div');
    toast.className = 'lp-toast ' + (type || 'info');
    toast.textContent = message;
    this._toastContainer.appendChild(toast);
    void toast.offsetWidth;
    toast.classList.add('show');
    setTimeout(() => {
      toast.classList.remove('show');
      setTimeout(() => toast.remove(), 250);
    }, 3000);
  }

  // ──────────────────────────────────────────────
  // 持久化辅助
  // ──────────────────────────────────────────────

  _loadSet(key, fallback) {
    try {
      const raw = localStorage.getItem(this._prefix + key);
      if (raw) {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr)) return new Set(arr);
      }
    } catch (_) {}
    return fallback;
  }

  _saveSet(key, set) {
    try { localStorage.setItem(this._prefix + key, JSON.stringify(Array.from(set))); } catch (_) {}
  }

  _loadBool(key, fallback) {
    try {
      const raw = localStorage.getItem(this._prefix + key);
      if (raw !== null) return raw === 'true';
    } catch (_) {}
    return fallback;
  }

  _saveBool(key, val) {
    try { localStorage.setItem(this._prefix + key, String(val)); } catch (_) {}
  }

  // ──────────────────────────────────────────────
  // 工具函数
  // ──────────────────────────────────────────────

  _clamp(v, min, max) {
    if (max < min) max = min;
    if (v < min) return min;
    if (v > max) return max;
    return v;
  }

  _copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(() => true).catch(() => this._fallbackCopy(text));
    }
    return Promise.resolve(this._fallbackCopy(text));
  }

  _fallbackCopy(text) {
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
}

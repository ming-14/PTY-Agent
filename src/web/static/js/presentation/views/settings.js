/**
 * 表现层：设置 tab 视图
 *
 * 负责设置 tab 的打开/关闭、左侧导航渲染、右侧表单渲染与交互。
 * 通过 sessionHandlers 机制注册为 'settings' 类型 handler，
 * 与 terminal/vnc/fastscreen 三个 handler 并列，由 ui.js 统一分发。
 *
 * 渲染策略：首次进入 tab 时渲染整体骨架（导航 + 内容容器），
 * 切换分类时只重渲染右侧内容；底部操作栏在骨架中固定。
 *
 * 依赖：领域层 settingsSchema/state、应用层 settingsStore、基础设施 domUtils。
 */

import { state, saveTabState } from '../../domain/state.js';
import { SETTINGS_TAB_ID } from '../../domain/constants.js';
import { $ } from '../../infrastructure/domUtils.js';
import { debug, info, warn } from '../../domain/logger.js';
import { updateAutoHide } from './autohide.js';
import { registerSessionHandler } from './sessionHandlers.js';
import {
  SETTINGS_CATEGORIES,
  SETTINGS_SCHEMA,
  SETTING_TYPES,
  groupByCategory,
} from '../../domain/settingsSchema.js';
import * as store from '../../application/settingsStore.js';

const LS_SERVER_ADDR_KEY = 'pty_server_address';

// 当前选中的分类 id
let _activeCategory = 'basic';

/**
 * 打开设置 tab（单例：若已存在则仅切换）。
 * 加入 tabOrder，切换显示，并触发首次渲染。
 */
export function openSettingsTab() {
  info('settings', 'openSettingsTab, current tab=%s', state.activeTab);
  if (!state.tabOrder.includes(SETTINGS_TAB_ID)) {
    state.tabOrder.push(SETTINGS_TAB_ID);
    saveTabState();
  }
  switchToSettingsFrame();
  state.activeTab = SETTINGS_TAB_ID;
  state.sessions[SETTINGS_TAB_ID].running = true;
  saveTabState();
  renderSettingsView();
  // 重新渲染 tab 栏：新增 settings tab 后必须调用，否则 tab 栏不显示
  // （VNC/FastScreen 通过 wsSend 请求状态的响应间接触发 renderTabs，settings 无此机制）
  import('./ui.js').then(ui => {
    try { ui.renderTabs(); } catch (e) { warn('settings', 'renderTabs failed: %s', e); }
  });
  updateAutoHide();
}

/**
 * 关闭设置 tab（从 tabOrder 移除，切到下一个 tab）。
 * 委托给 ui.js closeTab 通过 handler 分发统一处理。
 */
export function closeSettingsTab() {
  info('settings', 'closeSettingsTab');
  state.sessions[SETTINGS_TAB_ID].running = false;
  import('./ui.js').then(ui => {
    try { ui.closeTab(SETTINGS_TAB_ID); } catch (e) { warn('settings', 'closeTab failed: %s', e); }
  });
}

/**
 * 切换到设置 frame（隐藏 terminal/vnc/fastscreen frame，显示 settings-frame）。
 * 由 ui.js switchTab 在 sid === SETTINGS_TAB_ID 时通过 handler.switchTo 调用。
 */
export function switchToSettingsFrame() {
  $('empty-state').style.display = 'none';
  $('terminal-frame').style.display = 'none';
  const vncFrame = $('vnc-frame');
  if (vncFrame) vncFrame.style.display = 'none';
  const fsFrame = $('fastscreen-frame');
  if (fsFrame) fsFrame.style.display = 'none';
  $('settings-frame').style.display = 'flex';
  // 进入贴边模式：取消 terminal-stage 的 padding，让设置界面贴边显示（不悬浮）
  $('terminal-stage').classList.add('stage-flush');
  // 隐藏终端专属状态项
  $('status-pty').style.display = 'none';
  $('status-size').style.display = 'none';
  renderSettingsView();
  try { updateAutoHide(); } catch (e) { warn('settings', 'updateAutoHide: %s', e); }
}

/**
 * 渲染设置 tab（由 ui.js renderTabs 调用，返回 tab DOM 元素）。
 * @returns {HTMLElement|null} 设置 tab 元素，若不在 tabOrder 中返回 null
 */
export function buildSettingsTabElement() {
  if (!state.tabOrder.includes(SETTINGS_TAB_ID)) return null;
  const tab = document.createElement('div');
  tab.className = 'tab settings-tab' + (state.activeTab === SETTINGS_TAB_ID ? ' active' : '');
  tab.dataset.sid = SETTINGS_TAB_ID;
  tab.innerHTML =
    '<span class="tab-icon running"></span>' +
    '<span class="tab-title" title="设置">设置</span>' +
    '<span class="tab-close" data-sid="' + SETTINGS_TAB_ID + '" title="关闭标签">' +
    '<svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>' +
    '</span>';
  tab.onclick = e => {
    if (e.target.closest('.tab-close')) return;
    openSettingsTab();
  };
  tab.oncontextmenu = e => {
    e.preventDefault();
    // 设置 tab 右键菜单暂不提供额外操作，仅防止默认右键
  };
  const closeBtn = tab.querySelector('.tab-close');
  closeBtn.onclick = e => {
    e.stopPropagation();
    closeSettingsTab();
  };
  return tab;
}

/**
 * 渲染设置视图骨架（左侧导航 + 右侧内容容器）。
 * 设置项变更由 settingsStore 自动保存（debounced），无需底部操作栏。
 */
export function renderSettingsView() {
  const nav = $('settings-nav');
  const content = $('settings-content');
  if (!nav || !content) return;

  // 渲染左侧导航
  nav.innerHTML = '';
  for (const cat of SETTINGS_CATEGORIES) {
    const item = document.createElement('div');
    item.className = 'settings-nav-item' + (_activeCategory === cat.id ? ' active' : '');
    item.dataset.cat = cat.id;
    item.innerHTML = cat.icon +
      '<span>' + cat.label + '</span>' +
      (cat.future ? '<span class="nav-badge-future">未来</span>' : '');
    item.onclick = () => {
      _activeCategory = cat.id;
      _renderNavActive();
      _renderContent();
    };
    nav.appendChild(item);
  }

  _renderContent();
}

// ── 内部：渲染导航激活态 ──
function _renderNavActive() {
  const nav = $('settings-nav');
  if (!nav) return;
  Array.from(nav.children).forEach(el => {
    el.classList.toggle('active', el.dataset.cat === _activeCategory);
  });
}

// ── 内部：渲染右侧内容 ──
function _renderContent() {
  const content = $('settings-content');
  if (!content) return;

  // 安全设置（未来）显示占位
  if (_activeCategory === 'security') {
    content.innerHTML =
      '<div class="settings-placeholder">' +
        '<svg viewBox="0 0 48 48" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2">' +
          '<path d="M24 4l16 6v14c0 10-7 18-16 22-9-4-16-12-16-22V10l16-6z" stroke-linejoin="round"/>' +
          '<path d="M16 24l6 6 12-12" stroke-linecap="round" stroke-linejoin="round"/>' +
        '</svg>' +
        '<div>安全设置正在开发中，敬请期待</div>' +
        '<div style="margin-top:8px;font-size:11px">计划支持：执行沙箱、操作审批、命令白名单、审计日志</div>' +
      '</div>';
    return;
  }

  const grouped = groupByCategory();
  const sections = grouped[_activeCategory] || [];
  // 非触摸设备隐藏 mobileOnly 项（如键盘方案，仅移动端需要）
  const isTouch = document.body.classList.contains('touch-device');
  let html = '';
  for (const sec of sections) {
    const visibleItems = sec.items.filter(it => !it.mobileOnly || isTouch);
    if (visibleItems.length === 0) continue;
    html += '<div class="settings-section-title">' + _escHtml(sec.name) + '</div>';
    for (const item of visibleItems) {
      html += _renderRow(item);
    }
  }
  content.innerHTML = html;
  _bindContentEvents();
}

// ── 内部：渲染单行设置项 ──
function _renderRow(item) {
  const enabled = item.enabled !== false;  // 默认启用
  let value = store.get(item.key);
  if (item.key === 'basic.serverAddress') {
    value = localStorage.getItem(LS_SERVER_ADDR_KEY) || '';
  }
  let controlHtml = '';
  switch (item.type) {
    case SETTING_TYPES.PILLS:
      controlHtml = _renderPills(item, value, enabled);
      break;
    case SETTING_TYPES.TOGGLE:
      controlHtml = _renderToggle(item, value, enabled);
      break;
    case SETTING_TYPES.SELECT:
      controlHtml = _renderSelect(item, value, enabled);
      break;
    case SETTING_TYPES.STEPPER:
      controlHtml = _renderStepper(item, value, enabled);
      break;
    case SETTING_TYPES.INPUT:
      controlHtml = _renderInput(item, value, enabled);
      break;
    case SETTING_TYPES.TEXTAREA:
      controlHtml = _renderTextarea(item, value, enabled);
      break;
    case SETTING_TYPES.ACTION:
      controlHtml = _renderAction(item, value, enabled);
      break;
    default:
      controlHtml = '<span style="color:var(--wt-tab-text-muted)">未实现的控件类型: ' + item.type + '</span>';
  }
  const label = item.key === 'rikka.enabled' ? _rikkaLabel() : _escHtml(item.label);
  return (
    '<div class="settings-row">' +
      '<div class="settings-row-info">' +
        '<div class="settings-row-label">' + label + '</div>' +
        '<div class="settings-row-desc">' + item.desc + '</div>' +
      '</div>' +
      '<div class="settings-control">' + controlHtml + '</div>' +
    '</div>'
  );
}

function _renderToggle(item, value, enabled) {
  const cls = 'settings-toggle' + (value ? ' on' : '') + (enabled ? '' : ' disabled');
  return '<div class="' + cls + '" data-key="' + item.key + '"' + (enabled ? '' : ' data-disabled="1"') + '></div>';
}

function _renderSelect(item, value, enabled) {
  // 用 String() 比较，避免数字 option value 与字符串 store value 类型不匹配导致选中态丢失
  const opts = (item.options || []).map(o =>
    '<option value="' + _escAttr(o.value) + '"' + (String(o.value) === String(value) ? ' selected' : '') + '>' + _escHtml(o.label) + '</option>'
  ).join('');
  return '<select class="settings-select" data-key="' + item.key + '"' + (enabled ? '' : ' disabled') + '>' + opts + '</select>';
}

function _renderStepper(item, value, enabled) {
  const display = (value == null ? '' : value) + ' ' + (item.unit || '');
  return (
    '<div class="settings-stepper" data-key="' + item.key + '" data-min="' + item.min + '" data-max="' + item.max + '" data-step="' + item.step + '"' + (enabled ? '' : ' data-disabled="1"') + '>' +
      '<button type="button" data-action="dec"' + (enabled ? '' : ' disabled') + '>-</button>' +
      '<span class="stepper-value">' + _escHtml(display.trim()) + '</span>' +
      '<button type="button" data-action="inc"' + (enabled ? '' : ' disabled') + '>+</button>' +
    '</div>'
  );
}

function _renderPills(item, value, enabled) {
  const pills = (item.options || []).map(o =>
    '<div class="settings-pill' + (o.value === value ? ' active' : '') + '" data-key="' + item.key + '" data-value="' + _escAttr(o.value) + '"' + (enabled ? '' : ' data-disabled="1"') + '>' + _escHtml(o.label) + '</div>'
  ).join('');
  return '<div class="settings-pills">' + pills + '</div>';
}

function _renderInput(item, value, enabled) {
  return '<input class="settings-input" data-key="' + item.key + '" value="' + _escAttr(value == null ? '' : value) + '"' +
    (item.placeholder ? ' placeholder="' + _escAttr(item.placeholder) + '"' : '') +
    (enabled ? '' : ' disabled') + '>';
}

function _renderTextarea(item, value, enabled) {
  return '<textarea class="settings-textarea" data-key="' + item.key + '" rows="3"' +
    (item.placeholder ? ' placeholder="' + _escAttr(item.placeholder) + '"' : '') +
    (enabled ? '' : ' disabled') + '>' + _escHtml(value == null ? '' : value) + '</textarea>';
}

function _renderAction(item, value, enabled) {
  const display = value ? _escHtml(value) : '<span style="color:var(--wt-tab-text-muted)">默认</span>';
  return '<div class="settings-action" data-key="' + item.key + '"' + (enabled ? '' : ' data-disabled="1"') + '>' + display + '</div>';
}

// ── 内部：绑定右侧内容区控件事件 ──
function _bindContentEvents() {
  const content = $('settings-content');
  if (!content) return;

  // toggle 点击
  content.querySelectorAll('.settings-toggle').forEach(el => {
    if (el.dataset.disabled === '1') return;
    const key = el.dataset.key;
    // remote.cursorLocator 是服务端状态，初始值从 state 同步，点击时发 WS 消息
    if (key === 'remote.cursorLocator') {
      const running = state.fastscreen.cursorLocatorRunning;
      el.classList.toggle('on', running);
      el.onclick = () => {
        const next = !el.classList.contains('on');
        el.classList.toggle('on', next);
        store.set(key, next);
      };
      return;
    }
    el.onclick = () => {
      const next = !el.classList.contains('on');
      el.classList.toggle('on', next);
      store.set(key, next);
    };
  });

  // select 变化：从 schema options 中查找原始 value，保留数字等非字符串类型
  // （el.value 始终为字符串，直接存入会导致再次渲染时 === 比较失败）
  content.querySelectorAll('.settings-select').forEach(el => {
    el.onchange = () => {
      const key = el.dataset.key;
      const item = SETTINGS_SCHEMA.find(i => i.key === key);
      const opt = item && item.options && item.options.find(o => String(o.value) === el.value);
      store.set(key, opt ? opt.value : el.value);
    };
  });

  // stepper +/- 按钮
  content.querySelectorAll('.settings-stepper').forEach(el => {
    if (el.dataset.disabled === '1') return;
    const min = parseInt(el.dataset.min, 10);
    const max = parseInt(el.dataset.max, 10);
    const step = parseInt(el.dataset.step, 10) || 1;
    const key = el.dataset.key;
    const valEl = el.querySelector('.stepper-value');
    const decBtn = el.querySelector('[data-action="dec"]');
    const incBtn = el.querySelector('[data-action="inc"]');
    const apply = (next) => {
      if (Number.isFinite(min)) next = Math.max(min, next);
      if (Number.isFinite(max)) next = Math.min(max, next);
      const unit = (SETTINGS_SCHEMA.find(i => i.key === key) || {}).unit || '';
      valEl.textContent = (next + ' ' + unit).trim();
      store.set(key, next);
    };
    decBtn.onclick = () => {
      const cur = store.get(key);
      apply((Number(cur) || 0) - step);
    };
    incBtn.onclick = () => {
      const cur = store.get(key);
      apply((Number(cur) || 0) + step);
    };
  });

  // pills 点击
  content.querySelectorAll('.settings-pill').forEach(el => {
    if (el.dataset.disabled === '1') return;
    el.onclick = () => {
      const key = el.dataset.key;
      const val = el.dataset.value;
      // 取消同组其他 pill 的 active，点亮当前
      content.querySelectorAll('.settings-pill[data-key="' + key + '"]').forEach(p => p.classList.remove('active'));
      el.classList.add('active');
      store.set(key, val);
    };
  });

  // input/textarea 变化（change 时提交，避免输入过程频繁触发）
  content.querySelectorAll('.settings-input, .settings-textarea').forEach(el => {
    el.onchange = () => {
      store.set(el.dataset.key, el.value);
    };
  });

  // action 点击（弹出对话框）
  content.querySelectorAll('.settings-action').forEach(el => {
    if (el.dataset.disabled === '1') return;
    el.onclick = () => {
      const key = el.dataset.key;
      if (key === 'basic.serverAddress') {
        _showServerAddrDialog();
      }
    };
  });
}

// ── 内部：rikka label 动态生成 ──
function _rikkaCnNum(n) {
  const map = { 1: '一', 2: '两', 3: '三', 4: '四', 5: '五', 6: '六', 7: '七', 8: '八', 9: '九', 10: '十' };
  return map[n] || String(n);
}
function _rikkaLabel() {
  const n = parseInt(localStorage.getItem('pty_rikka_count'), 10) || 1;
  return n === 1 ? '获取一只rikka' : '获取' + _rikkaCnNum(n) + '只rikka';
}

// ── 内部：HTML 转义（避免引入 formatters 循环依赖，本地实现） ──
function _escHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function _escAttr(s) {
  return _escHtml(s);
}

// ── 内部：服务端地址对话框 ──
function _showServerAddrDialog() {
  const overlay = $('server-addr-overlay');
  const input = $('server-addr-input');
  if (!overlay || !input) return;
  const current = localStorage.getItem(LS_SERVER_ADDR_KEY) || '';
  input.value = current;
  overlay.style.display = 'flex';
  setTimeout(() => input.focus(), 50);
}

function _hideServerAddrDialog() {
  const overlay = $('server-addr-overlay');
  if (overlay) overlay.style.display = 'none';
}

function _confirmServerAddr() {
  const input = $('server-addr-input');
  if (!input) return;
  const addr = input.value.trim();
  if (addr) {
    localStorage.setItem(LS_SERVER_ADDR_KEY, addr);
  } else {
    localStorage.removeItem(LS_SERVER_ADDR_KEY);
  }
  _hideServerAddrDialog();
  info('settings', 'server address set to: %s, reloading...', addr || '(default)');
  location.reload();
}

function _bindServerAddrDialogEvents() {
  const okBtn = $('server-addr-ok');
  const cancelBtn = $('server-addr-cancel');
  const overlay = $('server-addr-overlay');
  const input = $('server-addr-input');
  if (okBtn) okBtn.onclick = _confirmServerAddr;
  if (cancelBtn) cancelBtn.onclick = _hideServerAddrDialog;
  if (input) input.onkeydown = e => {
    if (e.key === 'Enter') { e.preventDefault(); _confirmServerAddr(); }
    if (e.key === 'Escape') { e.preventDefault(); _hideServerAddrDialog(); }
  };
}

/**
 * 绑定设置相关 DOM 事件（由 events.js 调用）。
 */
export function bindSettingsEvents() {
  const btn = $('btn-settings');
  if (btn) {
    btn.onclick = () => {
      debug('settings', 'btn-settings click → openSettingsTab');
      openSettingsTab();
    };
  }
}

/**
 * 初始化设置视图（由 app.js 启动时调用）。
 * 绑定入口按钮事件。
 * 设置数据由 app.js init() 中 await settingsStore.load() 统一加载，此处不再重复加载。
 * autohide 已由 vnc/fastscreen 初始化时绑定（_eventsBound 守护），无需重复调用。
 */
export function initSettingsView() {
  bindSettingsEvents();
  _bindServerAddrDialogEvents();

  // 订阅外部设置变更：当 developer.logPanelEnabled / basic.theme 被其他模块
  // （如 devConsole 关闭按钮、状态栏主题按钮）修改时，即时刷新设置面板中的 UI。
  store.subscribe((key, value) => {
    if (key === 'developer.logPanelEnabled') {
      const toggle = document.querySelector('.settings-toggle[data-key="developer.logPanelEnabled"]');
      if (toggle) {
        toggle.classList.toggle('on', Boolean(value));
      }
    } else if (key === 'basic.theme') {
      // 更新主题 pills 的 active 态
      const pills = document.querySelectorAll('.settings-pill[data-key="basic.theme"]');
      pills.forEach(p => {
        p.classList.toggle('active', p.dataset.value === value);
      });
    }
  });
}

// ── 注册 settings 会话 handler（模块加载时执行） ──
// handler 接口见 sessionHandlers.js，与 vnc/fastscreen handler 并列。
registerSessionHandler('settings', {
  // 切换到设置 frame（隐藏其他 frame，显示 settings-frame）
  switchTo: (sid) => switchToSettingsFrame(),
  // 关闭时类型特定清理：标记非活跃
  close: (sid) => { state.sessions[sid].running = false; },
  // 构建设置 tab DOM 元素
  buildTab: (sid) => buildSettingsTabElement(),
  // 页面刷新后恢复设置 tab：重新渲染
  restore: (sid) => { renderSettingsView(); },
  // 设置 tab 永远有效
  isValid: (sid) => true,
  // 打开设置 tab
  open: (sid) => openSettingsTab(),
});

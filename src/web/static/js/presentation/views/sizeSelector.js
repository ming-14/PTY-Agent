/**
 * 表现层：终端尺寸选择器视图
 *
 * 负责渲染状态栏左下角的终端尺寸下拉菜单：
 * - 默认（使用守护进程配置）
 * - 自适应（网页根据容器计算，不低于 80x24）
 * - 固定预设（80x24 / 100x30 / ...）
 * - 自定义（用户输入 cols x rows）
 *
 * 选择后通过 setSizeMode / setFixedSize / setCustomSize 更新领域状态，
 * 并调用 applyTerminalSize 重新计算终端尺寸与守护进程同步。
 *
 * 尺寸配置按会话 uid 独立存储：下拉菜单读取/写入当前活动会话的配置，
 * 切换标签页时自动反映该会话自身的模式。
 *
 * 自适应排他锁。
 * - 当其他连接持有自适应锁时（isSizeUILocked=true），下拉内选项灰显禁用，
 *   底部显示"接管"按钮，点击后发 takeover_size_control，等后端清锁后用户再选模式。
 * - selectMode/selectFixedPreset/selectCustomSize 切换模式时同步发 set_size_mode 到后端，
 *   后端据此维护 AdaptiveLockService 锁状态并广播 size_mode_changed 通知其他客户端。
 */

import {
  state,
  setSizeMode,
  setFixedSize,
  setCustomSize,
  getSessionSizeConfigBySid,
  isSizeUILocked,
} from '../../domain/state.js';
import { TERMINAL_SIZE_PRESETS, ADAPTIVE_MIN_COLS, ADAPTIVE_MIN_ROWS } from '../../domain/constants.js';
import { $, showToast } from '../../infrastructure/domUtils.js';
import { getHandlerBySid } from './sessionHandlers.js';
import { debug, info } from '../../domain/logger.js';
import { wsSend } from '../../infrastructure/wsClient.js';
import {
  reapplyAllTerminalSizes,
  applySessionFrameRatio,
} from '../../infrastructure/terminalAdapter.js';

/**
 * 渲染尺寸选择器下拉内容。
 * 每次打开时重新渲染，以反映当前活动会话的模式与守护进程默认尺寸。
 *
 * 当 isSizeUILocked(sid)=true（其他连接持有自适应锁）时：
 * - 所有尺寸选项灰显禁用，点击不响应
 * - 自定义输入区只读
 * - 底部追加"接管尺寸控制"按钮，点击发 takeover_size_control
 */
export function renderSizeDropdown() {
  const dd = $('size-dropdown');
  if (!dd) return;
  dd.innerHTML = '';

  const sid = state.activeTab;
  const s = sid ? state.sessions[sid] : null;
  // 当前活动会话的尺寸配置（按 uid 查询）
  const cfg = getSessionSizeConfigBySid(sid);
  // 是否被其他连接持有自适应锁（UI 灰显 + 接管按钮）
  const locked = isSizeUILocked(sid);
  if (locked) {
    info('size', 'renderSizeDropdown: sid=%s is locked by another connection', sid);
  }

  // 模式选项区
  const modeSection = document.createElement('div');
  modeSection.className = 'size-dropdown-section';
  modeSection.textContent = '尺寸模式';
  dd.appendChild(modeSection);

  // 默认
  const defaultItem = document.createElement('div');
  defaultItem.className = 'size-dropdown-item'
    + (cfg.mode === 'default' ? ' selected' : '')
    + (locked ? ' disabled' : '');
  // 优先使用该会话缓存的守护进程默认尺寸，其次使用会话当前尺寸
  const defaultDesc = (cfg.daemonCols && cfg.daemonRows)
    ? cfg.daemonCols + 'x' + cfg.daemonRows
    : (s ? (s.cols || '?') + 'x' + (s.rows || '?') : '守护进程配置');
  defaultItem.innerHTML =
    '<span>默认</span>' +
    '<span class="size-item-value">' + defaultDesc + '</span>';
  if (!locked) defaultItem.onclick = () => selectMode('default');
  dd.appendChild(defaultItem);

  // 自适应
  const adaptiveItem = document.createElement('div');
  adaptiveItem.className = 'size-dropdown-item'
    + (cfg.mode === 'adaptive' ? ' selected' : '')
    + (locked ? ' disabled' : '');
  adaptiveItem.innerHTML =
    '<span>自适应</span>' +
    '<span class="size-item-value">≥' + ADAPTIVE_MIN_COLS + 'x' + ADAPTIVE_MIN_ROWS + '</span>';
  if (!locked) adaptiveItem.onclick = () => selectMode('adaptive');
  dd.appendChild(adaptiveItem);

  // 分隔线
  const divider1 = document.createElement('div');
  divider1.className = 'size-dropdown-divider';
  dd.appendChild(divider1);

  // 固定预设区
  const presetSection = document.createElement('div');
  presetSection.className = 'size-dropdown-section';
  presetSection.textContent = '固定尺寸';
  dd.appendChild(presetSection);

  TERMINAL_SIZE_PRESETS.forEach(preset => {
    const item = document.createElement('div');
    const isFixedSelected = cfg.mode === 'fixed'
      && cfg.fixedCols === preset.cols
      && cfg.fixedRows === preset.rows;
    item.className = 'size-dropdown-item'
      + (isFixedSelected ? ' selected' : '')
      + (locked ? ' disabled' : '');
    item.innerHTML =
      '<span>' + preset.label + '</span>' +
      '<span class="size-item-value">' + preset.cols + 'x' + preset.rows + '</span>';
    if (!locked) item.onclick = () => selectFixedPreset(preset.cols, preset.rows);
    dd.appendChild(item);
  });

  // 分隔线
  const divider2 = document.createElement('div');
  divider2.className = 'size-dropdown-divider';
  dd.appendChild(divider2);

  // 自定义
  const customItem = document.createElement('div');
  const isCustomSelected = cfg.mode === 'custom';
  customItem.className = 'size-dropdown-item'
    + (isCustomSelected ? ' selected' : '')
    + (locked ? ' disabled' : '');
  customItem.innerHTML =
    '<span>自定义</span>' +
    '<span class="size-item-value">' +
      (isCustomSelected ? cfg.customCols + 'x' + cfg.customRows : '输入尺寸') +
    '</span>';
  if (!locked) {
    customItem.onclick = () => {
      if (!isCustomSelected) {
        selectMode('custom');
      }
      // 选中后聚焦到自定义输入框
      setTimeout(() => {
        const input = dd.querySelector('.size-custom-input input');
        if (input) input.focus();
      }, 0);
    };
  }
  dd.appendChild(customItem);

  // 自定义输入区（始终渲染，方便用户随时修改；locked 时只读）
  const customInput = document.createElement('div');
  customInput.className = 'size-custom-input' + (locked ? ' disabled' : '');
  customInput.innerHTML =
    '<input type="number" id="size-custom-cols" min="' + ADAPTIVE_MIN_COLS + '" max="400" value="' + cfg.customCols + '" placeholder="列"' + (locked ? ' disabled' : '') + '>' +
    '<span class="size-x">x</span>' +
    '<input type="number" id="size-custom-rows" min="' + ADAPTIVE_MIN_ROWS + '" max="120" value="' + cfg.customRows + '" placeholder="行"' + (locked ? ' disabled' : '') + '>' +
    '<button id="size-custom-apply"' + (locked ? ' disabled' : '') + '>应用</button>';
  dd.appendChild(customInput);

  if (!locked) {
    const colsInput = customInput.querySelector('#size-custom-cols');
    const rowsInput = customInput.querySelector('#size-custom-rows');
    const applyBtn = customInput.querySelector('#size-custom-apply');

    const applyCustom = () => {
      const cols = parseInt(colsInput.value, 10);
      const rows = parseInt(rowsInput.value, 10);
      if (!Number.isFinite(cols) || !Number.isFinite(rows)) {
        showToast('请输入有效的数字', 'error');
        return;
      }
      if (cols < ADAPTIVE_MIN_COLS || rows < ADAPTIVE_MIN_ROWS) {
        showToast('尺寸不能小于 ' + ADAPTIVE_MIN_COLS + 'x' + ADAPTIVE_MIN_ROWS, 'error');
        return;
      }
      if (cols > 400 || rows > 120) {
        showToast('尺寸不能超过 400x120', 'error');
        return;
      }
      selectCustomSize(cols, rows);
    };
    applyBtn.onclick = applyCustom;
    [colsInput, rowsInput].forEach(input => {
      input.addEventListener('keydown', e => {
        if (e.key === 'Enter') {
          e.preventDefault();
          applyCustom();
        }
      });
    });
  }

  // 被其他连接持锁时，底部显示"接管尺寸控制"按钮
  if (locked) {
    const takeoverBtn = document.createElement('button');
    takeoverBtn.id = 'size-takeover-btn';
    takeoverBtn.className = 'size-takeover-btn';
    takeoverBtn.textContent = '接管尺寸控制';
    takeoverBtn.onclick = () => requestTakeover(sid);
    dd.appendChild(takeoverBtn);
  }
}

/**
 * 定位尺寸选择器下拉到 #status-size 附近。
 */
export function positionSizeDropdown() {
  const dd = $('size-dropdown');
  const anchor = $('status-size');
  if (!dd || !anchor) return;
  const rect = anchor.getBoundingClientRect();
  const ddRect = dd.getBoundingClientRect();
  // 默认放在 status-size 下方右对齐
  let left = rect.right - ddRect.width;
  let top = rect.bottom + 4;
  if (left < 8) left = 8;
  if (left + ddRect.width > window.innerWidth - 8) left = window.innerWidth - ddRect.width - 8;
  // 状态栏在底部，下拉默认向上展开
  if (top + ddRect.height > window.innerHeight - 8) {
    top = rect.top - ddRect.height - 4;
  }
  if (top < 8) top = 8;
  dd.style.left = left + 'px';
  dd.style.top = top + 'px';
}

/**
 * 显示/隐藏尺寸选择器下拉。
 */
export function toggleSizeDropdown(show) {
  const dd = $('size-dropdown');
  if (!dd) return;
  if (show === undefined) show = dd.style.display === 'none';
  if (show) {
    renderSizeDropdown();
    dd.style.display = 'block';
    state.sizeSelectorVisible = true;
    requestAnimationFrame(positionSizeDropdown);
  } else {
    dd.style.display = 'none';
    state.sizeSelectorVisible = false;
  }
}

/**
 * 若尺寸选择器下拉当前已打开，则重新渲染（自适应锁状态变更后调用）。
 * 由 messageHandlers.handleSizeModeChanged / handleTakeoverAck 通过 ports.ui 调用，
 * 用于在锁状态变化时即时更新下拉内的灰显与接管按钮显示。
 */
export function refreshSizeSelectorIfOpen() {
  if (!state.sizeSelectorVisible) return;
  renderSizeDropdown();
  positionSizeDropdown();
}

/**
 * 选择尺寸模式（default / adaptive / custom）。
 * fixed 模式请用 selectFixedPreset。
 * 操作目标为当前活动会话。
 *
 * 所有模式切换后都调用 applySessionFrameRatio 初始化/恢复该会话的 frameRatio。
 * - adaptive 模式：按 ratio 设 frame 尺寸 + fit() 算 cols/rows（自适应 stage 宽高比，cols/rows 变）
 * - 非 adaptive 模式：按 ratio 反算字号（cols/rows 不变）
 *
 * 先发 set_size_mode 给后端（让 AdaptiveLockService 同步锁状态），
 * 后端按 FIFO 顺序处理 set_size_mode → 后续触发的 resize（持锁者允许）。
 * 然后本地乐观更新模式 + reapplyAllTerminalSizes 触发 resize 同步守护进程。
 */
function selectMode(mode) {
  const sid = state.activeTab;
  const prevCfg = getSessionSizeConfigBySid(sid);
  info('size', 'selectMode → %s (was %s) sid=%s', mode, prevCfg.mode, sid);

  // 先发 set_size_mode，后端 acquire/release 锁后再发 resize
  sendSetSizeMode(sid, mode);

  setSizeMode(mode);

  // 立即重新应用所有终端尺寸（仅活动会话的配置发生变化）
  // force=true：用户显式切换模式时强制同步守护进程，即使 s.cols 与目标一致
  reapplyAllTerminalSizes(true);

  // 更新状态栏显示
  updateSizeStatusDisplay();

  // 重新渲染下拉以反映选中状态
  renderSizeDropdown();
  positionSizeDropdown();

  // 所有模式都等一帧让 xterm 尺寸刷新后，初始化/应用该会话的 frameRatio。
  // - adaptive 模式：按 ratio 设 frame 尺寸 + fit() 算 cols/rows（cols/rows 变）
  // - 非 adaptive 模式：按 ratio 反算字号（cols/rows 不变）
  requestAnimationFrame(() => {
    applySessionFrameRatio(sid);
    updateSizeStatusDisplay();
  });

  showToast(getModeLabel(mode) + ' 模式', 'info');
}

/**
 * 选择固定预设尺寸。
 * 操作目标为当前活动会话。
 */
function selectFixedPreset(cols, rows) {
  info('size', 'selectFixedPreset → %dx%d sid=%s', cols, rows, state.activeTab);
  // 先发 set_size_mode(fixed, cols, rows)，后端释放锁并 resize
  sendSetSizeMode(state.activeTab, 'fixed', cols, rows);

  setFixedSize(cols, rows);
  setSizeMode('fixed');

  // force=true：用户显式选择固定尺寸时强制同步守护进程
  reapplyAllTerminalSizes(true);
  updateSizeStatusDisplay();
  renderSizeDropdown();
  positionSizeDropdown();

  // 等一帧后初始化/应用 frameRatio（非 adaptive 模式按 ratio 反算字号）
  requestAnimationFrame(() => { applySessionFrameRatio(state.activeTab); });

  showToast('已切换到 ' + cols + 'x' + rows, 'info');
}

/**
 * 选择自定义尺寸。
 * 操作目标为当前活动会话。
 */
function selectCustomSize(cols, rows) {
  info('size', 'selectCustomSize → %dx%d sid=%s', cols, rows, state.activeTab);
  // 先发 set_size_mode(custom, cols, rows)，后端释放锁并 resize
  sendSetSizeMode(state.activeTab, 'custom', cols, rows);

  setCustomSize(cols, rows);
  setSizeMode('custom');

  // force=true：用户显式输入自定义尺寸时强制同步守护进程
  reapplyAllTerminalSizes(true);
  updateSizeStatusDisplay();
  renderSizeDropdown();
  positionSizeDropdown();

  // 等一帧后初始化/应用 frameRatio（非 adaptive 模式按 ratio 反算字号）
  requestAnimationFrame(() => { applySessionFrameRatio(state.activeTab); });

  showToast('已切换到 ' + cols + 'x' + rows, 'info');
}

// ── 自适应排他锁通信辅助 ──

/**
 * 发送 set_size_mode 到后端。
 * 后端 SetSizeModeHandler 会：
 * - adaptive：acquire 锁（旧持有者降级），广播 size_mode_changed
 * - fixed/custom：release 锁 + session.resize + 广播 session_resized + size_mode_changed
 * - default：release 锁，不主动 resize
 *
 * @param {string} sid 会话 id
 * @param {string} mode 'adaptive' | 'fixed' | 'custom' | 'default'
 * @param {number} [cols] fixed/custom 模式的列数
 * @param {number} [rows] fixed/custom 模式的行数
 */
function sendSetSizeMode(sid, mode, cols, rows) {
  const payload = { type: 'set_size_mode', session_id: sid, mode };
  if (cols != null) payload.cols = cols;
  if (rows != null) payload.rows = rows;
  wsSend(payload);
}

/**
 * 请求接管尺寸控制权。
 * 后端清空自适应锁（旧持有者降级），返回 takeover_ack 后用户再选新模式。
 * @param {string} sid 会话 id
 */
function requestTakeover(sid) {
  info('size', 'requestTakeover sid=%s', sid);
  wsSend({ type: 'takeover_size_control', session_id: sid });
}

/**
 * 根据会话自身的尺寸模式返回状态栏应显示的文本。
 *
 * 所有模式统一显示实际尺寸 s.cols x s.rows（仅 adaptive 追加 "(自适应)" 标签）。
 * 这样在任何"被动跟随"场景下状态栏都与 term 实际尺寸同步：
 * - session_resized（其他端 resize 广播）：s.cols/s.rows 已更新，状态栏跟随
 * - 本端主动 resize：onResize 已更新 s.cols/s.rows，状态栏跟随
 * 未被被动跟随时 s.cols == cfg.fixedCols/customCols，即显示模式设定值。
 *
 * @param {object} s 会话对象（state.sessions[sid]）
 */
export function getSizeStatusText(s) {
  if (!s) return '';
  const sid = s.id;
  const cfg = getSessionSizeConfigBySid(sid);
  // 历史会话固定显示生前最后尺寸（不带模式标签，因为已禁用模式切换）
  if (s.history) {
    return (s.cols || '?') + 'x' + (s.rows || '?');
  }
  // 所有模式统一显示实际尺寸，确保被动跟随时状态栏同步
  const sizeStr = (s.cols || '?') + 'x' + (s.rows || '?');
  if (cfg.mode === 'adaptive') {
    return sizeStr + ' (自适应)';
  }
  // fixed / custom / default 不带标签，仅显示实际尺寸
  return sizeStr;
}

/**
 * 更新状态栏尺寸项的显示。
 * 由 updateStatusInfo 调用，也可在模式切换后单独调用。
 */
export function updateSizeStatusDisplay() {
  const sid = state.activeTab;
  if (!sid) return;
  const s = state.sessions[sid];
  if (!s) return;
  // 非终端 tab（设置/VNC/FastScreen）不显示终端尺寸状态
  if (getHandlerBySid(sid)) return;
  const sizeEl = $('status-size');
  if (!sizeEl) return;

  const cfg = getSessionSizeConfigBySid(sid);
  sizeEl.style.display = 'flex';
  sizeEl.textContent = getSizeStatusText(s);
  // 仅自适应模式高亮（蓝色 + "(自适应)" 标签提示正在自适应）；
  // 退出自适应后（fixed/custom/default）不高亮，避免蓝色文字干扰
  sizeEl.classList.toggle('size-active', !s.history && cfg.mode === 'adaptive');
}

function getModeLabel(mode) {
  if (mode === 'default') return '默认';
  if (mode === 'adaptive') return '自适应';
  if (mode === 'fixed') return '固定';
  if (mode === 'custom') return '自定义';
  return mode;
}

/**
 * 表现层：会话详情对话框视图
 *
 * 负责会话详情、进程树、事件列表的渲染与自动刷新。
 */

import { $ } from '../../infrastructure/domUtils.js';
import { escHtml, formatAbsoluteTime, formatRelativeTime } from '../../domain/formatters.js';
import { state } from '../../domain/state.js';
import { t } from '../../domain/i18n.js';
import { wsSend, sendToSession } from '../../infrastructure/wsClient.js';
import { DEFAULT_COLS, DEFAULT_ROWS } from '../../domain/constants.js';

let currentDetailUid = null;
let currentTab = 'info';
let detailData = null;
let selectedProcessPid = null;
let refreshTimer = null;
let dialogResizeObserver = null;
const INFO_REFRESH_MS = 2000;
const PROCESS_REFRESH_MS = 3000;

function updateDialogOrientation() {
  const overlay = $('detail-overlay');
  if (!overlay) return;
  const dialog = overlay.querySelector('.detail-dialog');
  if (!dialog) return;
  const rect = dialog.getBoundingClientRect();
  const vertical = rect.height > rect.width;
  dialog.classList.toggle('vertical', vertical);
}

export function showDetailDialog(uid, data) {
  const alreadyOpen = currentDetailUid === uid;
  currentDetailUid = uid;
  detailData = data;
  const isHistory = detailData && (detailData.source === 'history' || !detailData.running);
  if (!alreadyOpen) {
    currentTab = 'info';
    selectedProcessPid = null;
  } else if (currentTab === 'process' && isHistory) {
    currentTab = 'info';
  }
  const overlay = $('detail-overlay');
  if (!overlay) return;
  overlay.style.display = 'flex';
  $('detail-title').textContent = t('detail.title', { sid: (data && data.id) || uid });
  renderTabs();
  renderContent();
  if (!alreadyOpen) startRefresh();
  // 监听对话框尺寸变化，按宽高比切换进程树面板布局
  const dialog = overlay.querySelector('.detail-dialog');
  if (dialog) {
    updateDialogOrientation();
    if (!dialogResizeObserver) {
      dialogResizeObserver = new ResizeObserver(() => updateDialogOrientation());
    }
    dialogResizeObserver.disconnect();
    dialogResizeObserver.observe(dialog);
  }
}

export function hideDetailDialog() {
  const overlay = $('detail-overlay');
  if (overlay) overlay.style.display = 'none';
  stopRefresh();
  if (dialogResizeObserver) {
    dialogResizeObserver.disconnect();
    dialogResizeObserver = null;
  }
  currentDetailUid = null;
  detailData = null;
  selectedProcessPid = null;
}

export function updateDetailData(data) {
  if (!currentDetailUid) return;
  const uid = data.sessionUid || data.uid || data.id;
  if (uid && uid !== currentDetailUid) return;
  detailData = data;
  renderContent();
}

export function applyDetailRefresh(msg) {
  if (!currentDetailUid || !detailData) return;
  const uid = msg.sessionUid || msg.uid || msg.id;
  if (uid && uid !== currentDetailUid) return;

  if (msg.tab === 'info') {
    if (msg.running !== undefined) detailData.running = msg.running;
    if (msg.exitCode !== undefined) detailData.exitCode = msg.exitCode;
    if (msg.errorMessage !== undefined) detailData.errorMessage = msg.errorMessage;
    if (msg.outputSize !== undefined) detailData.outputSize = msg.outputSize;
    if (currentTab === 'info') renderContent();
  } else if (msg.tab === 'process') {
    if (msg.processDetails) {
      if (!detailData.processDetails) detailData.processDetails = {};
      for (const [pid, d] of Object.entries(msg.processDetails)) {
        if (detailData.processDetails[pid]) {
          if (d.memoryMb !== undefined) detailData.processDetails[pid].memoryMb = d.memoryMb;
          if (d.cpuSeconds !== undefined) detailData.processDetails[pid].cpuSeconds = d.cpuSeconds;
        }
      }
    }
    if (currentTab === 'process' && selectedProcessPid) {
      showProcessDetail(selectedProcessPid);
    }
  }
}

export function appendDetailEvent(event) {
  if (!detailData || !detailData.events) return;
  detailData.events.push(event);
  if (currentTab === 'events') {
    renderEventItem(event);
  }
  if (currentTab === 'process' && (event.type === 'process_spawn' || event.type === 'process_exit' || event.type === 'process_crash')) {
    sendToSession(currentDetailUid, { type: 'session_detail' });
  }
}

function startRefresh() {
  stopRefresh();
  if (!detailData || !detailData.running) return;
  scheduleNext();
}

function stopRefresh() {
  if (refreshTimer) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
}

function scheduleNext() {
  if (!currentDetailUid || !detailData || !detailData.running) return;
  const interval = currentTab === 'process' ? PROCESS_REFRESH_MS : INFO_REFRESH_MS;
  refreshTimer = setTimeout(() => {
    if (!currentDetailUid || !detailData) return;
    if (currentTab === 'events') {
      scheduleNext();
      return;
    }
    sendToSession(currentDetailUid, { type: 'session_detail_refresh', tab: currentTab });
    scheduleNext();
  }, interval);
}

function renderTabs() {
  const tabs = $('detail-tabs');
  if (!tabs) return;
  tabs.innerHTML = '';
  const isHistory = detailData && (detailData.source === 'history' || !detailData.running);
  const tabDefs = [
    { key: 'info', label: t('detail.basic') },
    { key: 'process', label: t('detail.processTree'), hidden: isHistory },
    { key: 'events', label: t('detail.events') },
  ].filter(t => !t.hidden);
  tabDefs.forEach(t => {
    const el = document.createElement('div');
    el.className = 'detail-tab' + (currentTab === t.key ? ' active' : '');
    el.textContent = t.label;
    el.onclick = () => {
      const prevTab = currentTab;
      currentTab = t.key;
      renderTabs();
      if (currentTab === 'process' && prevTab !== 'process') {
        sendToSession(currentDetailUid, { type: 'session_detail' });
      } else {
        renderContent();
      }
      stopRefresh();
      scheduleNext();
    };
    tabs.appendChild(el);
  });
}

function renderContent() {
  const content = $('detail-content');
  if (!content || !detailData) return;
  content.innerHTML = '';
  if (currentTab === 'info') {
    renderInfoTab(content);
  } else if (currentTab === 'process') {
    renderProcessTab(content);
  } else if (currentTab === 'events') {
    renderEventsTab(content);
  }
}

function renderInfoTab(container) {
  const d = detailData;
  const isHistory = d.source === 'history' || !d.running;

  const rows = [
    { label: t('detail.sessionId'), value: d.id },
    { label: t('detail.command'), value: d.command },
    { label: t('detail.ptyType'), value: d.ptyType },
    { label: t('detail.encoding'), value: d.encoding },
    { label: t('detail.terminalSize'), value: (d.cols || DEFAULT_COLS) + ' x ' + (d.rows || DEFAULT_ROWS) },
  ];

  if (d.cwd) {
    rows.push({ label: t('detail.workdir'), value: d.cwd });
  }

  rows.push({ label: t('detail.status'), value: d.running ? t('detail.statusRunning') : t('detail.statusEnded') });
  rows.push({ label: t('detail.startTime'), value: d.startTime ? formatAbsoluteTime(d.startTime) : '-' });

  if (!d.running && d.endTime) {
    rows.push({ label: t('detail.endTime'), value: formatAbsoluteTime(d.endTime) });
  }
  if (d.exitCode !== null && d.exitCode !== undefined) {
    rows.push({ label: t('detail.exitCode'), value: String(d.exitCode) });
  }
  if (d.errorMessage) {
    rows.push({ label: t('detail.errorInfo'), value: d.errorMessage });
  }
  if (d.outputSize !== undefined) {
    rows.push({ label: t('detail.outputSize'), value: formatBytes(d.outputSize) });
  }

  const table = document.createElement('div');
  table.className = 'detail-table';
  rows.forEach(r => {
    const row = document.createElement('div');
    row.className = 'detail-row';
    row.innerHTML =
      '<div class="detail-row-label">' + escHtml(r.label) + '</div>' +
      '<div class="detail-row-value">' + escHtml(String(r.value)) + '</div>';
    table.appendChild(row);
  });
  container.appendChild(table);
}

function renderProcessTab(container) {
  const tree = detailData.processTree;
  if (!tree || tree.length === 0) {
    container.innerHTML = '<div class="detail-empty">' + t('detail.noProcess') + '</div>';
    return;
  }

  const wrapper = document.createElement('div');
  wrapper.className = 'detail-process-wrapper';

  const treeEl = document.createElement('div');
  treeEl.className = 'detail-process-tree';
  tree.forEach(node => treeEl.appendChild(buildProcessNode(node)));
  wrapper.appendChild(treeEl);

  const resizer = document.createElement('div');
  resizer.className = 'detail-process-resizer';
  wrapper.appendChild(resizer);

  const detailPanel = document.createElement('div');
  detailPanel.className = 'detail-process-detail';
  detailPanel.id = 'process-detail-panel';
  detailPanel.innerHTML = '<div class="detail-empty">' + t('detail.clickProcessHint') + '</div>';
  wrapper.appendChild(detailPanel);

  container.appendChild(wrapper);
  bindProcessResizer(resizer, wrapper, treeEl, detailPanel);
}

function buildProcessNode(node, depth, isLast) {
  depth = depth || 0;
  if (isLast === undefined) isLast = true;
  const el = document.createElement('div');
  el.className = 'process-node';

  const header = document.createElement('div');
  header.className = 'process-node-header' + (selectedProcessPid === node.pid ? ' selected' : '');

  let indent = '';
  for (let i = 0; i < depth; i++) {
    indent += '<span class="tree-indent"></span>';
  }
  const hasChildren = node.children && node.children.length > 0;
  const connector = depth > 0
    ? '<span class="tree-connector">' + (isLast ? '└─' : '├─') + '</span>'
    : '';

  header.innerHTML =
    indent + connector +
    '<span class="process-node-icon">' + (hasChildren ? '▼' : '●') + '</span>' +
    '<span class="process-node-name">' + escHtml(node.name) + '</span>' +
    '<span class="process-node-pid">(' + node.pid + ')</span>';
  header.onclick = () => {
    selectedProcessPid = node.pid;
    showProcessDetail(node.pid);
    document.querySelectorAll('.process-node-header.selected').forEach(e => e.classList.remove('selected'));
    header.classList.add('selected');
  };
  el.appendChild(header);

  if (hasChildren) {
    node.children.forEach((child, i) => {
      const childIsLast = i === node.children.length - 1;
      el.appendChild(buildProcessNode(child, depth + 1, childIsLast));
    });
  }
  return el;
}

function showProcessDetail(pid) {
  const panel = $('process-detail-panel');
  if (!panel) return;
  const details = detailData.processDetails;
  if (!details || !details[String(pid)]) {
    panel.innerHTML = '<div class="detail-empty">' + t('detail.noProcessDetail') + '</div>';
    return;
  }
  const d = details[String(pid)];
  const rows = [
    { label: 'PID', value: d.pid },
    { label: t('detail.processName'), value: d.name },
    { label: t('detail.fullPath'), value: d.path || '-' },
    { label: t('detail.cmdline'), value: d.commandLine || '-' },
    { label: t('detail.parentPid'), value: d.ppid || '-' },
    { label: t('detail.memory'), value: d.memoryMb !== null && d.memoryMb !== undefined ? d.memoryMb + ' MB' : '-' },
    { label: t('detail.cpuTime'), value: d.cpuSeconds !== null && d.cpuSeconds !== undefined ? d.cpuSeconds + t('detail.secondsSuffix') : '-' },
    { label: t('detail.createdTime'), value: d.createTime ? formatAbsoluteTime(d.createTime) : '-' },
  ];

  const table = document.createElement('div');
  table.className = 'detail-table';
  rows.forEach(r => {
    const row = document.createElement('div');
    row.className = 'detail-row';
    row.innerHTML =
      '<div class="detail-row-label">' + escHtml(r.label) + '</div>' +
      '<div class="detail-row-value">' + escHtml(String(r.value)) + '</div>';
    table.appendChild(row);
  });
  panel.innerHTML = '';
  panel.appendChild(table);
}

function renderEventsTab(container) {
  const events = detailData.events;
  if (!events || events.length === 0) {
    container.innerHTML = '<div class="detail-empty">' + t('detail.noEvents') + '</div>';
    return;
  }

  const list = document.createElement('div');
  list.className = 'detail-event-list';
  list.id = 'detail-event-list';
  events.forEach(ev => {
    list.appendChild(buildEventItem(ev));
  });
  container.appendChild(list);
}

function renderEventItem(ev) {
  const list = $('detail-event-list');
  if (!list) return;
  list.appendChild(buildEventItem(ev));
  list.scrollTop = list.scrollHeight;
}

function buildEventItem(ev) {
  const el = document.createElement('div');
  el.className = 'detail-event-item';

  const typeLabel = getEventTypeLabel(ev.type);
  const typeClass = getEventTypeClass(ev.type);

  let detailText = '';
  if (ev.detail) {
    if (ev.type === 'process_spawn') {
      detailText = ev.detail.path || ev.detail.commandLine || ev.detail.name || ev.info || '';
    } else if (ev.type === 'process_exit' || ev.type === 'process_crash') {
      detailText = 'exit=' + (ev.detail.exitCode !== undefined ? ev.detail.exitCode : '?');
      if (ev.detail.errorMessage) detailText += ' ' + ev.detail.errorMessage;
    } else if (ev.type === 'gui_window') {
      detailText = (ev.detail.title || '') + (ev.detail.className ? ' [' + ev.detail.className + ']' : '');
    } else {
      detailText = ev.detail.info || ev.detail.path || ev.detail.name || JSON.stringify(ev.detail);
    }
  } else if (ev.info) {
    detailText = ev.info;
  }

  el.innerHTML =
    '<div class="detail-event-time">' + escHtml(ev.time || '') + '</div>' +
    '<div class="detail-event-type ' + typeClass + '">' + escHtml(typeLabel) + '</div>' +
    '<div class="detail-event-info">' +
      '<span class="detail-event-pid">' + (ev.pid ? '[' + ev.pid + '] ' : '') + '</span>' +
      escHtml(detailText) +
    '</div>';
  return el;
}

function getEventTypeLabel(type) {
  const map = {
    'process_spawn': t('detail.eventSpawn'),
    'process_exit': t('detail.eventExit'),
    'process_crash': t('detail.eventCrash'),
    'gui_window': t('detail.eventGuiWindow'),
    'encoding_change': t('detail.eventEncodingChange'),
  };
  return map[type] || type;
}

function getEventTypeClass(type) {
  if (type === 'process_spawn') return 'event-type-spawn';
  if (type === 'process_exit') return 'event-type-exit';
  if (type === 'process_crash') return 'event-type-crash';
  if (type === 'gui_window') return 'event-type-gui';
  return '';
}

function formatBytes(bytes) {
  if (bytes === 0) return '0 B';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function bindProcessResizer(resizer, wrapper, treeEl, detailPanel) {
  const isVertical = () => wrapper.closest('.detail-dialog.vertical') !== null;
  let startPos = 0;
  let startTreeSize = 0;
  let startDetailSize = 0;

  function applyResize(pos) {
    const delta = pos - startPos;
    if (isVertical()) {
      const newTreeH = Math.max(60, startTreeSize + delta);
      treeEl.style.height = newTreeH + 'px';
      treeEl.style.flex = 'none';
    } else {
      const newDetailW = Math.max(120, startDetailSize - delta);
      detailPanel.style.width = newDetailW + 'px';
      detailPanel.style.minWidth = newDetailW + 'px';
    }
  }

  function finishResize() {
    resizer.classList.remove('active');
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
    document.removeEventListener('touchmove', onTouchMove);
    document.removeEventListener('touchend', onTouchEnd);
  }

  function onMouseMove(e) {
    applyResize(isVertical() ? e.clientY : e.clientX);
  }
  function onMouseUp() { finishResize(); }
  function onTouchMove(e) {
    e.preventDefault();
    applyResize(isVertical() ? e.touches[0].clientY : e.touches[0].clientX);
  }
  function onTouchEnd() { finishResize(); }

  function startResize(pos) {
    resizer.classList.add('active');
    if (isVertical()) {
      startPos = pos;
      startTreeSize = treeEl.getBoundingClientRect().height;
      startDetailSize = detailPanel.getBoundingClientRect().height;
    } else {
      startPos = pos;
      startTreeSize = treeEl.getBoundingClientRect().width;
      startDetailSize = detailPanel.getBoundingClientRect().width;
    }
  }

  resizer.addEventListener('mousedown', e => {
    e.preventDefault();
    startResize(isVertical() ? e.clientY : e.clientX);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  });

  resizer.addEventListener('touchstart', e => {
    e.preventDefault();
    startResize(isVertical() ? e.touches[0].clientY : e.touches[0].clientX);
    document.addEventListener('touchmove', onTouchMove, { passive: false });
    document.addEventListener('touchend', onTouchEnd);
  });
}

export function initDetailDialog() {
  const overlay = $('detail-overlay');
  if (!overlay) return;
  // 仅在 mousedown 和 mouseup 都发生在遮罩（非对话框内）时关闭，
  // 避免用户在对话框内按下鼠标、拖到外部松开选择文本时误关闭。
  let mouseDownInside = false;
  overlay.addEventListener('mousedown', e => {
    mouseDownInside = !!e.target.closest('.dialog');
  });
  overlay.addEventListener('click', e => {
    if (mouseDownInside) return;
    if (e.target === overlay) hideDetailDialog();
  });
  const closeBtn = $('detail-close');
  if (closeBtn) {
    closeBtn.onclick = () => hideDetailDialog();
  }
}

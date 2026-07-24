/**
 * 基础设施层：DOM 操作适配器
 *
 * 封装对浏览器 DOM 的访问，为上层提供稳定的 DOM 工具接口。
 */

import { state } from '../domain/state.js';

export const $ = id => document.getElementById(id);

export function setStatus(st, text) {
  const dot = $('status-dot');
  if (dot) dot.className = 'status-dot ' + st;
  const el = $('status-text');
  if (el) el.textContent = text;
}

export function showToast(message, type) {
  const container = $('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'toast ' + (type || 'info');
  toast.textContent = message;
  container.appendChild(toast);
  void toast.offsetWidth;
  toast.classList.add('show');
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 250);
  }, 5000);
}

export function showConfirm(title, body, onOk) {
  state.confirmOkCallback = onOk;
  $('confirm-title').textContent = title;
  $('confirm-body').textContent = body;
  $('confirm-overlay').style.display = 'flex';
}

export function hideConfirm() {
  $('confirm-overlay').style.display = 'none';
  state.confirmOkCallback = null;
}

export function updateSystemStatsUI(stats) {
  const cpuEl = $('status-cpu');
  const memEl = $('status-mem');
  function fmt(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return '-';
    return v.toFixed(1) + '%';
  }
  if (cpuEl) cpuEl.textContent = 'CPU ' + fmt(stats.cpu);
  if (memEl) memEl.textContent = 'MEM ' + fmt(stats.memory);
}

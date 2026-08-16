/**
 * 领域层：纯格式化/转义工具
 *
 * 这些函数无副作用、不依赖浏览器 DOM 或存储，可在领域层安全使用。
 */

import { t } from './i18n.js'

export function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

export function escAttr(s) {
  return String(s).replace(/[&"'<>]/g, c => ({
    '&': '&amp;',
    '"': '&quot;',
    "'": '&#39;',
    '<': '&lt;',
    '>': '&gt;',
  })[c]);
}

export function formatRelativeTime(timestamp) {
  if (!timestamp) return '';
  const now = Date.now() / 1000;
  const diff = now - timestamp;
  if (diff < 60) return t('time.justNow');
  if (diff < 3600) return t('time.minutesAgo', { n: Math.floor(diff / 60) });
  if (diff < 86400) return t('time.hoursAgo', { n: Math.floor(diff / 3600) });
  if (diff < 604800) return t('time.daysAgo', { n: Math.floor(diff / 86400) });
  return formatAbsoluteTime(timestamp);
}

export function formatAbsoluteTime(timestamp) {
  if (!timestamp) return '';
  const d = new Date(timestamp * 1000);
  const pad = n => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
    ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
}

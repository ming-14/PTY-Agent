/**
 * 基础设施层：设置存储适配器
 *
 * 封装两层持久化：
 * 1. localStorage：用户自定义设置的唯一持久化位置（读写）
 * 2. /api/settings GET：拉取 web.toml 默认值（只读，作为 localStorage 未命中时的兜底）
 *
 * 数据流：
 * - 用户修改设置 → 仅写 localStorage（不走 POST 服务端）
 * - 启动加载优先级：localStorage > 服务端默认值(GET) > 前端 schema default
 * - POST /api/settings 保留端点但为空实现，本模块不调用
 *
 * 注意：与 storage.js 中零散的 localStorage key（pty_theme 等）的关系：
 * 本模块作为统一设置入口，迁移工作在后续阶段进行；当前为并存。
 */

import { debug, warn } from '../domain/logger.js';
import { handleUnauthorized, authHeaders } from './auth.js';

const LS_KEY = 'pty_user_settings';

/**
 * 从 localStorage 读取用户自定义设置。
 * @returns {object} 用户设置对象，无数据时返回 {}
 */
export function loadFromLocal() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') return parsed;
  } catch (e) {
    warn('settings', 'loadFromLocal parse failed: %s', e);
  }
  return {};
}

/**
 * 写入 localStorage（覆盖整个对象）。
 * 用户自定义设置的唯一持久化路径。
 * @param {object} settings 完整设置对象
 */
export function saveToLocal(settings) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(settings));
    debug('settings', 'saveLocal: %d keys', Object.keys(settings).length);
  } catch (e) {
    warn('settings', 'saveLocal failed: %s', e);
  }
}

/**
 * 从服务端拉取默认值（GET /api/settings）。
 * 返回 web.toml 提供的默认值（只读），作为 localStorage 未命中时的兜底。
 * 失败时返回空对象，调用方回退到前端 schema default。
 * @returns {Promise<object>} 服务端默认值对象，失败时返回 {}
 */
export async function loadFromServer() {
  try {
    const customAddr = (localStorage.getItem('pty_server_address') || '').trim();
    const baseUrl = customAddr ? (location.protocol === 'https:' ? 'https:' : 'http:') + '//' + customAddr : '';
    const url = baseUrl ? baseUrl + '/api/settings' : '/api/settings';
    const resp = await fetch(url, {
      method: 'GET',
      headers: authHeaders(),
      credentials: 'include',
    });
    if (!resp.ok) {
      if (resp.status === 401) {
        handleUnauthorized();
        return {};
      }
      warn('settings', 'loadFromServer: HTTP %d', resp.status);
      return {};
    }
    const data = await resp.json();
    debug('settings', 'loadFromServer: %d keys', Object.keys(data || {}).length);
    return data && typeof data === 'object' ? data : {};
  } catch (e) {
    warn('settings', 'loadFromServer network error: %s', e);
    return {};
  }
}

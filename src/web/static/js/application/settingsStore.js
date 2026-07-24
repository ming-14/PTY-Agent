/**
 * 应用层：设置 Store（用例编排）
 *
 * 协调领域 schema、基础设施存储适配器与监听者。
 * 提供 load / get / set / save / subscribe / reset 接口给表现层使用。
 *
 * 数据流：
 *   load()  → schema defaults  ← server(默认值)  ← local  （优先级：local > server > default）
 *   set()   → 内存缓存 + 通知订阅者（实时生效，如主题切换）+ 调度自动保存
 *   save()  → 写 localStorage（用户自定义唯一持久化路径，不走服务端）
 *   reset() → 恢复 schema defaults + 调度自动保存
 *
 * 自动保存：set/setMany/reset 后 debounce 400ms 自动写 localStorage，无需保存按钮。
 * 成功静默；失败时通过 ports.notification.showToast 提示。
 *
 * 服务端：GET /api/settings 仅返回 web.toml 默认值（只读兜底）；POST 为空实现备用。
 *
 * 依赖方向：应用层 → 领域层；存储适配器通过 ports.settingsStorage 注入。
 */

import { buildDefaults } from '../domain/settingsSchema.js';
import { debug, info, warn } from '../domain/logger.js';
import { ports } from './ports.js';

// 内存中的当前设置值
let _values = buildDefaults();

// 是否已加载（避免启动期订阅者读到旧默认值，且阻止 load 前的自动保存）
let _loaded = false;

// 订阅者列表（设置项变化时通知）
const _subscribers = new Set();

// 脏标记：set 后置 true，save 后清零
let _dirty = false;

// 自动保存 debounce 定时器
let _autoSaveTimer = null;
const AUTO_SAVE_DELAY = 400; // ms

/**
 * 加载设置：合并默认值 ← 服务端 ← 本地。
 * 服务端 501 时回退到本地+默认值，不阻塞 UI。
 */
export async function load() {
  const defaults = buildDefaults();
  const serverData = await ports.settingsStorage.loadFromServer();
  const localData = ports.settingsStorage.loadFromLocal();
  // 优先级：local > server > default
  _values = { ...defaults, ...serverData, ...localData };
  _loaded = true;
  _dirty = false;
  info('settings', 'load: %d keys (server=%d local=%d)',
    Object.keys(_values).length, Object.keys(serverData).length, Object.keys(localData).length);
}

/**
 * 读取单个设置项。
 * @param {string} key 设置项 key（如 'basic.theme'）
 * @returns {*} 当前值
 */
export function get(key) {
  return _values[key];
}

/**
 * 读取所有设置项的副本。
 * @returns {object}
 */
export function getAll() {
  return { ..._values };
}

/**
 * 写入单个设置项。
 * 立即通知订阅者（用于主题/字号等实时生效场景），并标记为脏 + 调度自动保存。
 * @param {string} key  设置项 key
 * @param {*}      value 新值
 */
export function set(key, value) {
  if (_values[key] === value) return;
  debug('settings', 'set: %s = %s', key, value);
  _values[key] = value;
  _dirty = true;
  _notify(key, value);
  _scheduleAutoSave();
}

/**
 * 批量写入。
 * @param {object} patch { key: value, ... }
 */
export function setMany(patch) {
  let changed = false;
  for (const k of Object.keys(patch)) {
    if (_values[k] === patch[k]) continue;
    _values[k] = patch[k];
    _notify(k, patch[k]);
    changed = true;
  }
  if (changed) {
    _dirty = true;
    _scheduleAutoSave();
  }
}

/**
 * 保存设置到 localStorage（用户自定义设置的唯一持久化路径，不走服务端）。
 * 通常由自动保存调度调用，也可手动调用（如关闭前强制持久化）。
 */
export function save() {
  ports.settingsStorage.saveToLocal(_values);
  _dirty = false;
  info('settings', 'save: local=ok (localStorage-only)');
}

/**
 * 重置为默认值。通知订阅者并调度自动保存（无需手动 save）。
 */
export function reset() {
  const defaults = buildDefaults();
  for (const k of Object.keys(_values)) {
    if (_values[k] !== defaults[k]) {
      _values[k] = defaults[k];
      _notify(k, defaults[k]);
    }
  }
  _dirty = true;
  info('settings', 'reset to defaults');
  _scheduleAutoSave();
}

/**
 * 是否有未保存的改动。
 */
export function isDirty() {
  return _dirty;
}

/**
 * 订阅设置项变化。
 * @param {(key:string, value:*) => void} cb
 * @returns {() => void} 取消订阅函数
 */
export function subscribe(cb) {
  _subscribers.add(cb);
  return () => _subscribers.delete(cb);
}

/**
 * 是否已加载完成。
 */
export function isLoaded() {
  return _loaded;
}

// ── 内部：通知订阅者 ──
function _notify(key, value) {
  for (const cb of _subscribers) {
    try { cb(key, value); } catch (e) { warn('settings', 'subscriber error: %s', e); }
  }
}

// ── 内部：调度 debounced 自动保存 ──
// 连续 set/setMany/reset 合并为一次保存，避免频繁请求。
// load() 完成前不保存（_values 仍为默认值，避免覆盖用户数据）。
function _scheduleAutoSave() {
  if (!_loaded) return;
  if (_autoSaveTimer) clearTimeout(_autoSaveTimer);
  _autoSaveTimer = setTimeout(() => {
    _autoSaveTimer = null;
    _doAutoSave();
  }, AUTO_SAVE_DELAY);
}

// ── 内部：执行自动保存 ──
// 仅写 localStorage（用户自定义设置不走服务端持久化）。成功静默；失败 toast 提示。
function _doAutoSave() {
  if (!_dirty) return;
  try {
    save();
    // 成功静默，不打扰用户
  } catch (e) {
    warn('settings', 'auto-save failed: %s', e);
    _toast('设置保存失败: ' + e, 'error');
  }
}

// ── 内部：通过 ports 通知 UI（toast） ──
function _toast(msg, level) {
  const fn = ports.notification && ports.notification.showToast;
  if (typeof fn === 'function') {
    try { fn(msg, level); } catch (e) { warn('settings', 'toast failed: %s', e); }
  }
}

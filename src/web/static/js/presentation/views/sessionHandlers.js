/**
 * 表现层：统一会话 Handler 注册机制
 *
 * 重构核心：将终端/FastScreen/VNC 三种会话类型的操作统一为 handler 接口，
 * 消除 ui.js 中的 if (sid === VNC_TAB_ID/FASTSCREEN_TAB_ID) 特判分支。
 *
 * 每种会话类型注册一个 handler，提供以下方法：
 * - switchTo(sid):  切换到此会话（显示对应 frame，隐藏其他）
 * - close(sid):     关闭会话（断开连接/取消订阅，但不移除 tab）
 * - buildTab(sid):  构建标签栏 DOM 元素
 * - restore(sid):   页面刷新后恢复会话
 * - isValid(sid):   会话是否仍然有效（用于 restoreTabs/handleSessionList 清理）
 *
 * 统一的 tab 生命周期管理（创建/移除 tabOrder 项/选择 nextTab）由 ui.js 的
 * _removeTabAndSelectNext 公共函数处理，各 handler 只负责类型特定的逻辑。
 */

import { state } from '../../domain/state.js';
import { debug, warn } from '../../domain/logger.js';

const _handlers = {};

/**
 * 注册会话类型 handler。
 * @param {string} type 会话类型（'terminal' | 'fastscreen' | 'vnc'）
 * @param {object} handler handler 对象
 */
export function registerSessionHandler(type, handler) {
  _handlers[type] = handler;
  debug('handlers', 'registered session handler: %s', type);
}

/**
 * 获取指定类型的 handler。
 * @param {string} type 会话类型
 * @returns {object|null} handler 对象，未注册时返回 null
 */
export function getHandler(type) {
  return _handlers[type] || null;
}

/**
 * 通过会话键获取对应 handler。
 * state.sessions 以 uid 为键（真实会话）或固定常量 id（VNC/FastScreen/Settings 特殊 tab）。
 * 参数 key 为状态键：真实会话传 uid，特殊 tab 传其常量 id。
 * 兼容旧调用点传展示名（sid）：真实会话展示名查不到返回 null（本就不是 handler tab），
 * 特殊 tab 的常量 id 即其状态键，可命中。
 * @param {string} key 会话状态键（uid 或特殊 tab 常量 id）
 * @returns {object|null} handler 对象
 */
export function getHandlerByKey(key) {
  const s = key ? state.sessions[key] : null;
  if (!s) {
    return null;
  }
  return getHandler(s.type);
}

/** 兼容旧名称（逻辑同 getHandlerByKey）。 */
export function getHandlerBySid(key) {
  return getHandlerByKey(key);
}

/**
 * 统一的 nextTab 选择与 tab 移除逻辑。
 * 从 tabOrder 中移除指定 sid，如果它是 activeTab 则切换到最后一个 tab。
 * 由 closeTab / removeSessionTab / 各 handler 的 close 方法调用。
 *
 * @param {string} sid 要移除的会话 id
 * @param {object} ui ui 模块（避免循环依赖，由调用方传入）
 * @returns {string|null} 切换到的下一个 tab sid，或 null（无 tab 可切）
 */
export function removeTabAndSelectNext(sid, ui) {
  const idx = state.tabOrder.indexOf(sid);
  if (idx >= 0) state.tabOrder.splice(idx, 1);

  let nextTab = null;
  if (state.activeTab === sid) {
    if (state.tabOrder.length > 0) {
      nextTab = state.tabOrder[state.tabOrder.length - 1];
      state.activeTab = nextTab;
    } else {
      state.activeTab = null;
    }
  }
  // saveTabState 由调用方或 ui 统一处理
  return nextTab;
}

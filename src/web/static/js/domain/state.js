/**
 * 领域层：全局应用状态与状态持久化接口
 *
 * state 对象是领域实体在运行时的聚合，不依赖任何外层模块。
 * loadTabState / saveTabState 提供与持久化无关的语义接口，具体存储实现由基础设施层提供。
 */

import { DEFAULT_SIDEBAR_WIDTH, DEFAULT_FONT_SIZE, MIN_FONT_SIZE, MAX_FONT_SIZE, VNC_TAB_ID, FASTSCREEN_TAB_ID, SETTINGS_TAB_ID } from './constants.js';
import { warn } from './logger.js';

// 终端尺寸模式：
// - 'default':  使用守护进程配置的尺寸（s.cols / s.rows）
// - 'adaptive': 由网页根据容器尺寸自适应计算（不低于 80x24）
// - 'fixed':    使用预设固定尺寸（fixedCols / fixedRows）
// - 'custom':   使用用户自定义尺寸（customCols / customRows）
//
// 尺寸配置按会话 uid 独立存储：每个会话维护自己的尺寸模式与自定义值，
// 不再全局共享。配置 Map 持久化到 localStorage，键为 uid。
//
// 每个会话（含 adaptive）额外保存 frameRatio（框/stage 占比，取宽高较小值），
// 用户 Ctrl+滚轮调整的是 frameRatio，字号由 frameRatio + 当前 stage 尺寸反算得到，
// 不再持久化字号本身（字号是运行时计算的派生值）。
// adaptive 模式同样保存 frameRatio：按 ratio 设 frame 尺寸再 fit() 算 cols/rows，
// 切换回去时按比例恢复框大小，不再填满 stage。
const DEFAULT_SIZE_MODE = 'default';

// frameRatio 兜底值：0.8 表示新会话默认框占 stage 内容区 80%，
// 用户可通过 Ctrl+滚轮调节并存回 localStorage；null 表示未设置（首次打开用 0.8 后写入）
export const DEFAULT_FRAME_RATIO = 0.8;

// 单个会话的默认尺寸配置（结构定义 & 回退值）
const DEFAULT_SIZE_CONFIG = {
  mode: DEFAULT_SIZE_MODE,    // 尺寸模式
  fixedCols: 80,              // 固定预设列
  fixedRows: 24,              // 固定预设行
  customCols: 120,            // 自定义列
  customRows: 30,             // 自定义行
  daemonCols: null,           // 该会话首次订阅时守护进程上报的列（"默认"模式回退用）
  daemonRows: null,           // 该会话首次订阅时守护进程上报的行
  frameRatio: DEFAULT_FRAME_RATIO, // 框/stage 占比（取宽高较小值），null=未设置
  lastUsed: 0,                // 最近一次写入时间戳，用于 LRU 淘汰
};

// localStorage 中保存的会话数量上限，避免无限增长
const MAX_STORED_SESSIONS = 50;

// 生成或读取 web 客户端 uid（localStorage 持久化，刷新不变）。
// 用于自适应锁的持有者标识：同一 client_uid 的多个标签页共享锁，
// 后端 _cleanup 时若同 uid 还有其他活跃连接则保留锁（继承）。
// 注意：与 session.uid（会话标识）是不同概念，勿混淆。
function getOrCreateClientUid() {
  try {
    let uid = localStorage.getItem('pty_client_uid');
    if (uid) return uid;
    // 生成 RFC4122 v4 UUID（crypto.randomUUID 不可用时手写兜底）
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      uid = window.crypto.randomUUID();
    } else {
      // 兜底：用 Math.random 拼 8-4-4-4-12（非严格 UUID，但足够唯一）
      uid = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
      });
    }
    localStorage.setItem('pty_client_uid', uid);
    return uid;
  } catch (_) {
    // localStorage 不可用（隐私模式）时返回临时值，本会话内保持一致
    return 'tmp-' + Math.random().toString(36).slice(2) + '-' + Date.now().toString(36);
  }
}

// 一次性清理旧版本的全局尺寸配置键，避免遗留脏数据
try {
  ['pty_size_mode', 'pty_fixed_cols', 'pty_fixed_rows', 'pty_custom_cols', 'pty_custom_rows']
    .forEach(k => localStorage.removeItem(k));
} catch (_) {}

// 清理旧版本全局字号持久化键。
// 字号原为全局共享（存于 pty_terminal_font_size 与更早的 pty_terminal_scale）；
// 现按会话存 frameRatio，字号由 frameRatio + stage 实时反算，不再持久化。
// 直接清除旧键（无法从全局字号反推每个会话的 ratio，因为 ratio 依赖 stage 尺寸）。
try {
  localStorage.removeItem('pty_terminal_font_size');
  localStorage.removeItem('pty_terminal_scale');
} catch (_) {}

// 按 uid 加载已持久化的尺寸配置 Map
function loadSessionSizeConfigs() {
  try {
    const raw = localStorage.getItem('pty_session_size_configs');
    return raw ? JSON.parse(raw) : {};
  } catch (_) {
    return {};
  }
}

export const state = {
  ws: null,
  reconnectTimer: null,
  // 会话状态表：按 uid 索引（uid 为会话唯一稳定标识；sid 仅作展示名，可复用）。
  // 同名 sid 会话先后创建时，新旧会话以不同 uid 并存，互不污染。
  sessions: {},
  // 历史会话表：按 uid 索引（sid 可重复，同名历史多条保留）。
  history: {},
  // 标签顺序：元素为会话 uid（特殊 tab 为其固定常量 id，如 __vnc__）
  tabOrder: [],
  historyVisible: false,
  // 终端实例表：按会话 uid 索引
  termInstances: {},
  // 乐观创建跟踪：元素为临时 sid（create 响应 sessionUid 到达后迁移为 uid 并清除）
  pendingCreates: new Set(),
  pendingSwitch: null,
  // 本 web 客户端的 uid（localStorage 持久化，刷新不变）。
  // WS 连接 URL 携带此 uid，后端自适应锁以 client_uid 为持有者标识。
  // 同一 client_uid 的多个标签页共享锁，刷新后锁可恢复/继承。
  clientUid: getOrCreateClientUid(),
  // 本 client_uid 持有的自适应锁会话 uid 集合。
  // 后端 AdaptiveLockService 按 client_uid 排他持有（localStorage 持久化，刷新不变）。
  // 前端通过 size_mode_ack(mode=adaptive) 确认自己已持锁，记录于此；
  // 收到 size_mode_changed adaptiveOwnerUid !== clientUid 或 adaptiveOwnerActive=false 时移除。
  // 刷新后从 ws_subscribed 响应的 adaptiveOwnerUid 恢复（若 === clientUid）。
  // 用途：区分"自己持有"与"他人持有"，前者 UI 正常，后者尺寸 UI 灰显 + 显示接管按钮。
  localAdaptiveOwnerUids: new Set(),
  // 按会话 uid 存储运行时字号（不持久化）。
  // 字号由 frameRatio + 当前 stage 尺寸反算得到，会话切换/打开时计算并写入此 Map。
  // 用 uid 而非 sid：termInstances 也是按 uid 索引，关闭会话时一并清理。
  sessionFontSizes: {},
  sidebarCollapsed: localStorage.getItem('pty_sidebar_collapsed') === 'true',
  sidebarWidth: parseInt(localStorage.getItem('pty_sidebar_width') || String(DEFAULT_SIDEBAR_WIDTH), 10),
  activeTab: localStorage.getItem('pty_active_tab') || null,
  contextMenuTarget: null,
  contextMenuContext: null,
  confirmOkCallback: null,
  restartTargetSid: null,
  isResizingSidebar: false,
  restoreState: { pending: false, gotList: false, gotHistory: false },
  closedSessionToastSet: new Set(),
  availableShells: (() => {
    try {
      const raw = localStorage.getItem('pty_available_shells');
      return raw ? JSON.parse(raw) : {};
    } catch (_) {
      return {};
    }
  })(),
  // 按 uid 存储的尺寸配置 Map：{ [uid]: { mode, fixedCols, fixedRows, customCols, customRows, daemonCols, daemonRows, lastUsed } }
  sessionSizeConfigs: loadSessionSizeConfigs(),
  // 尺寸选择器下拉是否可见（UI 状态，不持久化）
  sizeSelectorVisible: false,
  // VNC 远程桌面状态
  vnc: {
    disabled: false,        // 后端 ENABLE_VNC=False 时为 true，前端隐藏入口
    winvncAvailable: false, // winvnc.exe 是否存在
    running: false,         // VNC 进程（winvnc.exe）是否运行中
    starting: false,        // 正在启动（发送 vnc_start 后等待 vnc_started）
    stopping: false,        // 正在停止（发送 vnc_stop 后等待 vnc_stopped）
    vncPort: null,          // VNC 服务端口（守护进程 /vnc/websockify 代理到此端口）
    password: null,         // VNC 连接密码
    vncPid: null,
    error: null,            // 最近一次错误消息
  },
  // FastScreen 屏幕查看状态
  fastscreen: {
    disabled: false,        // 后端 ENABLE_FASTSCREEN=False 时为 true，前端隐藏入口
    available: false,       // fastscreen.dll 是否加载成功
    activeSessions: 0,      // 当前活跃捕获会话数（多客户端共享）
    connected: false,       // 前端流是否已连接（_activeStream 非空），用于 autohide 判断
    error: null,            // 最近一次错误消息
    // 当前查看目标（由前端工具条选择，切换时重建流连接）
    targetType: 'monitor',  // 'monitor' | 'window'
    targetId: 0,            // monitor id 或 window hwnd
    method: 'auto',         // 捕获方法：auto/dxgi/wgc/bitblt
    fps: 30,                // 帧率
    quality: 0.8,           // MJPEG 质量 / H264 CRF 映射
    bitrate: 2000000,       // H264 码率
    gopSize: 30,            // H264 GOP 大小
    // 流格式：'mjpeg' | 'mse' | 'webcodecs'
    streamFormat: 'mse',
    // 已 enumerated 的目标列表（由 fs_targets 消息填充）
    monitors: [],
    windows: [],
    // 鼠标增强光标定位器状态（服务端单例，多客户端共享）
    cursorLocatorRunning: false,   // 光标定位器是否运行中
    cursorLocatorAvailable: false, // 光标定位器是否可用（仅 Windows）
  },
};

// ── 统一会话模型：为 FastScreen / VNC 这两个单例 tab 在 state.sessions 中建立条目 ──
// 这些条目在初始化时创建，type 字段用于 sessionHandlers.js 分发对应 handler；
// 它们不参与 session_list / history 清理（清理逻辑通过 handler.isValid 判断有效性）。
state.sessions[FASTSCREEN_TAB_ID] = {
  id: FASTSCREEN_TAB_ID,
  type: 'fastscreen',       // 进入 tab 时由 openFastScreenTab 设为 true
  running: false,
  title: '屏幕查看',
};
state.sessions[VNC_TAB_ID] = {
  id: VNC_TAB_ID,
  type: 'vnc',
  running: false,
  title: '远程桌面',
};

// 设置 tab 同样作为单例条目，type='settings' 用于 sessionHandlers 分发到 settings handler
state.sessions[SETTINGS_TAB_ID] = {
  id: SETTINGS_TAB_ID,
  type: 'settings',
  running: false,
  title: '设置',
};

/**
 * 获取指定 uid 的尺寸配置。不存在时返回默认配置副本。
 * @param {string} uid 会话 uid
 * @returns {{mode:string, fixedCols:number, fixedRows:number, customCols:number, customRows:number, daemonCols:number|null, daemonRows:number|null, lastUsed:number}}
 */
export function getSessionSizeConfig(uid) {
  if (!uid) return { ...DEFAULT_SIZE_CONFIG };
  const cached = state.sessionSizeConfigs[uid];
  if (cached) return { ...DEFAULT_SIZE_CONFIG, ...cached };
  return { ...DEFAULT_SIZE_CONFIG };
}

/**
 * 通过会话 uid 查找其尺寸配置（state.sessions 按 uid 索引）。
 * 若 uid 不存在或会话未上报 uid，返回默认配置。
 * @param {string} uid 会话 uid（或特殊 tab 的固定 id）
 */
export function getSessionSizeConfigByUid(uid) {
  const s = uid ? state.sessions[uid] : null;
  return getSessionSizeConfig(s && s.uid);
}

/**
 * 统一会话查找：按会话 uid（或特殊 tab 常量 id，如 __vnc__）取会话对象。
 * state.sessions 以 uid 为键；特殊 tab 以其固定常量 id 为键。
 * @param {string} key 会话 uid 或特殊 tab 常量 id
 * @returns {object|null}
 */
export function getSessionByKey(key) {
  if (!key) return null;
  return state.sessions[key] || null;
}

/**
 * 按展示名（sid）反查活跃会话 uid。
 * sid 可复用（同名会话先后创建），返回最新活跃会话的 uid；无匹配返回 null。
 * @param {string} sid 展示名（用户自定义会话名）
 * @returns {string|null}
 */
export function getUidBySid(sid) {
  if (!sid) return null;
  for (const s of Object.values(state.sessions)) {
    if (s && s.id === sid && !s.history) return s.uid;
  }
  return null;
}

/**
 * 按展示名（sid）反查历史会话 uid（同名历史多条时返回最新一条）。
 * @param {string} sid 展示名
 * @returns {string|null}
 */
export function getHistoryUidBySid(sid) {
  if (!sid) return null;
  let found = null;
  for (const h of Object.values(state.history)) {
    if (h && h.id === sid) {
      found = h.uid || found;
    }
  }
  return found;
}

/**
 * 入站 WS 消息路由键解析：优先 sessionUid（后端权威路由键），
 * 其次 msg.uid（session_detail/history_detail/session_created 等携带的权威 uid），
 * 否则按 sessionId/session_id/id（sid）反查 uid；历史会话无 uid 时返回 null。
 * 注意：sid 反查可能命中同名活跃会话（历史详情场景），故 uid/sessionUid 优先。
 * @param {object} msg 入站消息
 * @returns {string|null} 会话 uid；特殊 tab 消息（无 session 字段）返回 null
 */
export function resolveMsgUid(msg) {
  if (!msg) return null;
  const uid = msg.sessionUid || msg.uid;
  if (uid) return uid;
  const sid = msg.sessionId || msg.session_id || msg.id;
  if (!sid) return null;
  return getUidBySid(sid) || getHistoryUidBySid(sid) || null;
}

/**
 * 更新指定 uid 的尺寸配置（合并写入）并持久化。
 * 同时更新 lastUsed 时间戳，并在条目数超限时按 LRU 淘汰最旧条目。
 * @param {string} uid 会话 uid
 * @param {object} patch 要合并的字段
 */
export function setSessionSizeConfig(uid, patch) {
  if (!uid) return;
  const cur = getSessionSizeConfig(uid);
  const next = { ...cur, ...patch, lastUsed: Date.now() };
  state.sessionSizeConfigs[uid] = next;
  // LRU 淘汰：当条目数超过上限时，删除最久未使用的条目
  const uids = Object.keys(state.sessionSizeConfigs);
  if (uids.length > MAX_STORED_SESSIONS) {
    uids
      .sort((a, b) => (state.sessionSizeConfigs[a].lastUsed || 0) - (state.sessionSizeConfigs[b].lastUsed || 0))
      .slice(0, uids.length - MAX_STORED_SESSIONS)
      .forEach(oldUid => { delete state.sessionSizeConfigs[oldUid]; });
  }
  try {
    localStorage.setItem('pty_session_size_configs', JSON.stringify(state.sessionSizeConfigs));
  } catch (_) {}
}

/**
 * 设置当前活动会话的尺寸模式。
 * 操作目标为 state.activeTab 对应会话的 uid。
 */
export function setSizeMode(mode) {
  const sid = state.activeTab;
  const s = sid ? state.sessions[sid] : null;
  if (!s) {
    warn('size', 'setSizeMode(%s) skipped: no active session sid=%s', mode, sid);
    return;
  }
  if (!s.uid) {
    warn('size', 'setSizeMode(%s) skipped: sid=%s has no uid (session_created not yet received?)', mode, sid);
    return;
  }
  setSessionSizeConfig(s.uid, { mode });
}

/**
 * 设置当前活动会话的固定预设尺寸（不改变模式，由调用方随后调用 setSizeMode）。
 */
export function setFixedSize(cols, rows) {
  const sid = state.activeTab;
  const s = sid ? state.sessions[sid] : null;
  if (!s || !s.uid) {
    warn('size', 'setFixedSize(%dx%d) skipped: sid=%s uid=%s', cols, rows, sid, s && s.uid);
    return;
  }
  setSessionSizeConfig(s.uid, { fixedCols: cols, fixedRows: rows });
}

/**
 * 设置当前活动会话的自定义尺寸（不改变模式，由调用方随后调用 setSizeMode）。
 */
export function setCustomSize(cols, rows) {
  const sid = state.activeTab;
  const s = sid ? state.sessions[sid] : null;
  if (!s || !s.uid) {
    warn('size', 'setCustomSize(%dx%d) skipped: sid=%s uid=%s', cols, rows, sid, s && s.uid);
    return;
  }
  setSessionSizeConfig(s.uid, { customCols: cols, customRows: rows });
}

// ── 按会话的运行时字号 & 持久化 frameRatio 访问 ──

/**
 * 获取指定会话（按 uid）的运行时字号。
 * 字号不持久化，由 frameRatio + stage 尺寸反算后写入 state.sessionFontSizes。
 * 未设置时返回 DEFAULT_FONT_SIZE（供 ensureTerminal 初始化使用）。
 * @param {string} uid 会话 uid
 * @returns {number} 字号（已 clamp 到 [MIN_FONT_SIZE, MAX_FONT_SIZE]）
 */
export function getSessionFontSize(uid) {
  if (!uid) return DEFAULT_FONT_SIZE;
  const v = state.sessionFontSizes[uid];
  if (Number.isFinite(v) && v > 0) {
    return Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, Math.round(v)));
  }
  return DEFAULT_FONT_SIZE;
}

/**
 * 设置指定会话（按 uid）的运行时字号（不持久化，仅写入内存 Map）。
 * 调用方负责先反算好字号再写入；本函数只做 clamp。
 * @param {string} uid 会话 uid
 * @param {number} size 字号
 */
export function setSessionFontSize(uid, size) {
  if (!uid) return;
  const v = Math.round(size);
  if (!Number.isFinite(v) || v <= 0) return;
  state.sessionFontSizes[uid] = Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, v));
}

/**
 * 清除指定会话（按 uid）的运行时字号（会话关闭时调用，避免内存泄漏）。
 * @param {string} uid 会话 uid
 */
export function clearSessionFontSize(uid) {
  if (uid) delete state.sessionFontSizes[uid];
}

/**
 * 获取指定 uid 的 frameRatio（框/stage 占比，取宽高较小值）。
 * 所有模式（含 adaptive）都参与 ratio 记忆。未设置时返回 null。
 * @param {string} uid 会话 uid
 * @returns {number|null} ratio (0, 1.0]，null 表示未设置
 */
export function getSessionFrameRatio(uid) {
  if (!uid) return null;
  const cfg = getSessionSizeConfig(uid);
  const r = cfg.frameRatio;
  return (Number.isFinite(r) && r > 0) ? r : null;
}

/**
 * 获取当前活动会话的 frameRatio。
 * @returns {number|null}
 */
export function getActiveSessionFrameRatio() {
  const sid = state.activeTab;
  const s = sid ? state.sessions[sid] : null;
  return getSessionFrameRatio(s && s.uid);
}

/**
 * 设置当前活动会话的 frameRatio 并持久化。
 * 所有模式（含 adaptive）都保存 ratio。
 * @param {number} ratio (0, 1.0]
 */
export function setActiveSessionFrameRatio(ratio) {
  const sid = state.activeTab;
  const s = sid ? state.sessions[sid] : null;
  if (!s || !s.uid) {
    warn('size', 'setActiveSessionFrameRatio(%s) skipped: sid=%s uid=%s', ratio, sid, s && s.uid);
    return;
  }
  const clamped = Math.max(0.1, Math.min(1.0, ratio));
  setSessionSizeConfig(s.uid, { frameRatio: clamped });
}

export function loadTabState() {
  try {
    const raw = localStorage.getItem('pty_tab_order');
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) state.tabOrder = parsed;
    }
  } catch (e) {
    state.tabOrder = [];
  }
  const savedActive = localStorage.getItem('pty_active_tab');
  if (savedActive && state.tabOrder.includes(savedActive)) {
    state.activeTab = savedActive;
  } else if (state.tabOrder.length > 0) {
    state.activeTab = state.tabOrder[state.tabOrder.length - 1];
  } else {
    state.activeTab = null;
  }
}

// ── 自适应锁本地持有者状态 ──

/**
 * 判断本 client_uid 是否持有指定会话（按 uid）的自适应锁。
 * 优先检查 localAdaptiveOwnerUids（本端发起 set_size_mode 后的乐观标记），
 * 其次检查 s.adaptiveOwnerUid === state.clientUid（后端权威状态，刷新后从 ws_subscribed 恢复）。
 * @param {string} uid 会话 uid
 * @returns {boolean}
 */
export function isLocalAdaptiveOwner(uid) {
  if (!uid) return false;
  if (state.localAdaptiveOwnerUids.has(uid)) return true;
  // 刷新后 localAdaptiveOwnerUids 为空，但后端锁仍属于本 client_uid，
  // 从 ws_subscribed / size_mode_changed 同步的 adaptiveOwnerUid 判断
  const s = state.sessions[uid];
  return !!(s && s.adaptiveOwnerUid && s.adaptiveOwnerUid === state.clientUid);
}

/**
 * 设置本 client_uid 对指定会话（按 uid）的自适应锁持有状态（乐观标记）。
 * @param {string} uid 会话 uid
 * @param {boolean} on true=持有, false=释放
 */
export function setLocalAdaptiveOwner(uid, on) {
  if (!uid) return;
  if (on) {
    state.localAdaptiveOwnerUids.add(uid);
  } else {
    state.localAdaptiveOwnerUids.delete(uid);
  }
}

/**
 * 判断指定会话（按 uid）的尺寸调整 UI 是否应被灰显（被其他 client_uid 持有自适应锁）。
 * 灰显条件：adaptiveOwnerActive=true 且本端不是持有者。
 * @param {string} uid 会话 uid
 * @returns {boolean}
 */
export function isSizeUILocked(uid) {
  const s = uid ? state.sessions[uid] : null;
  if (!s) return false;
  return !!s.adaptiveOwnerActive && !isLocalAdaptiveOwner(uid);
}

export function saveTabState() {
  localStorage.setItem('pty_tab_order', JSON.stringify(state.tabOrder));
  localStorage.setItem('pty_active_tab', state.activeTab || '');
}

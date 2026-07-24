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
// 自 v2 起改为按会话 uid 独立存储：每个会话维护自己的尺寸模式与自定义值，
// 不再全局共享。配置 Map 持久化到 localStorage，键为 uid。
//
// v9 起：每个会话（含 adaptive）额外保存 frameRatio（框/stage 占比，取宽高较小值），
// 用户 Ctrl+滚轮调整的是 frameRatio，字号由 frameRatio + 当前 stage 尺寸反算得到，
// 不再持久化字号本身（字号是运行时计算的派生值）。
// adaptive 模式同样保存 frameRatio：按 ratio 设 frame 尺寸再 fit() 算 cols/rows，
// 切换回去时按比例恢复框大小，不再填满 stage。
const DEFAULT_SIZE_MODE = 'default';

// frameRatio 兜底值：1.0 表示撑满 stage；null 表示未设置（首次打开用默认字号反算后写入）
const DEFAULT_FRAME_RATIO = null;

// 单个会话的默认尺寸配置（结构定义 & 回退值）
const DEFAULT_SIZE_CONFIG = {
  mode: DEFAULT_SIZE_MODE,    // 尺寸模式
  fixedCols: 80,              // 固定预设列
  fixedRows: 24,              // 固定预设行
  customCols: 120,            // 自定义列
  customRows: 30,             // 自定义行
  daemonCols: null,           // 该会话首次订阅时守护进程上报的列（"默认"模式回退用）
  daemonRows: null,           // 该会话首次订阅时守护进程上报的行
  frameRatio: DEFAULT_FRAME_RATIO, // v9: 框/stage 占比（取宽高较小值），null=未设置
  lastUsed: 0,                // 最近一次写入时间戳，用于 LRU 淘汰
};

// localStorage 中保存的会话数量上限，避免无限增长
const MAX_STORED_SESSIONS = 50;

// v3: 生成或读取 web 客户端 uid（localStorage 持久化，刷新不变）。
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

// 一次性清理旧版本（v1）的全局尺寸配置键，避免遗留脏数据
try {
  ['pty_size_mode', 'pty_fixed_cols', 'pty_fixed_rows', 'pty_custom_cols', 'pty_custom_rows']
    .forEach(k => localStorage.removeItem(k));
} catch (_) {}

// v9: 清理旧版本（v3~v8）的全局字号持久化键。
// 旧版本字号全局共享存 pty_terminal_font_size（更早还有 pty_terminal_scale）；
// v9 改为按会话存 frameRatio，字号由 frameRatio + stage 实时反算，不再持久化。
// 迁移策略：直接清除旧键（无法从全局字号反推每个会话的 ratio，因为 ratio 依赖 stage 尺寸）。
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
  sessions: {},
  history: {},
  tabOrder: [],
  historyVisible: false,
  termInstances: {},
  pendingCreates: new Set(),
  pendingSwitch: null,
  // v3: 本 web 客户端的 uid（localStorage 持久化，刷新不变）。
  // WS 连接 URL 携带此 uid，后端自适应锁以 client_uid 为持有者标识。
  // 同一 client_uid 的多个标签页共享锁，刷新后锁可恢复/继承。
  clientUid: getOrCreateClientUid(),
  // 问题2/v3：本 client_uid 持有的自适应锁会话 sid 集合。
  // 后端 AdaptiveLockService 按 client_uid 排他持有（localStorage 持久化，刷新不变）。
  // 前端通过 size_mode_ack(mode=adaptive) 确认自己已持锁，记录于此；
  // 收到 size_mode_changed adaptiveOwnerUid !== clientUid 或 adaptiveOwnerActive=false 时移除。
  // 刷新后从 ws_subscribed 响应的 adaptiveOwnerUid 恢复（若 === clientUid）。
  // 用途：区分"自己持有"与"他人持有"，前者 UI 正常，后者尺寸 UI 灰显 + 显示接管按钮。
  localAdaptiveOwnerSids: new Set(),
  // v9: 按会话 sid 存储运行时字号（不持久化）。
  // 字号由 frameRatio + 当前 stage 尺寸反算得到，会话切换/打开时计算并写入此 Map。
  // 用 sid 而非 uid：termInstances 也是按 sid 索引，关闭会话时一并清理。
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
 * 通过会话 sid 查找其 uid，再返回对应的尺寸配置。
 * 若 sid 不存在或 uid 未上报，返回默认配置。
 * @param {string} sid 会话 id
 */
export function getSessionSizeConfigBySid(sid) {
  const s = sid ? state.sessions[sid] : null;
  return getSessionSizeConfig(s && s.uid);
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

// ── v9: 按会话的运行时字号 & 持久化 frameRatio 访问 ──

/**
 * 获取指定会话的运行时字号。
 * 字号不持久化，由 frameRatio + stage 尺寸反算后写入 state.sessionFontSizes。
 * 未设置时返回 DEFAULT_FONT_SIZE（供 ensureTerminal 初始化使用）。
 * @param {string} sid 会话 id
 * @returns {number} 字号（已 clamp 到 [MIN_FONT_SIZE, MAX_FONT_SIZE]）
 */
export function getSessionFontSize(sid) {
  if (!sid) return DEFAULT_FONT_SIZE;
  const v = state.sessionFontSizes[sid];
  if (Number.isFinite(v) && v > 0) {
    return Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, Math.round(v)));
  }
  return DEFAULT_FONT_SIZE;
}

/**
 * 设置指定会话的运行时字号（不持久化，仅写入内存 Map）。
 * 调用方负责先反算好字号再写入；本函数只做 clamp。
 * @param {string} sid 会话 id
 * @param {number} size 字号
 */
export function setSessionFontSize(sid, size) {
  if (!sid) return;
  const v = Math.round(size);
  if (!Number.isFinite(v) || v <= 0) return;
  state.sessionFontSizes[sid] = Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, v));
}

/**
 * 清除指定会话的运行时字号（会话关闭时调用，避免内存泄漏）。
 * @param {string} sid 会话 id
 */
export function clearSessionFontSize(sid) {
  if (sid) delete state.sessionFontSizes[sid];
}

/**
 * 获取指定 uid 的 frameRatio（框/stage 占比，取宽高较小值）。
 * v9: 所有模式（含 adaptive）都参与 ratio 记忆。未设置时返回 null。
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
 * v9: 所有模式（含 adaptive）都保存 ratio。
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

// ── 问题2/v3：自适应锁本地持有者状态 ──

/**
 * 判断本 client_uid 是否持有指定会话的自适应锁。
 * v3 改造：优先检查 localAdaptiveOwnerSids（本端发起 set_size_mode 后的乐观标记），
 * 其次检查 s.adaptiveOwnerUid === state.clientUid（后端权威状态，刷新后从 ws_subscribed 恢复）。
 * @param {string} sid 会话 id
 * @returns {boolean}
 */
export function isLocalAdaptiveOwner(sid) {
  if (!sid) return false;
  if (state.localAdaptiveOwnerSids.has(sid)) return true;
  // v3: 刷新后 localAdaptiveOwnerSids 为空，但后端锁仍属于本 client_uid，
  // 从 ws_subscribed / size_mode_changed 同步的 adaptiveOwnerUid 判断
  const s = state.sessions[sid];
  return !!(s && s.adaptiveOwnerUid && s.adaptiveOwnerUid === state.clientUid);
}

/**
 * 设置本 client_uid 对指定会话的自适应锁持有状态（乐观标记）。
 * @param {string} sid 会话 id
 * @param {boolean} on true=持有, false=释放
 */
export function setLocalAdaptiveOwner(sid, on) {
  if (!sid) return;
  if (on) {
    state.localAdaptiveOwnerSids.add(sid);
  } else {
    state.localAdaptiveOwnerSids.delete(sid);
  }
}

/**
 * 判断指定会话的尺寸调整 UI 是否应被灰显（被其他 client_uid 持有自适应锁）。
 * 灰显条件：adaptiveOwnerActive=true 且本端不是持有者。
 * @param {string} sid 会话 id
 * @returns {boolean}
 */
export function isSizeUILocked(sid) {
  const s = sid ? state.sessions[sid] : null;
  if (!s) return false;
  return !!s.adaptiveOwnerActive && !isLocalAdaptiveOwner(sid);
}

export function saveTabState() {
  localStorage.setItem('pty_tab_order', JSON.stringify(state.tabOrder));
  localStorage.setItem('pty_active_tab', state.activeTab || '');
}

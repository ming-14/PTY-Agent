/**
 * 领域层：前端日志系统
 *
 * 与守护进程类似的日志等级。日志是跨领域的横切关注点，
 * 但实现不依赖任何外层模块，因此置于领域层。
 *
 * 日志等级：
 *   DEBUG (0) — 详细操作日志：按键、鼠标、SGR、WebSocket 消息
 *   INFO  (1) — 重要事件：会话创建、标签切换、连接状态
 *   WARN  (2) — 潜在问题：丢弃消息、操作失败
 *   ERROR (3) — 错误
 *   NONE  (4) — 关闭日志
 *
 * 使用方式：
 *   import { debug, info, warn, error } from '../domain/logger.js';
 *   debug('terminal', 'key event:', e.key);
 *
 * 两个独立等级（解耦控制台输出与日志视窗采集）：
 *   - 输出等级 (_level)：控制是否调用 console.*，默认 NONE
 *   - 采集等级 (_captureLevel)：控制是否进入环形缓冲区供日志视窗订阅，默认 DEBUG
 *   两者独立，因此即便控制台关闭日志，悬浮日志视窗仍能展示运行日志。
 *
 * 日志视窗接入：
 *   - subscribe(cb)：注册订阅者，每条入缓冲区的日志都会回调一次
 *   - getEntries()：读取当前缓冲区快照
 *   - clearBuffer()：清空缓冲区
 *
 * 切换日志等级（输出等级）：
 *   - Ctrl+Shift+D 切换 DEBUG / NONE（由 events.js 写回 settingsStore.developer.logLevel）
 *   - localStorage.setItem('pty_log_level', '0') 持久化设置
 *   - window.__logLevel__ = 0 临时设置
 */

export const LEVELS = {
  DEBUG: 0,
  INFO: 1,
  WARN: 2,
  ERROR: 3,
  NONE: 4,
};

const LEVEL_NAMES = ['DEBUG', 'INFO', 'WARN', 'ERROR', 'NONE'];

const TAG_COLORS = {
  terminal: '#4CAF50',
  mouse: '#FF9800',
  key: '#2196F3',
  ws: '#9C27B0',
  ui: '#00BCD4',
  session: '#FF5722',
  scroll: '#795548',
  paste: '#E91E63',
  cursor: '#9CCC65',
  touch: '#BA68C8',
  settings: '#FFD54F',
  app: '#90A4AE',
  fs: '#26C6DA',
  vnc: '#7E57C2',
  console: '#607D8B',
  default: '#888',
};

// 各等级展示色（用于日志视窗左侧色条 + level 标签）
const LEVEL_COLORS = {
  0: '#888',   // DEBUG
  1: '#08f',   // INFO
  2: '#f80',   // WARN
  3: '#f00',   // ERROR
};

// ── 保存原始 console 引用（供 patch 使用，避免循环） ──
const _origConsole = {
  log: console.log.bind(console),
  debug: (console.debug && console.debug.bind(console)) || console.log.bind(console),
  info: (console.info && console.info.bind(console)) || console.log.bind(console),
  warn: console.warn.bind(console),
  error: console.error.bind(console),
};

// ── 输出等级（控制 console.* 是否调用） ──
let _level = LEVELS.NONE;

// ── 采集等级（控制是否进入环形缓冲区供视窗订阅） ──
// 独立于输出等级：即便 _level=NONE（控制台静默），_captureLevel=DEBUG 时视窗仍可见。
let _captureLevel = LEVELS.DEBUG;

// ── 环形缓冲区：固定容量，超出丢弃最旧 ──
const DEFAULT_BUFFER_CAPACITY = 1000;
let _bufferCapacity = DEFAULT_BUFFER_CAPACITY;
let _buffer = [];

// ── 订阅者集合：每条入缓冲区的日志回调一次 ──
const _subscribers = new Set();

function loadLevel() {
  try {
    const saved = localStorage.getItem('pty_log_level');
    if (saved !== null) {
      _level = parseInt(saved, 10);
      if (isNaN(_level)) _level = LEVELS.NONE;
    }
  } catch (e) {}
}
loadLevel();

// ── 输出等级 setter/getter ──
export function setLogLevel(level) {
  _level = level;
  try {
    localStorage.setItem('pty_log_level', String(level));
  } catch (e) {}
}

export function getLogLevel() {
  return _level;
}

export function getLogLevelName() {
  return LEVEL_NAMES[_level] || 'NONE';
}

export function isDebugEnabled() {
  return _level <= LEVELS.DEBUG;
}

export function toggleDebug() {
  if (_level <= LEVELS.DEBUG) {
    setLogLevel(LEVELS.NONE);
  } else {
    setLogLevel(LEVELS.DEBUG);
  }
  return _level;
}

// ── 采集等级 setter/getter（视窗采集阈值，独立于输出等级） ──
export function setCaptureLevel(level) {
  _captureLevel = level;
}

export function getCaptureLevel() {
  return _captureLevel;
}

// ── 缓冲区容量控制 ──
export function setBufferSize(capacity) {
  if (!Number.isFinite(capacity) || capacity < 1) return;
  _bufferCapacity = Math.floor(capacity);
  // 容量收缩时立即裁剪最旧条目
  if (_buffer.length > _bufferCapacity) {
    _buffer.splice(0, _buffer.length - _bufferCapacity);
  }
}

export function getBufferSize() {
  return _buffer.length;
}

export function getBufferCapacity() {
  return _bufferCapacity;
}

/**
 * 读取缓冲区快照（返回新数组，调用方可安全遍历/过滤）。
 * @returns {Array<LogEntry>}
 */
export function getEntries() {
  return _buffer.slice();
}

/**
 * 清空缓冲区（不清空控制台）。通知订阅者 entry=null 表示「清空」事件。
 */
export function clearBuffer() {
  _buffer.length = 0;
  _notify({ type: 'clear' });
}

/**
 * 订阅日志事件。订阅期间新入缓冲区的每条日志都会回调 cb(entry)；
 * 缓冲区清空时回调 cb({type:'clear'})。
 * @param {(entry: LogEntry|{type:string}) => void} cb
 * @returns {() => void} 取消订阅函数
 */
export function subscribe(cb) {
  _subscribers.add(cb);
  return () => _subscribers.delete(cb);
}

export function unsubscribe(cb) {
  _subscribers.delete(cb);
}

// ── 内部：通知所有订阅者 ──
function _notify(entry) {
  for (const cb of _subscribers) {
    try { cb(entry); } catch (_) {}
  }
}

// ── 内部：args 序列化为可读字符串（避免 [object Object]） ──
// 如果第一个参数是字符串且包含 printf 占位符（%s / %d / %o / %f / %j / %%），
// 则按顺序替换占位符，否则简单空格拼接。
// 这样日志视窗展示的内容与 console.log 原生格式一致。
function _stringifyArgs(args) {
  if (args.length === 0) return '';
  // 检测 printf 风格：首个参数是字符串且包含占位符
  if (args.length >= 2 && typeof args[0] === 'string' && /%[sdifoOj%]/.test(args[0])) {
    let fmt = args[0];
    let argIdx = 1;
    fmt = fmt.replace(/%([sdifoOj%])/g, (match, spec) => {
      if (spec === '%') return '%';
      if (argIdx >= args.length) return match;
      const val = args[argIdx++];
      switch (spec) {
        case 's': return String(val);
        case 'd':
        case 'i': {
          const n = Number(val);
          return Number.isFinite(n) ? String(Math.floor(n)) : String(val);
        }
        case 'f': {
          const n = Number(val);
          return Number.isFinite(n) ? String(n) : String(val);
        }
        case 'o':
        case 'O':
        case 'j': return _stringify(val);
        default: return match;
      }
    });
    // 如果还有剩余参数未消费，附加在后面
    while (argIdx < args.length) {
      fmt += ' ' + _stringify(args[argIdx++]);
    }
    return fmt;
  }
  // 无 printf 格式串，原有空格拼接
  const parts = [];
  for (let i = 0; i < args.length; i++) {
    parts.push(_stringify(args[i]));
  }
  return parts.join(' ');
}

/**
 * 扫描 args 中是否有 Error 实例，提取完整 stack。
 * @returns {{ hasStack: boolean, stack: string }}
 */
function _extractStackFromArgs(args) {
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a instanceof Error && a.stack) {
      return { hasStack: true, stack: a.name + ': ' + a.message + '\n' + a.stack.split('\n').slice(1).join('\n') };
    }
  }
  return { hasStack: false, stack: '' };
}

function _stringify(a) {
  if (a === null) return 'null';
  if (a === undefined) return 'undefined';
  const t = typeof a;
  if (t === 'string') return a;
  if (t === 'number' || t === 'boolean' || t === 'bigint') return String(a);
  if (t === 'symbol') return a.toString();
  if (t === 'function') {
    const name = a.name || '<anonymous>';
    return '[Function ' + name + ']';
  }
  if (a instanceof Error) {
    return a.name + ': ' + a.message + (a.stack ? '\n' + a.stack.split('\n').slice(1, 4).join('\n') : '');
  }
  if (t === 'object') {
    // 精简格式化对象/数组：尝试 JSON，循环引用或超大时回退
    try {
      const json = JSON.stringify(a, (key, val) => {
        if (typeof val === 'function') return '[Function]';
        if (typeof val === 'bigint') return val.toString() + 'n';
        return val;
      }, 0);
      if (json !== undefined && json.length <= 2000) return json;
      if (json !== undefined) return json.slice(0, 2000) + '…';
    } catch (_) {}
    try { return String(a); } catch (_) { return '[Unserializable]'; }
  }
  return String(a);
}

// ── 内部：时间戳格式化为 HH:MM:SS.mmm ──
function _formatTs(ts) {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  const ms = String(d.getMilliseconds()).padStart(3, '0');
  return hh + ':' + mm + ':' + ss + '.' + ms;
}

// ── 内部：构造日志条目对象（不包含 I/O 逻辑） ──
function _buildEntry(level, tag, args, captureCallStack) {
  const now = Date.now();
  const tsStr = _formatTs(now);
  const text = _stringifyArgs(args);
  const { hasStack, stack } = _extractStackFromArgs(args);

  let callStack = stack;
  let hasCallStack = hasStack;
  if (!hasStack && captureCallStack && level >= LEVELS.WARN) {
    try {
      const err = new Error();
      const lines = (err.stack || '').split('\n');
      // 跳过前 3 行：Error / _buildEntry / (caller)
      const callerLines = lines.slice(3).filter(l => l.trim()).join('\n');
      if (callerLines) {
        callStack = 'Call stack:\n' + callerLines;
        hasCallStack = true;
      }
    } catch (_) {}
  }

  return {
    ts: now,
    tsStr,
    level,
    levelName: LEVEL_NAMES[level] || '?',
    tag: tag || 'default',
    tagColor: TAG_COLORS[tag] || TAG_COLORS.default,
    levelColor: LEVEL_COLORS[level] || '#888',
    text,
    hasStack: hasCallStack,
    stack: callStack,
  };
}

// ── 核心：写一条日志 ──
// 1) 输出等级过滤：level > _level 时仍可能进缓冲区（由 _captureLevel 决定）
// 2) 采集等级过滤：level > _captureLevel 时既不输出也不入缓冲
// 3) 否则：构造 entry → 推入环形缓冲区 → 通知订阅者；同时按输出等级决定是否调用 console.*
function _log(level, tag, args, color, consoleFn) {
  const captureOk = level >= _captureLevel;  // 采集范围（视窗可见）
  const outputOk = level >= _level;          // 控制台输出范围
  if (!captureOk && !outputOk) return;

  const entry = _buildEntry(level, tag, args, true);

  // 入环形缓冲区（仅采集范围内）
  if (captureOk) {
    if (_buffer.length >= _bufferCapacity) _buffer.shift();
    _buffer.push(entry);
    _notify(entry);
  }

  // 控制台输出（保留原有彩色前缀格式）
  if (outputOk) {
    const tagColor = TAG_COLORS[tag] || TAG_COLORS.default;
    const prefix = `%c[${LEVEL_NAMES[level]}]%c[${tag}]%c`;
    const levelStyle = `color:${color};font-weight:bold`;
    const tagStyle = `color:${tagColor};font-weight:bold`;
    const resetStyle = 'color:inherit';
    consoleFn(prefix, levelStyle, tagStyle, resetStyle, ...args);
  }
}

export function debug(tag, ...args) {
  _log(LEVELS.DEBUG, tag, args, '#888', _origConsole.log);
}

export function info(tag, ...args) {
  _log(LEVELS.INFO, tag, args, '#08f', _origConsole.log);
}

export function warn(tag, ...args) {
  _log(LEVELS.WARN, tag, args, '#f80', _origConsole.warn);
}

export function error(tag, ...args) {
  _log(LEVELS.ERROR, tag, args, '#f00', _origConsole.error);
}

// ── 捕获第三方 console 调用，推入日志缓冲区 ──
// 由 patched console 方法调用，仅推入缓冲区，不再调 console（避免循环）。
function _captureConsoleEntry(level, tag, args) {
  if (level < _captureLevel) return;
  const entry = _buildEntry(level, tag, args, false);
  if (_buffer.length >= _bufferCapacity) _buffer.shift();
  _buffer.push(entry);
  _notify(entry);
}

// ── Patch 原生 console 方法，使所有 console.log/warn/error 等被日志视窗捕获 ──
export function patchConsole() {
  console.log = function(...args) {
    _origConsole.log.apply(console, args);
    _captureConsoleEntry(LEVELS.INFO, 'console', args);
  };
  console.debug = function(...args) {
    _origConsole.debug.apply(console, args);
    _captureConsoleEntry(LEVELS.DEBUG, 'console', args);
  };
  console.info = function(...args) {
    _origConsole.info.apply(console, args);
    _captureConsoleEntry(LEVELS.INFO, 'console', args);
  };
  console.warn = function(...args) {
    _origConsole.warn.apply(console, args);
    _captureConsoleEntry(LEVELS.WARN, 'console', args);
  };
  console.error = function(...args) {
    _origConsole.error.apply(console, args);
    _captureConsoleEntry(LEVELS.ERROR, 'console', args);
  };
}

// ── 恢复原始 console 方法 ──
export function unpatchConsole() {
  console.log = _origConsole.log;
  console.debug = _origConsole.debug;
  console.info = _origConsole.info;
  console.warn = _origConsole.warn;
  console.error = _origConsole.error;
}

// 自动 patch
patchConsole();

// 暴露给控制台调试（保留原有习惯）
window.__logLevel__ = _level;

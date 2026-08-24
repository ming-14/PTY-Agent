/**
 * 终端基础设施：终端生命周期（创建、销毁、输出、只读状态）
 */

import { state, getSessionFontSize, clearSessionFontSize, isSizeUILocked } from '../../domain/state.js';
import { debug } from '../../domain/logger.js';
import { t } from '../../domain/i18n.js';
import { $ } from '../domUtils.js';
import { wsSend, sendToSession } from '../wsClient.js';
import { DEFAULT_FONT_SIZE, DEFAULT_COLS, DEFAULT_ROWS } from '../../domain/constants.js';
import { currentTheme } from '../storage.js';
import { applyTerminalSizeFromSession } from './shared.js';
import { trackAppMouseMode, getInitialMouseOverride, setAppMouseMode } from './mouseMode.js';
import { shouldTrackFocus } from '../rimeManager.js';
import { trackCursorSequences } from './cursorDebug.js';
import { attachCustomKeyEventHandler } from './input.js';
import { applyTerminalFrameSize } from './scale.js';
import { isTermAtBottom, scrollTermToBottom, scrollTermToTop } from './scroll.js';
import { bindTerminalEvents } from './events.js';
import { getTerminalFontFamily } from '../fontLoader.js';

export function ensureTerminal(uid) {
  if (state.termInstances[uid]) return;

  const s = state.sessions[uid];
  const container = $('terminal-container');
  const div = document.createElement('div');
  div.id = 'term-' + uid;
  div.className = 'term-instance';
  container.appendChild(div);

  // xterm.js 需要在可见容器上初始化才能拿到正确尺寸，
  // 临时让 frame 和当前 div 可见，初始化完成后再恢复状态。
  const frame = $('terminal-frame');
  const empty = $('empty-state');
  const wasFrameHidden = frame && getComputedStyle(frame).display === 'none';
  if (wasFrameHidden) {
    if (empty) empty.style.display = 'none';
    frame.style.display = 'block';
  }
  div.classList.add('active');

  const Terminal = window.Terminal;
  const WebLinksAddon = window.WebLinksAddon;
  const FitAddon = window.FitAddon;

  // 自适应模式：先以默认尺寸初始化，term.open 后由 FitAddon.fit() 计算实际尺寸
  // 其它模式：使用 applyTerminalSizeFromSession 返回的目标尺寸
  const size = applyTerminalSizeFromSession(s);
  const initCols = size ? size.cols : (s.cols || DEFAULT_COLS);
  const initRows = size ? size.rows : (s.rows || DEFAULT_ROWS);

  const term = new Terminal({
    theme: currentTheme(),
    fontFamily: getTerminalFontFamily(),
    // 字号按会话独立维护（state.sessionFontSizes[uid]）。
    // 新会话首次打开时未设置 → getSessionFontSize 返回 DEFAULT_FONT_SIZE，
    // 随后 ensureTerminal 末尾的 applySessionFrameRatio 会用渲染后的 cell 尺寸
    // 反算 frameRatio 并保存；再次打开该会话时按保存的 ratio 反算字号恢复框大小。
    fontSize: getSessionFontSize(uid),
    fontWeight: 'normal',
    cursorBlink: true,
    cursorStyle: 'bar',
    cursorInactiveStyle: 'block',
    // 历史会话初始即禁用 stdin：避免 term.open() 后同步改 options.disableStdin
    // 触发 _handleOptionsChanged → _fireOnCanvasResize，其异步 rAF 在 renderer
    // 首帧渲染前（dimensions.css 未就绪）读 device/css 抛 TypeError
    // （xterm 渲染链挂死）。applyReadonlyState 仅处理运行时转变。
    disableStdin: !!(s && s.history),
    scrollback: 10000,
    allowProposedApi: true,
    allowTransparency: true,
    screenReaderMode: false,
    rightClickSelectsWord: false,
    macOptionIsMeta: false,
    cols: initCols,
    rows: initRows,
    altClickMovesCursor: false,
  });

  if (WebLinksAddon) {
    term.loadAddon(new WebLinksAddon.WebLinksAddon());
  }
  // FitAddon：与 ttyd 一致，使用官方 addon 计算自适应尺寸
  // 替代旧版手动 measureTerminalCellSize + computeAdaptiveSize 的方式
  let fitAddon = null;
  if (FitAddon && FitAddon.FitAddon) {
    fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
  }
  term.open(div);

  const inst = {
    term, div,
    appMouseMode: false, appMouseModeDecset: false, appMouseModeDaemon: false,
    appMouseModePs: null, appMouseEncoding: 'sgr',
    appFocusReport: false,
    appAlternateScroll: false, appAlternateBuffer: false,
    mouseInputOverride: getInitialMouseOverride(s && s.uid),
    _pressedMouseButton: null, _focused: false, _touchAnchor: null,
    _wheelAccum: 0, _vtWheelAccum: 0,
    fitAddon,
    // resize 期间缓冲 ConPTY output，避免 partial repaint 污染 xterm.js
    // _resizePending=true 时 handleOutput 将 output 推入 _resizeBuffer 而非直接写入
    // resize_complete 重建 buffer 后丢弃缓冲（snapshot 已含 resize 期间的有效输出）
    // _resizeStartedAt 用于超时保护：2 秒未收到匹配 resize_complete 则强制清除
    _resizePending: false,
    _resizeBuffer: [],
    _resizeStartedAt: 0,
  };

  trackAppMouseMode(term, inst);
  trackCursorSequences(term, uid);
  attachCustomKeyEventHandler(term, uid);
  bindTerminalEvents(term, inst, uid);

  try {
    // 初始化为会话尺寸：仅在尺寸确实不同时设置有意标志并 resize——
    // 否则 resize 是 no-op（不发 onResize），标志会卡死为 true，
    // 之后任何容器自动 resize 被误判为有意 → 误发后端 → PTY 被重算。
    if (term.cols !== initCols || term.rows !== initRows) {
      inst._pendingDaemonResize = true;
      term.resize(initCols, initRows);
    }
  } catch (e) {
    error('resize', 'initial resize failed: %s', e && e.message);
  }

  // 若当前会话不是活动标签，恢复隐藏状态
  if (uid !== state.activeTab) {
    div.classList.remove('active');
    if (wasFrameHidden) {
      frame.style.display = 'none';
      if (empty) empty.style.display = 'flex';
    }
  }

  term.onData(data => {
    const s = state.sessions[uid];
    const inst = state.termInstances[uid];

    // 鼠标/焦点序列过滤（最后一道防线）：
    // xterm.js 内置的事件处理可能绕过 capture 阶段的 stopPropagation，
    // 仍通过 term.onData 生成转义序列。此处按用户开关拦截：
    // - 用户关闭鼠标输入（mouseInputOverride=false）：阻止 SGR 鼠标序列发送
    const c0 = data.charCodeAt(0);
    const c1 = data.charCodeAt(1);
    const c2 = data.charCodeAt(2);
    if (c0 === 0x1b && c1 === 0x5b) {
      if (c2 === 0x3c) {
        // \x1b[< — SGR 鼠标编码
        debug('mouse', 'onData SGR: %s', JSON.stringify(data));
        const dbg = document.getElementById('terminal-mouse-debug');
        if (dbg) dbg.textContent = data;
        if (inst && inst.appMouseMode && !inst.mouseInputOverride) {
          debug('mouse', 'onData SGR dropped: mouseInputOverride off uid=%s', uid);
          return;
        }
      } else if (c2 === 0x4d) {
        // \x1b[M — X10/UTF-8 鼠标编码
        debug('mouse', 'onData X10 mouse: %s', JSON.stringify(data));
      } else if (c2 === 0x49 || c2 === 0x4f) {
        // \x1b[I / \x1b[O — 焦点报告（Focus In/Out）
        debug('focus', 'onData focus report: %s', JSON.stringify(data));
        if (!shouldTrackFocus(uid)) {
          debug('focus', 'onData focus report dropped: keyboard+mouse disabled uid=%s', uid);
          return;
        }
      }
    }

    if (!s || !s.running || s.closing) {
      debug('terminal', 'onData dropped: uid=%s running=%s closing=%s', uid, s && s.running, s && s.closing);
      return;
    }
    if (inst && inst._readonly) {
      debug('terminal', 'onData ignored: uid=%s readonly (history)', uid);
      return;
    }
    // 沙箱会话为真实 ConPTY（hpcon），输入直接透传给 conhost：
    // 回显/行编辑/方向键历史由 conhost 处理，与原生 ConPTY 会话一致。
    sendToSession(uid, { type: 'input', data: data });
  });

  term.onResize(({ cols, rows }) => {
    const s2 = state.sessions[uid];
    const wantCols = s2 ? s2.cols : 0;
    const wantRows = s2 ? s2.rows : 0;
    debug('resize', 'onResize uid=%s cols=%d rows=%d wantCols=%d wantRows=%d pendingDaemon=%s external=%s',
          uid, cols, rows, wantCols, wantRows, !!inst._pendingDaemonResize, !!inst._externalResize);

    // 外部 resize（session_resized / resize_complete 已含完整 snapshot，
    // 不需要再向服务端发 resize，否则会触发冗余的 resize_complete 导致
    // buffer 被写两遍 + _resizePending 竞态）
    if (inst._externalResize) {
      inst._externalResize = false;
      debug('resize', 'onResize -> external, skip');
      return;
    }

    // ── 有意 resize（applyTerminalSize / fit 等显式调用）→ 正常同步后端 ──
    if (inst._pendingDaemonResize) {
      inst._pendingDaemonResize = false;
      debug('resize', 'onResize -> DELIBERATE uid=%s cols=%d rows=%d (send backend)', uid, cols, rows);
      requestAnimationFrame(() => {
        try { applyTerminalFrameSize(uid); } catch (_) {}
      });
      if (!s2 || !s2.running || s2.history || s2.closing) {
        debug('resize', 'onResize deliberate: session not active, skip');
        return;
      }
      if (s2.mode === 'subprocess' || s2.ptyType === 'subprocess') {
        // 子进程模式无终端：不发送 resize（后端拒绝）
        debug('resize', 'onResize deliberate: subprocess, skip');
        return;
      }
      if (isSizeUILocked(uid)) {
        debug('resize', 'onResize deliberate: locked by another connection, skip');
        return;
      }
      if (inst._resizePending && inst._resizeBuffer.length > 0) {
        debug('resize', 'onResize deliberate: nested resize, discard %d buffered', inst._resizeBuffer.length);
        inst._resizeBuffer = [];
      }
      inst._resizePending = true;
      inst._resizeStartedAt = Date.now();
      s2.cols = cols;
      s2.rows = rows;
      sendToSession(uid, { type: 'resize', cols: cols, rows: rows });
      return;
    }

    // ── 容器自动 resize（xterm ResizeObserver 触发，来自字体/框变化）──
    // PTY 尺寸是权威，回退到会话尺寸，绝不向后端发送。
    if (wantCols && (cols !== wantCols || rows !== wantRows)) {
      debug('resize', 'onResize -> AUTO-REVERT uid=%s %dx%d -> %dx%d (suppress backend resize)',
            uid, cols, rows, wantCols, wantRows);
      try { inst.term.resize(wantCols, wantRows); } catch (_) {}
      return;
    }

    // 回退完成 / 尺寸一致：仅跟随 frame
    debug('resize', 'onResize -> match, follow frame uid=%s cols=%d rows=%d', uid, cols, rows);
    requestAnimationFrame(() => {
      try { applyTerminalFrameSize(uid); } catch (_) {}
    });
  });

  state.termInstances[uid] = inst;

  // 订阅响应先于本函数到达时，后端权威鼠标模式暂存在 session._pendingAppMouseMode，
  // inst 创建后回填（修复 setAppMouseMode 早退竞态导致的模式丢失）
  if (s && s._pendingAppMouseMode !== undefined) {
    const pending = s._pendingAppMouseMode;
    delete s._pendingAppMouseMode;
    setAppMouseMode(uid, pending);
  }

  // 历史会话设为只读，禁止焦点/输入反馈
  applyReadonlyState(uid, !!(s && s.history));

  // 初始化完成后立即应用一次 frame 尺寸，确保首次渲染正确
  applyTerminalFrameSize(uid);

}

export function applyReadonlyState(sid, readonly) {
  const inst = state.termInstances[sid];
  if (!inst) return;
  inst._readonly = readonly;
  // disableStdin 是 options：变更触发 _handleOptionsChanged → _fireOnCanvasResize，
  // 其异步 rAF 在 renderer 首帧渲染前（dimensions.css 未就绪）读 device/css 抛
  // TypeError。历史会话的初始值已在构造函数设置（ensureTerminal 按 s.history 预置），
  // 此处仅对"renderer 已就绪"的运行时转变（如会话自然结束）赋值。
  const core = inst.term._core;
  const rendererReady = !!(core && core._renderService && core._renderService.dimensions
    && core._renderService.dimensions.css);
  if (rendererReady) {
    inst.term.options.disableStdin = readonly;
  }
  inst.div.classList.toggle('readonly', readonly);
  inst.div.tabIndex = readonly ? -1 : 0;
  // 注意：不能通过运行时修改 options.theme 隐藏光标——
  // theme 变更触发 xterm _handleOptionsChanged → _fireOnCanvasResize，
  // 其异步 rAF 视口刷新在 renderer 已释放（切标签/隐藏）时会读
  // renderService.dimensions.device 抛 TypeError，导致终端渲染/输入链挂死。
  // 只读光标由下方 \x1b[?25l（DECTCEM 隐藏）实现。
  if (readonly) {
    inst._focused = false;
    try {
      const frame = $('terminal-frame');
      if (frame) frame.classList.remove('focused');
    } catch (_) {}
    try { inst.term.blur(); } catch (_) {}
    try { inst.term.write('\x1b[?25l'); } catch (_) {}
  }
}

/**
 * 恢复 scrollback 并写入 snapshot（用于首次订阅/resize_complete 场景）
 *
 * 守护进程返回 scrollback + snapshot，前端需要：
 *   1. \x1b[3J 清空 scrollback
 *   2. \x1b[2J\x1b[1;1H 清空可见屏幕 + 光标定位到 (0, 0)
 *   3. 写入 scrollback 行 + 额外 \r\n 推入 scrollback 区
 *   4. \x1b[2J + snapshot 清空可见屏幕 + 写入 snapshot
 *
 * 视口修复（解决自适应模式滚动跳回顶端 bug）：
 * - \x1b[3J 会同步重置 xterm.js ydisp=0（视口到顶端）
 * - term.write 是异步的，写入内容前视口停在顶端，用户看到"跳回顶端"
 * - "过一会自己好了" = write 完成后视口跟随到底部
 * - 修复：在最后一次 term.write 的 callback 中执行 scroll，确保 write 完成后再滚动
 *
 * @param {Terminal} term xterm.js 终端实例
 * @param {string[]} scrollbackLines scrollback 行的 ANSI 字符串数组
 * @param {string} snapshot 后端返回的屏幕快照（含 VT 序列与光标定位）
 * @param {boolean} isHistory 是否历史会话（true 滚到顶端，false 滚到底部）
 */
export function restoreScrollbackAndSnapshot(term, scrollbackLines, snapshot, isHistory = false) {
  const hasScrollback = !!(scrollbackLines && scrollbackLines.length > 0);

  // 视口修复：write 完成后再 scroll，避免 \x1b[3J 重置 ydisp=0 后视口停在顶端
  const doScroll = () => {
    try {
      if (isHistory) scrollTermToTop(term);
      else scrollTermToBottom(term);
    } catch (_) {}
  };

  if (hasScrollback) {
    // 模式 A：有 scrollback，清空 + 恢复 + 写 snapshot
    term.write('\x1b[3J\x1b[2J\x1b[1;1H');

    const R = term.rows;
    const parts = [];
    for (const line of scrollbackLines) {
      parts.push(line);
      parts.push('\r\n');
    }
    for (let i = 0; i < R - 1; i++) {
      parts.push('\r\n');
    }
    term.write(parts.join(''));
    debug('terminal', 'restoreScrollback: wrote %d lines + %d extra \\r\\n',
          scrollbackLines.length, R - 1);

    if (snapshot && snapshot.length > 0) {
      // 最后一次 write 用 callback，确保 scroll 在 write 完成后执行
      term.write('\x1b[2J' + snapshot, doScroll);
    } else {
      // 无 snapshot，用空 write 触发 callback
      term.write('', doScroll);
    }
  } else {
    // 模式 B：无 scrollback，清空 scrollback + 可见屏幕 + 写 snapshot
    // \x1b[3J 清 xterm.js scrollback（term.resize() 重排可能残留旧内容），
    // \x1b[2J 清可见区，确保 snapshot 写入前 buffer 完全干净
    debug('terminal', 'restoreScrollback: no capture, clear scrollback + visible');
    if (snapshot && snapshot.length > 0) {
      term.write('\x1b[3J\x1b[2J\x1b[1;1H' + snapshot, doScroll);
    } else {
      doScroll();
    }
  }

  try { term.refresh(0, term.rows - 1); } catch (_) {}
}

export function replayPending(sid) {
  const s = state.sessions[sid];
  const inst = state.termInstances[sid];
  if (!s || !inst) return;
  const isHistory = s.history || false;

  // 首次订阅时守护进程返回 scrollback（wezterm 终端模型历史区）+ replay（visible snapshot）
  // 写入流程：
  //   1. \x1b[3J\x1b[2J\x1b[1;1H 清空 scrollback + 可见屏幕 + 光标定位到 (0, 0)
  //   2. 写入 scrollback 行 + (R-1) 个 \r\n 推入 scrollback 区
  //   3. \x1b[2J + replay 清空可见屏幕 + 写入 snapshot（含每行 CSI row;col H 定位 + 末尾光标序列）
  //
  // 已订阅会话切回：
  // - pendingScrollback + pendingReplay 均为空（handlers.py 已订阅时返回 ""）
  // - 不 clear()，保留 xterm.js 实例的 scrollback
  if (s.pendingScrollback && s.pendingReplay) {
    // 首次订阅，有 scrollback + replay
    const scrollbackLines = s.pendingScrollback.split('\r\n');
    // 去除末尾空字符串（capture_scrollback 末尾 \r\n 导致 split 产生空元素）
    if (scrollbackLines.length > 0 && scrollbackLines[scrollbackLines.length - 1] === '') {
      scrollbackLines.pop();
    }
    try {
      // isHistory 传入：restoreScrollbackAndSnapshot 内部会在 write 完成后
      // 通过 callback 执行正确的 scroll（避免 \x1b[3J 后视口停在顶端）
      restoreScrollbackAndSnapshot(inst.term, scrollbackLines, s.pendingReplay, isHistory);
      debug('terminal', 'replayPending sid=%s: wrote scrollback=%d lines + replay len=%d',
            sid, scrollbackLines.length, s.pendingReplay.length);
    } catch (e) {
      error('terminal', 'replayPending restoreScrollbackAndSnapshot failed: %s', e && e.message);
      // 回退：只写 replay
      try { inst.term.clear(); } catch (_) {}
      inst.term.write(s.pendingReplay);
    }
    s.pendingScrollback = null;
    s.pendingReplay = null;
    // scroll 已由 restoreScrollbackAndSnapshot 内部 callback 处理，无需重复调用
  } else if (s.pendingReplay) {
    // 首次订阅但无 scrollback（scrollback 为空）
    try { inst.term.clear(); } catch (e) {}
    inst.term.write(s.pendingReplay);
    s.pendingReplay = null;
    s.pendingScrollback = null;
    if (isHistory) scrollTermToTop(inst.term);
    else scrollTermToBottom(inst.term);
  } else if (s.pendingSnapshot) {
    // 历史会话回放
    try { inst.term.clear(); } catch (e) {}
    inst.term.write(s.pendingSnapshot);
    scrollTermToTop(inst.term);
    s.pendingSnapshot = null;
  }

  // 子进程模式：首次订阅时后端返回 stderr 全文，以 ERR > 前缀写入
  if (s.pendingStderrReplay) {
    inst.term.write(formatStderrText(s.pendingStderrReplay));
    s.pendingStderrReplay = null;
  }

  if (Array.isArray(s.pendingOutput) && s.pendingOutput.length) {
    for (const text of s.pendingOutput) inst.term.write(text);
    s.pendingOutput = [];
    if (!isHistory) scrollTermToBottom(inst.term);
    setTimeout(() => updateTerminalSnapshot(sid), 50);
  }
}

export function queuePendingOutput(uid, text) {
  const s = state.sessions[uid];
  if (!s) return;
  if (!Array.isArray(s.pendingOutput)) s.pendingOutput = [];
  s.pendingOutput.push(text);
}

/** 子进程 stderr 文本：逐行加红色 ERR > 前缀（空行保留） */
export function formatStderrText(text) {
  return text
    .split('\n')
    .map(l => (l.trim() ? '\x1b[31m' + t('term.stderrPrefix') + l + '\x1b[0m' : l))
    .join('\n');
}

export function handleOutput(msg) {
  // 路由键：后端输出消息携带 sessionUid（优先），兼容旧 sessionId
  const uid = msg._uid || msg.sessionUid || msg.sessionId;
  const inst = state.termInstances[uid];
  let text = String(msg.data || '');
  // 子进程模式 stderr：以红色 ERR > 前缀逐行展示
  if (msg.stream === 'stderr') {
    text = formatStderrText(text);
  }
  debug('terminal', 'handleOutput uid=%s inst=%s activeTab=%s len=%d stream=%s',
        uid, !!inst, state.activeTab, text.length, msg.stream || 'pty');
  if (inst) {
    // resize 进行中时缓冲 output，不直接写入 xterm.js。
    // 原因：ConPTY 在 resize 期间会发出针对旧/中间尺寸的 partial repaint
    // （如 \e[24;34H\e[J...），直接写入会污染 xterm.js 内部状态，导致
    // resize_complete 重建后仍出现错位/吞输出。
    // 缓冲的 output 会在 resize_complete 重建后被丢弃 —— 因为后端 wezterm 终端模型
    // 持续 feed ConPTY output，resize 期间的有效输出已包含在 snapshot 中，
    // 缓冲的只是冗余的 partial repaint。
    if (inst._resizePending) {
      // 超时保护：2 秒内未收到匹配的 resize_complete，强制清除并丢弃缓冲。
      // 防止 WebSocket 断开/后端异常导致 _resizePending 永久卡住（用户看不到任何输出）。
      if (Date.now() - inst._resizeStartedAt > 2000) {
        debug('terminal',
              'handleOutput uid=%s: resize pending timeout (%dms), flush %d buffered, resume normal',
              uid, Date.now() - inst._resizeStartedAt, inst._resizeBuffer.length);
        const buf = inst._resizeBuffer;
        inst._resizePending = false;
        inst._resizeBuffer = [];
        // 缓冲内容写入终端（新尺寸内容，不会错位）
        if (buf.length > 0) {
          inst.term.write(buf.join(''));
        }
        // 继续走下面的正常写入路径（不 return）
      } else {
        inst._resizeBuffer.push(text);
        debug('terminal',
              'handleOutput uid=%s BUFFERED (resize pending) len=%d buf_count=%d',
              uid, text.length, inst._resizeBuffer.length);
        return;
      }
    }
    const s = state.sessions[uid];
    const isHistory = s && s.history;
    // 不再排队，直接写入对应 xterm 实例
    // xterm.js 在 display:none 时 write 仍正常累积 scrollback（已验证）
    // 这样切回会话时 scrollback 完整，无需 replay
    const wasAtBottom = isTermAtBottom(inst.term);
    debug('terminal', 'handleOutput write uid=%s history=%s wasAtBottom=%s divActive=%s',
          uid, isHistory, wasAtBottom, inst.div.classList.contains('active'));
    inst.term.write(text);
    if (isHistory) scrollTermToTop(inst.term);
    else if (wasAtBottom && state.activeTab === uid) scrollTermToBottom(inst.term);
    setTimeout(() => updateTerminalSnapshot(uid), 50);
  } else {
    queuePendingOutput(uid, text);
  }
}

export function updateTerminalSnapshot(sid) {
  const targetSid = sid || state.activeTab;
  if (!targetSid) return;
  const inst = state.termInstances[targetSid];
  if (!inst) return;
  try {
    const buf = inst.term.buffer.active;
    const rows = inst.term.rows;
    const lines = [];
    for (let r = 0; r < rows; r++) {
      const line = buf.getLine(r);
      if (line) lines.push(line.translateToString(true));
    }
    const snap = document.getElementById('terminal-snapshot');
    if (snap) snap.textContent = lines.join('\n');
  } catch (e) {}
}

export function applyTheme() {
  const theme = currentTheme();
  for (const sid of Object.keys(state.termInstances)) {
    state.termInstances[sid].term.options.theme = theme;
  }
}

export function disposeTerminal(sid) {
  const inst = state.termInstances[sid];
  if (inst) {
    try { inst.term.dispose(); } catch (e) {}
    inst.div.remove();
    delete state.termInstances[sid];
  }
  // 清理运行时字号（frameRatio 已持久化在 sessionSizeConfigs，不受影响）
  clearSessionFontSize(sid);
}

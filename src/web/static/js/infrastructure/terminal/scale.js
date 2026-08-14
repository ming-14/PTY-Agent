/**
 * 终端基础设施：终端尺寸与字号缩放（按会话 frameRatio）
 *
 * 核心思路（"自适应的是比例"）：
 *   1. 每个会话（含 adaptive）持久化 frameRatio（框/stage 占比，取宽高较小值）
 *   2. applySessionFrameRatio（切标签 / stage 变化 / 新会话首次打开时调用）：
 *      - adaptive 模式：按 ratio 设 frame 尺寸 = ratio×stage → fit() 算 cols/rows
 *        （adaptive 自适应 stage 宽高比，cols/rows 跟着 fit() 变；保持比例不填满）
 *      - 非 adaptive 模式：按 ratio 反算字号（cols/rows 不变）
 *   3. zoomActiveSession（Ctrl+滚轮 / 触摸捏合 / Ctrl+± 调用）：
 *      - 所有模式统一：按 ratio 反算字号（cols/rows 不变）
 *      - Ctrl+滚轮只调整框占 stage 的真实大小（通过字号），不改 cols/rows
 *   4. resetActiveSessionZoom（Ctrl+0）：
 *      - 所有模式统一：字号重置为 DEFAULT_FONT_SIZE，按新 frame 反算 ratio（cols/rows 不变）
 *
 * 关键区分：
 *   - "自适应 stage 宽高比"（cols/rows 变）只发生在 applySessionFrameRatio 的 adaptive 分支
 *   - "调整框真实大小"（字号变，cols/rows 不变）发生在 zoomActiveSession 和 resetActiveSessionZoom
 *
 * 同步流程：
 *   前端 term.resize(cols, rows) → term.onResize 回调 → wsSend(resize) → 守护进程 PTY resize
 */

import { state, getSessionSizeConfigBySid, getSessionFontSize, setSessionFontSize,
         getSessionFrameRatio, setActiveSessionFrameRatio } from '../../domain/state.js';
import { debug } from '../../domain/logger.js';
import { $ } from '../domUtils.js';
import { wsSend } from '../wsClient.js';
import {
  applyTerminalSizeFromSession,
  getTerminalCellSize,
  computeFrameRatio,
  computeFontSizeFromRatio,
} from './shared.js';
import {
  DEFAULT_FONT_SIZE,
  FRAME_RATIO_MIN, FRAME_RATIO_MAX,
} from '../../domain/constants.js';

/**
 * 通过 FitAddon 获取终端实例。
 * FitAddon 在 lifecycle.ensureTerminal 中通过 term.loadAddon(fitAddon) 加载，
 * 并把引用挂在 inst.fitAddon 上。
 */
function getFitAddon(sid) {
  const inst = state.termInstances[sid];
  return inst && inst.fitAddon ? inst.fitAddon : null;
}

/**
 * 应用终端框尺寸：所有模式下 frame 都跟随 xterm 实际渲染区。
 *
 * frame 始终 = .xterm-screen 尺寸（canvas 容器）：
 *   - 读 .xterm-screen 的 getBoundingClientRect 作为 frame 尺寸
 *   - .xterm-screen 尺寸 = cols × cellWidth × rows × cellHeight
 *   - .xterm 是 width:100%/height:100%，读 .xterm 会拿到 frame 尺寸（循环依赖）
 *
 * @param {string} sid 会话 ID
 */
export function applyTerminalFrameSize(sid) {
  const inst = state.termInstances[sid];
  if (!inst || !inst.term) return;
  const frame = $('terminal-frame');
  if (!frame) return;

  // 不使用 .adaptive class，所有模式 frame 都跟随 xterm 实际渲染区
  frame.classList.remove('adaptive');

  // 读 .xterm-screen（canvas 容器）而非 .xterm（后者是 100%，会拿到 frame 尺寸）
  const termEl = inst.term.element
    ? inst.term.element.querySelector('.xterm-screen')
    : null;
  if (!termEl) {
    // xterm 尚未渲染，仅活动标签需要重试（非活动标签 div 隐藏，无需设置 frame）
    if (state.activeTab === sid) {
      requestAnimationFrame(() => applyTerminalFrameSize(sid));
    }
    return;
  }
  // 用 rAF 等待 xterm 完成当前帧渲染，避免读到陈旧尺寸
  requestAnimationFrame(() => {
    const rect = termEl.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      // div 为 display:none 时 .xterm-screen 尺寸为 0。
      // 仅当 sid 是当前活动标签时才重试（切标签后 div 刚可见但尚未渲染），
      // 非活动标签的 div 本就是隐藏的，无需设置 frame，直接返回避免无限 rAF 循环
      if (state.activeTab === sid) {
        requestAnimationFrame(() => applyTerminalFrameSize(sid));
      }
      return;
    }
    // frame 无 border（CSS 用 box-shadow 外部投影代替），box-sizing: content-box
    // frame width = .xterm-screen width，frame 内容区 = .xterm-screen 尺寸
    // .xterm (100%) = frame 内容区 = .xterm-screen，xterm 内部 canvas 渲染区与 buffer 完全匹配
    frame.style.width = Math.ceil(rect.width) + 'px';
    frame.style.height = Math.ceil(rect.height) + 'px';
    frame.style.maxWidth = '';
    frame.style.maxHeight = '';
  });

  if (state.activeTab === sid) {
    frame.style.display = 'block';
    const empty = $('empty-state');
    if (empty) empty.style.display = 'none';
  }
}

/**
 * 应用终端尺寸。
 *
 * 读取该会话自身的尺寸模式（按 uid 查询）：
 * - 'default':  使用守护进程上报的 s.cols/s.rows
 * - 'adaptive': 调 applySessionFrameRatio：按保存的 frameRatio 设 frame 尺寸 + fit() 算 cols/rows
 *               （adaptive 自适应 stage 宽高比，cols/rows 跟着 fit 变；Ctrl+滚轮不走这里）
 * - 'fixed':    使用 fixedCols/fixedRows，直接 term.resize
 * - 'custom':   使用 customCols/customRows，直接 term.resize
 *
 * 同步方向（与 ttyd 一致）：
 *   term.resize(cols, rows) → term.onResize → wsSend(resize) → 守护进程
 * 此处主动同步用于：用户切换模式 / 用户选择固定尺寸 / 窗口 resize / 切换标签
 *
 * @param {string} sid 会话 ID
 * @param {boolean} force 是否强制向守护进程发送 resize（用户显式选择尺寸时为 true）
 * @param {object} opts { skipDaemonResize: boolean } 跳过向守护进程发送 resize
 */
export function applyTerminalSize(sid, force, opts) {
  const s = state.sessions[sid];
  const inst = state.termInstances[sid];
  if (!s || !inst) return;

  const skipDaemonResize = !!(opts && opts.skipDaemonResize);
  const forceDaemonResize = !!force;
  const cfg = getSessionSizeConfigBySid(sid);
  // 历史会话原先是自适应的，固定显示会话生前最后的尺寸。
  const mode = s.history ? 'default' : cfg.mode;

  // adaptive 模式调 applySessionFrameRatio（按 ratio 设 frame + fit；Ctrl+滚轮不走这里）
  if (mode === 'adaptive') {
    applySessionFrameRatio(sid);
    debug('terminal', 'applyTerminalSize adaptive → applySessionFrameRatio sid=%s (force=%s)',
          sid, forceDaemonResize);
    return;
  }

  // 非 adaptive 模式：从配置算出目标 cols/rows
  const size = applyTerminalSizeFromSession(s);
  if (!size) {
    // 理论不会到这里（adaptive 已在上面处理），保险起见调用 fit
    const fit = getFitAddon(sid);
    if (fit) { try { fit.fit(); } catch (_) {} }
    return;
  }
  const cols = size.cols;
  const rows = size.rows;

  // 单路径同步（与 ttyd 一致）：
  //   只调用 term.resize()，由 onResize 回调统一发送 wsSend(resize)
  if (inst.term.cols !== cols || inst.term.rows !== rows) {
    try {
      inst.term.resize(cols, rows);
      debug('terminal', 'applyTerminalSize %s → term.resize sid=%s %dx%d (onResize will send)',
            mode, sid, cols, rows);
    } catch (e) {
      console.error('resize failed', e);
    }
  } else if (forceDaemonResize && !skipDaemonResize && s.running && !s.history) {
    // 尺寸未变化但 force=true：用户显式选择相同尺寸，仍需同步守护进程
    // （此场景 onResize 不会触发，需要主动发送）
    wsSend({ type: 'resize', session_id: sid, cols: cols, rows: rows });
    debug('terminal', 'applyTerminalSize %s force send sid=%s %dx%d',
          mode, sid, cols, rows);
  } else {
    debug('terminal', 'applyTerminalSize %s skip resize sid=%s (size unchanged)',
          mode, sid);
  }
}

/**
 * 应用指定会话的字号到其 term 实例。
 *
 * 只应用指定 sid 会话的字号。
 * 字号按会话独立维护（state.sessionFontSizes[sid]），不全局共享。
 * 调用后用 rAF 等待 xterm 内部 dimensions 刷新，再读 .xterm-screen 重算 frame。
 *
 * @param {string} sid 会话 ID
 */
export function applyTerminalFontSize(sid) {
  const inst = state.termInstances[sid];
  if (!inst || !inst.term) return;
  const fontSize = getSessionFontSize(sid);
  try {
    inst.term.options.fontSize = fontSize;
  } catch (e) {
    console.error('set fontSize failed', e);
  }
  // 不 fit，cols/rows 不变，frame 跟随新 cell 像素
  requestAnimationFrame(() => {
    try { applyTerminalFrameSize(sid); } catch (_) {}
    // 再等一帧，确保 xterm 内部完全刷新（有些渲染器要两帧）
    requestAnimationFrame(() => {
      try { applyTerminalFrameSize(sid); } catch (_) {}
    });
  });
  debug('terminal', 'applyTerminalFontSize sid=%s → %s', sid, fontSize);
}

/**
 * 重新计算并应用终端尺寸。
 * 用于尺寸模式切换、窗口 resize 等场景。
 *
 * 可选 sid 参数：被降级到 fixed 时仅需对单个会话应用，
 * 避免遍历所有会话引起其他会话不必要的 resize。
 *
 * @param {boolean} force 是否强制向守护进程发送 resize（用户显式选择尺寸时为 true）
 * @param {string} [sid] 可选：仅对指定会话应用；省略则遍历所有 termInstances
 */
export function reapplyAllTerminalSizes(force, sid) {
  if (sid) {
    if (state.termInstances[sid]) {
      applyTerminalSize(sid, force);
      applyTerminalFrameSize(sid);
    }
    return;
  }
  for (const s of Object.keys(state.termInstances)) {
    applyTerminalSize(s, force);
    applyTerminalFrameSize(s);
  }
}

/**
 * 根据会话保存的 frameRatio 和当前 stage 尺寸，恢复/应用框大小。
 *
 * 触发场景（注意：Ctrl+滚轮不走这里，走 zoomActiveSession）：
 *   - 切换标签（ui.switchTab）
 *   - stage 尺寸变化（ResizeObserver：sidebar 拖动、窗口 resize、全屏切换）
 *   - ensureTerminal 初始化完成后
 *   - applyTerminalSize 的 adaptive 分支
 *
 * 行为分支（所有模式都参与 ratio 记忆）：
 *   - adaptive 模式（cols/rows 会变，自适应 stage 宽高比）：
 *     - frameRatio 为 null（首次）：用当前 frame 尺寸反算 ratio 并保存，不改变任何东西
 *     - frameRatio 有值：设 frame 尺寸 = stage × ratio，调 fit() 基于 frame 尺寸算 cols/rows
 *       （adaptive 自适应 stage 宽高比，cols/rows 跟着 fit 变；保持比例不填满）
 *   - 非 adaptive 模式（cols/rows 不变）：
 *     - frameRatio 为 null（首次）：用当前字号渲染后的 frame 尺寸反算 ratio 并保存，不改变字号
 *     - frameRatio 有值：根据 stage 当前尺寸 + ratio 反算字号并应用（cols/rows 不变）
 *
 * @param {string} sid 会话 ID
 * @returns {boolean} 是否改变了尺寸（字号或 cols/rows）
 */
export function applySessionFrameRatio(sid) {
  const inst = state.termInstances[sid];
  if (!inst || !inst.term) return false;
  const s = state.sessions[sid];
  if (!s || !s.uid) return false;

  const cfg = getSessionSizeConfigBySid(sid);
  // 历史会话原先是自适应的，固定显示会话生前最后的尺寸。
  // 允许 Ctrl+滚轮缩放字号（按 frameRatio 反算），且记忆 frameRatio。
  const isHistory = !!s.history;
  const stage = $('terminal-stage');
  if (!stage) return false;
  const stageStyle = getComputedStyle(stage);
  const padLeft = parseFloat(stageStyle.paddingLeft) || 0;
  const padRight = parseFloat(stageStyle.paddingRight) || 0;
  const padTop = parseFloat(stageStyle.paddingTop) || 0;
  const padBottom = parseFloat(stageStyle.paddingBottom) || 0;
  const contentW = stage.clientWidth - padLeft - padRight;
  const contentH = stage.clientHeight - padTop - padBottom;
  if (!contentW || !contentH) return false;

  // 读当前 cell 尺寸（_renderService.dimensions 同步刷新）
  const cell = getTerminalCellSize(inst.term);
  if (!cell.w || !cell.h) {
    // xterm 尚未渲染，下一帧重试（仅活动标签需要）
    if (state.activeTab === sid) {
      requestAnimationFrame(() => applySessionFrameRatio(sid));
    }
    return false;
  }

  const savedRatio = getSessionFrameRatio(s.uid);

  // ── adaptive 模式：按 ratio 设 frame 尺寸，fit() 算 cols/rows ──
  // 历史会话强制非 adaptive：固定生前最后 cols/rows，不 fit 自适应 stage
  if (cfg.mode === 'adaptive' && !isHistory) {
    const fit = getFitAddon(sid);
    if (!fit) return false;

    if (savedRatio == null) {
      // 首次打开：用当前 frame 尺寸反算 ratio 并保存。
      // 防溢出：若当前 frame 尺寸超过 stage content（如从 fixed 200x50 切 adaptive），
      // 必须用算出的 ratio 重新设 frame = stage × ratio + fit()，否则 frame 保持大尺寸溢出 stage。
      // 与已有 ratio 分支一致：设 frame 尺寸 + fit() 算新 cols/rows + applyTerminalFrameSize 跟随。
      const frameW = cell.w * inst.term.cols;
      const frameH = cell.h * inst.term.rows;
      const ratio = computeFrameRatio(frameW, frameH, contentW, contentH);
      setActiveSessionFrameRatio(ratio);
      debug('terminal', 'applySessionFrameRatio adaptive INIT sid=%s ratio=%.3f (frame=%dx%d stage=%dx%d)',
            sid, ratio, Math.round(frameW), Math.round(frameH),
            Math.round(contentW), Math.round(contentH));
      // 用算出的 ratio 设 frame 尺寸 + fit()，确保 frame ≤ stage content（防溢出）
      const targetW = Math.floor(contentW * ratio);
      const targetH = Math.floor(contentH * ratio);
      const frame = $('terminal-frame');
      if (frame) {
        frame.style.width = targetW + 'px';
        frame.style.height = targetH + 'px';
        frame.style.maxWidth = '';
        frame.style.maxHeight = '';
      }
      try { fit.fit(); } catch (e) { console.error('fit failed', e); }
      requestAnimationFrame(() => { try { applyTerminalFrameSize(sid); } catch (_) {} });
      return true;
    }

    // 已有 ratio：设 frame 尺寸 = stage × ratio，fit() 基于 frame 尺寸算 cols/rows
    const targetW = Math.floor(contentW * savedRatio);
    const targetH = Math.floor(contentH * savedRatio);
    const frame = $('terminal-frame');
    if (frame) {
      frame.style.width = targetW + 'px';
      frame.style.height = targetH + 'px';
      frame.style.maxWidth = '';
      frame.style.maxHeight = '';
    }
    try { fit.fit(); } catch (e) { console.error('fit failed', e); }
    // fit() 触发 onResize → applyTerminalFrameSize（rAF）
    requestAnimationFrame(() => { try { applyTerminalFrameSize(sid); } catch (_) {} });
    debug('terminal', 'applySessionFrameRatio adaptive sid=%s ratio=%.3f frame=%dx%d → fit %dx%d',
          sid, savedRatio, targetW, targetH, inst.term.cols, inst.term.rows);
    return true;
  }

  // ── 非 adaptive 模式：按 ratio 反算字号 ──
  const currentFontSize = getSessionFontSize(sid);

  if (savedRatio == null) {
    // 首次打开：用当前字号渲染后的 frame 尺寸反算 ratio 并保存。
    // 防溢出：若 frame > stage（如守护进程返回 132 列但 stage 很窄），
    // ratio 会被 clamp 到 1.0，此时必须按 ratio 反算字号并应用，
    // 否则首次渲染时 frame 溢出 stage。
    const frameW = cell.w * inst.term.cols;
    const frameH = cell.h * inst.term.rows;
    const ratio = computeFrameRatio(frameW, frameH, contentW, contentH);
    setActiveSessionFrameRatio(ratio);

    // frameW > contentW || frameH > contentH
    const overflowed = frameW > contentW + 1 || frameH > contentH + 1;
    if (overflowed) {
      // frame 超出 stage，按 ratio 反算字号并应用（防溢出）
      const targetFontSize = computeFontSizeFromRatio(
        contentW, contentH, inst.term.cols, inst.term.rows, ratio,
        cell.w, cell.h, currentFontSize
      );
      if (targetFontSize !== currentFontSize) {
        setSessionFontSize(sid, targetFontSize);
        applyTerminalFontSize(sid);
        debug('terminal', 'applySessionFrameRatio sid=%s INIT OVERFLOW fontSize %d → %d (ratio=%.3f frame=%dx%d stage=%dx%d)',
              sid, currentFontSize, targetFontSize, ratio,
              Math.round(frameW), Math.round(frameH),
              Math.round(contentW), Math.round(contentH));
        return true;
      }
    }

    debug('terminal', 'applySessionFrameRatio sid=%s INIT ratio=%.3f (fontSize=%d frame=%dx%d stage=%dx%d)',
          sid, ratio, currentFontSize,
          Math.round(frameW), Math.round(frameH),
          Math.round(contentW), Math.round(contentH));
    requestAnimationFrame(() => applyTerminalFrameSize(sid));
    return false;
  }

  // 已有 ratio：根据 stage 当前尺寸 + ratio 反算字号
  const targetFontSize = computeFontSizeFromRatio(
    contentW, contentH, inst.term.cols, inst.term.rows, savedRatio,
    cell.w, cell.h, currentFontSize
  );

  if (targetFontSize !== currentFontSize) {
    setSessionFontSize(sid, targetFontSize);
    applyTerminalFontSize(sid);
    debug('terminal', 'applySessionFrameRatio sid=%s fontSize %d → %d (ratio=%.3f stage=%dx%d cols=%dx%d cell=%.1fx%.1f)',
          sid, currentFontSize, targetFontSize, savedRatio,
          Math.round(contentW), Math.round(contentH),
          inst.term.cols, inst.term.rows, cell.w, cell.h);
    return true;
  }

  // 字号未变，仅触发 frame 跟随
  requestAnimationFrame(() => applyTerminalFrameSize(sid));
  return false;
}

/**
 * 读取当前活动会话的 stage 内容区尺寸与 cell 尺寸。
 * 多处缩放逻辑共用，提取为内部函数。
 * @returns {{contentW:number, contentH:number, cellW:number, cellH:number, inst:object}|null}
 */
function getActiveStageAndCell() {
  const sid = state.activeTab;
  if (!sid) return null;
  const inst = state.termInstances[sid];
  if (!inst || !inst.term) return null;
  const stage = $('terminal-stage');
  if (!stage) return null;
  const stageStyle = getComputedStyle(stage);
  const padL = parseFloat(stageStyle.paddingLeft) || 0;
  const padR = parseFloat(stageStyle.paddingRight) || 0;
  const padT = parseFloat(stageStyle.paddingTop) || 0;
  const padB = parseFloat(stageStyle.paddingBottom) || 0;
  const contentW = stage.clientWidth - padL - padR;
  const contentH = stage.clientHeight - padT - padB;
  if (!contentW || !contentH) return null;
  const cell = getTerminalCellSize(inst.term);
  if (!cell.w || !cell.h) return null;
  return { contentW, contentH, cellW: cell.w, cellH: cell.h, inst };
}

/**
 * 调整当前活动会话的缩放。
 *
 * 统一缩放入口：Ctrl+滚轮 / 触摸捏合 / Ctrl+± 都调用本函数。
 *
 * 行为（所有模式统一）：调整 frameRatio → 按 ratio 反算字号 → 应用字号（cols/rows 不变）。
 *
 * 关键区分（"自适应的是比例"）：
 *   - adaptive 模式的"自适应 stage 宽高比"由 applySessionFrameRatio 在切标签 / stage 变化时
 *     通过"设 frame 尺寸 + fit()"完成，cols/rows 跟着 fit() 变；
 *   - Ctrl+滚轮只调整框占 stage 的真实大小（通过字号），**不改 cols/rows**。
 *   因此 adaptive 与非 adaptive 模式下 Ctrl+滚轮行为一致。
 *
 * @param {number} deltaRatio ratio 增量（正=增大，负=减小）
 * @returns {boolean} 是否实际改变了尺寸
 */
export function zoomActiveSession(deltaRatio) {
  const sid = state.activeTab;
  if (!sid) return false;
  const s = state.sessions[sid];
  if (!s || !s.uid) return false;
  if (!deltaRatio) return false;

  const ctx = getActiveStageAndCell();
  if (!ctx) return false;
  const { contentW, contentH, cellW, cellH, inst } = ctx;

  // 读当前 ratio（未设置则用当前渲染状态反算作为基准）
  let baseRatio = getSessionFrameRatio(s.uid);
  if (baseRatio == null) {
    const frameW = cellW * inst.term.cols;
    const frameH = cellH * inst.term.rows;
    baseRatio = computeFrameRatio(frameW, frameH, contentW, contentH);
  }

  const nextRatio = Math.max(FRAME_RATIO_MIN, Math.min(FRAME_RATIO_MAX, baseRatio + deltaRatio));
  if (Math.abs(nextRatio - baseRatio) < 1e-4) return false;

  // 保存 ratio（持久化）
  setActiveSessionFrameRatio(nextRatio);

  // 所有模式统一按 ratio 反算字号（cols/rows 不变）。
  // adaptive 模式的"自适应 stage 宽高比"由 applySessionFrameRatio 在切标签/stage 变化时通过 fit() 完成，
  // Ctrl+滚轮只调整框占 stage 的真实大小（通过字号），不改 cols/rows。
  const currentFontSize = getSessionFontSize(sid);
  const targetFontSize = computeFontSizeFromRatio(
    contentW, contentH, inst.term.cols, inst.term.rows, nextRatio,
    cellW, cellH, currentFontSize
  );
  setSessionFontSize(sid, targetFontSize);
  applyTerminalFontSize(sid);
  debug('terminal', 'zoomActiveSession sid=%s mode=%s ratio %.3f → %.3f fontSize %d → %d (cols/rows unchanged)',
        sid, getSessionSizeConfigBySid(sid).mode, baseRatio, nextRatio, currentFontSize, targetFontSize);
  return true;
}

/**
 * 重置当前活动会话的缩放（Ctrl+0）。
 *
 * 行为（所有模式统一）：字号重置为 DEFAULT_FONT_SIZE，等渲染完成后按新 frame 反算 ratio 保存。
 * cols/rows 不变（与 zoomActiveSession 一致：Ctrl+滚轮类操作不改 cols/rows）。
 *
 * @returns {boolean} 是否执行了重置
 */
export function resetActiveSessionZoom() {
  const sid = state.activeTab;
  if (!sid) return false;
  const inst = state.termInstances[sid];
  if (!inst || !inst.term) return false;
  const s = state.sessions[sid];
  if (!s || !s.uid) return false;

  // 所有模式统一字号回默认 + 反算 ratio（cols/rows 不变）
  setSessionFontSize(sid, DEFAULT_FONT_SIZE);
  applyTerminalFontSize(sid);
  requestAnimationFrame(() => {
    const ctx = getActiveStageAndCell();
    if (!ctx) return;
    const { contentW, contentH, cellW, cellH, inst } = ctx;
    const frameW = cellW * inst.term.cols;
    const frameH = cellH * inst.term.rows;
    const ratio = computeFrameRatio(frameW, frameH, contentW, contentH);
    setActiveSessionFrameRatio(ratio);
    debug('terminal', 'resetActiveSessionZoom sid=%s mode=%s → fontSize=%d ratio=%.3f (cols/rows unchanged)',
          sid, getSessionSizeConfigBySid(sid).mode, DEFAULT_FONT_SIZE, ratio);
  });
  return true;
}

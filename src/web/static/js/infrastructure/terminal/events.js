/**
 * 终端基础设施：事件绑定（焦点、鼠标、滚轮、触摸、右键菜单）
 *
 * v9.2: Ctrl+滚轮 / 触摸捏合调用 zoomActiveSession 调整 frameRatio
 *       所有模式统一：按 ratio 反算字号（cols/rows 不变）
 *       （adaptive 的"自适应 stage 宽高比"由 applySessionFrameRatio 在切标签/stage 变化时通过 fit() 完成）
 */

import { state } from '../../domain/state.js';
import { debug } from '../../domain/logger.js';
import { $, showToast } from '../domUtils.js';
import { wsSend } from '../wsClient.js';
import { zoomActiveSession } from './scale.js';
import { getTerminalCellSize } from './shared.js';
import { canSendVtMouseInput, shouldSendAlternateScroll } from './mouseMode.js';
import { shouldTrackFocus } from '../rimeManager.js';
import { restartCursorBlinkIfNeeded, logCursorState } from './cursorDebug.js';
import { FRAME_RATIO_STEP } from '../../domain/constants.js';
import { doPaste } from './input.js';

const WHEEL_DELTA = 120;
const WHEEL_LINES = 3;

/**
 * 检查当前活动会话的框是否已撑满 stage（再增大就会超出）。
 *
 * v9 重构：统一基于"当前 frame 实际尺寸是否已达到 stage 内容区尺寸"判断。
 * 框宽高比固定（由 cols/rows 决定），撑满任一方向后再增大都会导致该方向超出。
 * 适用于所有模式（adaptive 和非 adaptive）。
 *
 * 用于 Ctrl+滚轮上滚 / 双指捏合放大时阻止继续增大：
 * 用户需求"框放到最大时，Ctrl+滚轮上滚终端不应继续变大"。
 *
 * @returns {boolean} true 表示已撑满，应阻止继续增大
 */
export function isFrameAtMaxSize() {
  const sid = state.activeTab;
  if (!sid) return false;
  const inst = state.termInstances[sid];
  if (!inst || !inst.term) return false;
  const s = state.sessions[sid];
  if (!s || !s.uid) return false;

  // 读 stage 内容区尺寸
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

  // 读当前 cell 尺寸
  const cell = getTerminalCellSize(inst.term);
  let cellW = cell.w, cellH = cell.h;
  if (!cellW || !cellH) {
    // 回退到 .xterm-screen DOM 尺寸
    const termEl = inst.term.element
      ? inst.term.element.querySelector('.xterm-screen')
      : null;
    if (termEl) {
      const rect = termEl.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        cellW = rect.width / inst.term.cols;
        cellH = rect.height / inst.term.rows;
      }
    }
  }
  if (!cellW || !cellH) {
    debug('scroll', 'isFrameAtMaxSize: cell unavailable, allow zoom');
    return false;
  }

  // 通用判断：当前 frame 尺寸是否已撑满 stage（任一方向达到 stage 尺寸）
  // 撑满后再增大字号/ratio 都会导致超出，应阻止
  const frameW = cellW * inst.term.cols;
  const frameH = cellH * inst.term.rows;
  // 留 1px 容差避免浮点误差
  const atMaxW = frameW >= contentW - 1;
  const atMaxH = frameH >= contentH - 1;
  // 框宽高比固定，撑满任一方向即视为已撑满（再增大会导致该方向超出）
  const willExceed = atMaxW || atMaxH;
  debug('scroll', 'isFrameAtMaxSize: frame=%dx%d stage=%dx%d → %s',
        Math.round(frameW), Math.round(frameH),
        Math.round(contentW), Math.round(contentH), willExceed ? 'BLOCK' : 'allow');
  return willExceed;
}

/**
 * 从鼠标事件计算终端逻辑坐标 (col, row)。
 * v3 起不再有 CSS transform scale，鼠标坐标直接对应 xterm 内部逻辑坐标。
 */
function getTerminalCellFromEvent(term, e) {
  const el = term.element;
  if (!el) return null;
  const rect = el.getBoundingClientRect();
  const cell = getTerminalCellSize(term);
  if (!cell.w || !cell.h) return null;
  let col = Math.floor((e.clientX - rect.left) / cell.w);
  let row = Math.floor((e.clientY - rect.top) / cell.h);
  col = Math.max(0, Math.min(term.cols - 1, col));
  row = Math.max(0, Math.min(term.rows - 1, row));
  return { col, row };
}

function buildSGRMouse(button, col, row, isRelease, e) {
  let b = button;
  if (e && e.shiftKey) b += 4;
  if (e && e.altKey) b += 8;
  if (e && e.ctrlKey) b += 16;
  const c = col + 1;
  const r = row + 1;
  const suffix = isRelease ? 'm' : 'M';
  return '\x1b[<' + b + ';' + c + ';' + r + suffix;
}

function sendSGRMouse(sid, seq) {
  const s = state.sessions[sid];
  if (!s || !s.running || s.closing) {
    debug('mouse', 'SGR dropped: sid=%s running=%s closing=%s', sid, s && s.running, s && s.closing);
    return;
  }
  debug('mouse', 'SGR send: sid=%s seq=%s', sid, JSON.stringify(seq));
  const dbg = document.getElementById('terminal-mouse-debug');
  if (dbg) dbg.textContent = seq;
  wsSend({ type: 'input', session_id: sid, data: seq });
}

function sendVtWheelEvent(sid, inst, term, e) {
  const isHorizontal = Math.abs(e.deltaX) > Math.abs(e.deltaY);
  const delta = isHorizontal ? e.deltaX : e.deltaY;
  inst._vtWheelAccum += delta;
  if (Math.abs(inst._vtWheelAccum) < WHEEL_DELTA) {
    // 已消费事件，但尚未达到发送阈值（平滑滚轮累计）
    return true;
  }
  const direction = inst._vtWheelAccum > 0 ? 1 : -1; // 正 = 向下/向右
  inst._vtWheelAccum -= direction * WHEEL_DELTA;

  if (shouldSendAlternateScroll(inst, e)) {
    // alternate scroll：垂直滚轮映射为上下箭头；水平暂时忽略
    if (!isHorizontal) {
      const seq = direction < 0 ? '\x1b[A' : '\x1b[B';
      debug('mouse', 'alternate scroll seq=%s deltaY=%d', JSON.stringify(seq), e.deltaY);
      sendSGRMouse(sid, seq);
    }
  } else {
    const cell = getTerminalCellFromEvent(term, e);
    if (!cell) return true;
    // SGR 编码：64 = wheel up, 65 = wheel down
    // WT 行为：滚轮事件只有 press（M），没有 release（m）
    const button = direction < 0 ? 64 : 65;
    const seq = buildSGRMouse(button, cell.col, cell.row, false, e);
    debug('mouse', 'wheel SGR button=%d col=%d row=%d seq=%s', button, cell.col, cell.row, JSON.stringify(seq));
    sendSGRMouse(sid, seq);
  }
  return true;
}

function scrollViewportByWheel(term, inst, e) {
  const cell = getTerminalCellSize(term);
  const rowHeight = cell.h;
  if (!rowHeight) return;
  inst._wheelAccum += e.deltaY;
  const lines = Math.round(inst._wheelAccum / rowHeight);
  if (lines !== 0) {
    try {
      term.scrollLines(lines);
    } catch (_) {}
    inst._wheelAccum -= lines * rowHeight;
    debug('scroll', 'wheel scroll lines=%d remainder=%d deltaY=%d', lines, inst._wheelAccum, e.deltaY);
  }
}

export function bindTerminalEvents(term, inst, sid) {
  const div = inst.div;
  let mouseMoveThrottle = 0;
  const _pressedButtonKey = '_pressedMouseButton';

  function updateFocusBorder(focused) {
    const frame = $('terminal-frame');
    if (!frame) return;
    if (focused) frame.classList.add('focused');
    else frame.classList.remove('focused');
  }

  function sendFocusVT(focused) {
    const s = state.sessions[sid];
    if (!s || s.running === false || s.closing) return;
    if (!shouldTrackFocus(sid)) {
      debug('focus', 'sendFocusVT skipped: keyboard+mouse disabled sid=%s', sid);
      return;
    }
    const seq = focused ? '\x1b[I' : '\x1b[O';
    wsSend({ type: 'input', session_id: sid, data: seq });
    debug('focus', 'sendFocusVT: focused=%s seq=%r sid=%s', focused, JSON.stringify(seq), sid);
  }

  div.addEventListener('focusin', () => {
    const s = state.sessions[sid];
    if (s && s.history) return;
    if (!inst._focused) {
      inst._focused = true;
      updateFocusBorder(true);
      sendFocusVT(true);
    }
    // 终端重新获得焦点时强制刷新，避免切标签/切窗口后画布定格
    try { inst.term.refresh(0, inst.term.rows - 1); } catch (_) {}
    restartCursorBlinkIfNeeded(sid);
    logCursorState(sid);
  });

  div.addEventListener('focusout', () => {
    if (!inst._focused) return;
    inst._focused = false;
    updateFocusBorder(false);
    sendFocusVT(false);
    logCursorState(sid);
  });

  div.addEventListener('mousedown', e => {
    const s = state.sessions[sid];
    if (s && s.history) {
      e.preventDefault();
      return;
    }

    term.focus();

    if (inst.appMouseMode && !e.shiftKey) {
      e.preventDefault();
      e.stopPropagation();
      if (canSendVtMouseInput(inst, e)) {
        const cell = getTerminalCellFromEvent(term, e);
        if (!cell) return;
        let button = 0;
        if (e.button === 1) button = 1;
        else if (e.button === 2) button = 2;
        inst[_pressedButtonKey] = button;
        sendSGRMouse(sid, buildSGRMouse(button, cell.col, cell.row, false, e));
      }
    }
  }, true);

  div.addEventListener('mouseup', e => {
    if (inst.appMouseMode && !e.shiftKey) {
      e.preventDefault();
      e.stopPropagation();
    }
    if (!canSendVtMouseInput(inst, e)) return;
    const cell = getTerminalCellFromEvent(term, e);
    const button = inst[_pressedButtonKey] != null ? inst[_pressedButtonKey] : 0;
    inst[_pressedButtonKey] = null;
    if (!cell) return;
    sendSGRMouse(sid, buildSGRMouse(button, cell.col, cell.row, true, e));
  }, true);

  div.addEventListener('mousemove', e => {
    if (!inst._focused || !inst.appMouseMode || e.shiftKey) return;
    const now = performance.now();
    if (now - mouseMoveThrottle < 33) return;
    mouseMoveThrottle = now;

    const buttonPressed = inst[_pressedButtonKey] != null;
    // WT/xterm 语义：1000 不发移动；1002 只在按键按下时发移动；1003 始终发移动。
    const isAnyEvent = inst.appMouseModePs === 1003;
    if (!buttonPressed && !isAnyEvent) return;

    e.preventDefault();
    if (!canSendVtMouseInput(inst, e)) return;
    const cell = getTerminalCellFromEvent(term, e);
    if (!cell) return;
    // WT 行为：
    // - 拖拽（有按键按下）：button = 实际按键(0/1/2) + 0x20(motion)
    // - 纯 hover（无按键，1003 模式）：button = 3（Released），不加 motion 标志
    let button;
    if (buttonPressed) {
      button = 32 + inst[_pressedButtonKey]; // motion + actual button
    } else {
      button = 3; // Released/hover, no motion flag (WT: WM_MOUSEMOVE -> button=3)
    }
    sendSGRMouse(sid, buildSGRMouse(button, cell.col, cell.row, false, e));
  }, true);

  div.addEventListener('wheel', e => {
    const s = state.sessions[sid];
    const isHistory = !!(s && s.history);

    // v9.2: 历史会话允许 Ctrl+滚轮缩放（用户需求），但跳过 VT 鼠标透传（无活动进程）。
    // 普通滚轮在历史会话中允许 scrollback 滚动（下方默认分支处理）。
    if (e.ctrlKey && !e.shiftKey) {
      // v9.2: Ctrl+滚轮 = 调整 frameRatio（框/stage 占比）。
      // 所有模式统一（含历史会话）：按 ratio 反算字号（cols/rows 不变）。
      // （adaptive 的"自适应 stage 宽高比"由 applySessionFrameRatio 在切标签/stage 变化时通过 fit() 完成）
      // 上滚（deltaY<0）= 增大，下滚（deltaY>0）= 减小。
      // 撑满 stage 后阻止继续增大（用户需求"框放到最大时不应继续变大"）。
      e.preventDefault();
      e.stopPropagation();
      const isZoomIn = e.deltaY < 0;
      // 撑满检查：仅在上滚（增大）时阻止
      if (isZoomIn && isFrameAtMaxSize()) {
        debug('scroll', 'Ctrl+wheel zoom skipped: frame at max size sid=%s', sid);
        return;
      }
      const changed = zoomActiveSession(isZoomIn ? FRAME_RATIO_STEP : -FRAME_RATIO_STEP);
      if (changed) {
        debug('scroll', 'Ctrl+wheel zoom sid=%s isZoomIn=%s (history=%s)', sid, isZoomIn, isHistory);
      }
      return;
    }

    // 历史会话：跳过 VT 鼠标/alternate scroll 透传（无活动进程），保留 scrollback 滚动
    if (isHistory) {
      e.preventDefault();
      e.stopPropagation();
      scrollViewportByWheel(term, inst, e);
      return;
    }

    const canSendMouse = canSendVtMouseInput(inst, e);
    const canSendAltScroll = shouldSendAlternateScroll(inst, e);
    debug('mouse', 'wheel decision appMouse=%s(decset=%s,daemon=%s) altScroll=%s altBuffer=%s shift=%s',
          inst.appMouseMode, inst.appMouseModeDecset, inst.appMouseModeDaemon,
          inst.appAlternateScroll, inst.appAlternateBuffer, e.shiftKey);
    if (canSendMouse || canSendAltScroll) {
      const handled = sendVtWheelEvent(sid, inst, term, e);
      if (handled) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
    }

    // 默认：滚动视图（不再依赖 xterm.js 内部滚轮处理，保证滚动条/滚轮可用）
    e.preventDefault();
    e.stopPropagation();
    scrollViewportByWheel(term, inst, e);
  }, { capture: true, passive: false });

  div.addEventListener('contextmenu', e => {
    // 触摸操作后浏览器会合成 contextmenu 事件，已被触摸逻辑处理，需抑制以避免重复粘贴
    if (suppressContextMenu) {
      e.preventDefault();
      return;
    }
    const s = state.sessions[sid];
    if (s && s.history) {
      e.preventDefault();
      return;
    }
    // 应用鼠标追踪模式下，右键作为 VT 鼠标事件发送，禁止浏览器右键菜单。
    // 按住 Shift 时抑制鼠标模式，可正常调出复制/粘贴菜单。
    if (inst.appMouseMode && !e.shiftKey) {
      debug('mouse', 'contextmenu: mouse mode active, passthrough');
      e.preventDefault();
      return;
    }
    e.preventDefault();
    const selection = term.getSelection();
    if (selection) {
      debug('paste', 'right-click copy');
      navigator.clipboard.writeText(selection).catch(err => {
        showToast('复制失败：请允许网站的剪贴板权限', 'error');
        debug('paste', 'right-click copy failed: %s', err && err.message);
      });
      term.clearSelection();
    } else {
      debug('paste', 'right-click paste');
      doPaste(sid);
    }
  });

  // ═══ Touch support (aligned with WT TouchPressed/TouchMoved/TouchReleased) ═══
  // 交互逻辑：
  //   - 直接拖动（< 400ms 即移动）= 上下滚动（半行阈值）
  //   - 长按（400ms）+ 拖动 = 文本选择（web 端用 term.select；VT 鼠标模式透传 SGR）
  //   - 长按但不拖动 = 粘贴
  //   - 轻触（< 300ms，< 10px）= 获取焦点
  //   - 双指捏合 = 缩放（不改变聚焦状态）
  let touchStartX = 0, touchStartY = 0, touchStartTime = 0;
  let pinchInitialDist = 0;
  let pinchActive = false;
  // 长按选择相关状态
  let longPressTimer = null;
  let selectionMode = null;       // null | 'web' | 'vt'
  let selectionStartCell = null;  // { col, row }
  let selectionHasDragged = false;
  // 抑制触摸后浏览器合成的 contextmenu 事件，避免与长按逻辑重复触发粘贴
  let suppressContextMenu = false;

  function touchDist(t1, t2) {
    const dx = t1.clientX - t2.clientX;
    const dy = t1.clientY - t2.clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }

  function clearLongPressTimer() {
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    }
  }

  function exitSelectionMode() {
    selectionMode = null;
    selectionStartCell = null;
    selectionHasDragged = false;
  }

  // 使用 xterm.js select() API 更新 web 端选择区域
  function updateWebSelection(endCell) {
    if (!selectionStartCell) return;
    let sc = selectionStartCell.col, sr = selectionStartCell.row;
    let ec = endCell.col, er = endCell.row;
    // 确保起点在终点之前（从左上到右下）
    if (er < sr || (er === sr && ec < sc)) {
      [sc, sr, ec, er] = [ec, er, sc, sr];
    }
    const length = (term.cols * (er - sr)) + (ec - sc);
    if (length > 0) {
      try { term.select(sc, sr, length); } catch (_) {}
    }
  }

  function hideCopyButton() {
    div.querySelectorAll('.term-copy-btn').forEach(b => b.remove());
  }

  function showCopyButton(clientX, clientY) {
    hideCopyButton();
    const sel = term.getSelection();
    if (!sel) return;
    const btn = document.createElement('button');
    btn.className = 'term-copy-btn';
    btn.type = 'button';
    btn.textContent = '复制';
    // 使用 touchstart 而非 click：移动端 click 在 touchstart 之后触发，
    // 而终端 div 的 touchstart 会先移除按钮。stopPropagation 阻止冒泡到终端。
    btn.addEventListener('touchstart', ev => {
      ev.stopPropagation();
      ev.preventDefault();
      copySelection(term);
      hideCopyButton();
    }, { passive: false });
    // 桌面端点击也能复制（方便测试）
    btn.addEventListener('click', ev => {
      ev.stopPropagation();
      ev.preventDefault();
      copySelection(term);
      hideCopyButton();
    });
    const rect = div.getBoundingClientRect();
    let bx = clientX - rect.left + 12;
    let by = clientY - rect.top - 36;
    bx = Math.max(4, Math.min(bx, rect.width - 56));
    by = Math.max(4, Math.min(by, rect.height - 30));
    btn.style.left = bx + 'px';
    btn.style.top = by + 'px';
    div.appendChild(btn);
  }

  div.addEventListener('touchstart', e => {
    if (e.touches.length === 1) {
      const t = e.touches[0];
      touchStartX = t.clientX;
      touchStartY = t.clientY;
      touchStartTime = Date.now();
      inst._touchAnchor = { x: t.clientX, y: t.clientY };
      exitSelectionMode();
      hideCopyButton();
      // 抑制浏览器在长按后合成的 contextmenu 事件
      suppressContextMenu = true;
      // 启动 400ms 长按计时器
      clearLongPressTimer();
      longPressTimer = setTimeout(() => {
        longPressTimer = null;
        const fakeEvent = { shiftKey: false, altKey: false, ctrlKey: false };
        const cell = getTerminalCellFromEvent(term, { clientX: touchStartX, clientY: touchStartY });
        if (!cell) return;
        if (canSendVtMouseInput(inst, fakeEvent)) {
          // VT 鼠标模式：透传给终端程序处理选择
          selectionMode = 'vt';
          selectionStartCell = cell;
          selectionHasDragged = false;
          sendSGRMouse(sid, buildSGRMouse(0, cell.col, cell.row, false, fakeEvent));
          if (navigator.vibrate) navigator.vibrate(50);
          debug('touch', 'long-press → vt selection mode cell=(%s,%s)', cell.col, cell.row);
        } else {
          // web 端选择模式
          selectionMode = 'web';
          selectionStartCell = cell;
          selectionHasDragged = false;
          if (!inst._focused) term.focus();
          if (navigator.vibrate) navigator.vibrate(50);
          debug('touch', 'long-press → web selection mode cell=(%s,%s)', cell.col, cell.row);
        }
      }, 400);
      debug('touch', 'touchstart: anchor=(%s,%s) focused=%s', t.clientX.toFixed(0), t.clientY.toFixed(0), inst._focused);
    } else if (e.touches.length === 2) {
      clearLongPressTimer();
      // 双指捏合开始时，若处于 VT 选择模式则发送 release 通知程序
      if (selectionMode === 'vt' && selectionStartCell) {
        sendSGRMouse(sid, buildSGRMouse(0, selectionStartCell.col, selectionStartCell.row, true, { shiftKey: false }));
      }
      exitSelectionMode();
      pinchInitialDist = touchDist(e.touches[0], e.touches[1]);
      // v9.2: 捏合缩放改为调整 frameRatio（所有模式统一按 ratio 反算字号，cols/rows 不变），
      //     通过 zoomActiveSession 统一入口。记录初始 dist 供 touchmove 算比例。
      pinchActive = true;
      inst._touchAnchor = null;
      debug('touch', 'pinch start: dist=%s', pinchInitialDist.toFixed(0));
    }
  }, { passive: true });

  div.addEventListener('touchmove', e => {
    if (e.touches.length === 1) {
      const t = e.touches[0];
      if (selectionMode === 'web') {
        e.preventDefault();
        const cell = getTerminalCellFromEvent(term, t);
        if (cell) {
          if (cell.col !== selectionStartCell.col || cell.row !== selectionStartCell.row) {
            selectionHasDragged = true;
          }
          updateWebSelection(cell);
        }
      } else if (selectionMode === 'vt') {
        e.preventDefault();
        const cell = getTerminalCellFromEvent(term, t);
        if (cell) {
          selectionHasDragged = true;
          // SGR motion: 32 = motion 标志 + 0 (left button)
          sendSGRMouse(sid, buildSGRMouse(32, cell.col, cell.row, false, { shiftKey: false }));
        }
      } else if (inst._touchAnchor) {
        // 滚动模式：移动超过 10px 即取消长按计时器
        const moveDx = t.clientX - touchStartX;
        const moveDy = t.clientY - touchStartY;
        if (Math.sqrt(moveDx * moveDx + moveDy * moveDy) > 10) {
          clearLongPressTimer();
        }
        const cellSize = getTerminalCellSize(term);
        const rowHeight = cellSize.h;
        const halfRow = rowHeight / 2;
        const dy = t.clientY - inst._touchAnchor.y;
        if (Math.abs(dy) > halfRow) {
          e.preventDefault();
          const numRows = Math.round(-dy / rowHeight);
          if (numRows !== 0) {
            const fakeEvent = { shiftKey: false };
            if (shouldSendAlternateScroll(inst, fakeEvent)) {
              // Alternate scroll：拖动转换为上下箭头（与桌面端滚轮一致）
              // touchmove 频率高（~60fps），每次只发 1 个事件即可，
              // 不需要按 numRows 倍数发送（否则 VT 滚轮事件像机关枪一样密集）
              const seq = numRows > 0 ? '\x1b[B' : '\x1b[A';
              sendSGRMouse(sid, seq);
              debug('touch', 'alt-scroll touch: numRows=%s dy=%s', numRows, dy.toFixed(1));
            } else if (canSendVtMouseInput(inst, fakeEvent)) {
              // SGR 鼠标滚轮：拖动转换为 VT 滚轮事件
              // WT 行为：滚轮只有 press（M），没有 release（m）
              // touchmove 频率高，每次只发 1 个事件
              const cell = getTerminalCellFromEvent(term, t);
              if (cell) {
                const button = numRows > 0 ? 65 : 64; // 65=down, 64=up
                sendSGRMouse(sid, buildSGRMouse(button, cell.col, cell.row, false, fakeEvent));
                debug('touch', 'vt touch scroll: numRows=%s dy=%s cell=(%s,%s)', numRows, dy.toFixed(1), cell.col, cell.row);
              }
            } else {
              // 非 VT 模式：普通视口滚动
              term.scrollLines(numRows);
              debug('touch', 'touchmove scroll: numRows=%s dy=%s', numRows, dy.toFixed(1));
            }
            inst._touchAnchor = { x: t.clientX, y: t.clientY };
          }
        }
      }
    } else if (e.touches.length === 2 && pinchInitialDist > 0) {
      // v9.2: 双指捏合 = 调整当前会话的 frameRatio（所有模式统一按 ratio 反算字号，cols/rows 不变）。
      // 捏合比例 → ratio 增量，通过 zoomActiveSession 统一入口。
      e.preventDefault();
      const dist = touchDist(e.touches[0], e.touches[1]);
      const pinchRatio = dist / pinchInitialDist;
      // 捏合比例 > 1.15 放大，< 0.85 缩小；与 WT 触摸缩放档位感一致
      const isZoomIn = pinchRatio > 1.15;
      const isZoomOut = pinchRatio < 0.85;
      if (!isZoomIn && !isZoomOut) return;
      // 撑满检查：仅在放大时阻止
      if (isZoomIn && isFrameAtMaxSize()) {
        debug('touch', 'pinch zoom skipped: frame at max size sid=%s', sid);
        return;
      }
      const changed = zoomActiveSession(isZoomIn ? FRAME_RATIO_STEP : -FRAME_RATIO_STEP);
      if (changed) {
        debug('touch', 'pinch zoom sid=%s pinchRatio=%.2f isZoomIn=%s',
              sid, pinchRatio, isZoomIn);
        // 重置 pinch 起点为当前 dist，避免连续触发（每档一次）
        pinchInitialDist = dist;
      }
    }
  }, { passive: false });

  div.addEventListener('touchend', e => {
    if (e.touches.length > 0) {
      if (e.touches.length === 1) {
        pinchInitialDist = 0;
        const t = e.touches[0];
        inst._touchAnchor = { x: t.clientX, y: t.clientY };
      }
      return;
    }
    const wasPinch = pinchActive;
    const lastTouch = e.changedTouches[0];
    const dx = (lastTouch?.clientX || 0) - touchStartX;
    const dy = (lastTouch?.clientY || 0) - touchStartY;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const duration = Date.now() - touchStartTime;

    clearLongPressTimer();
    const wasInSelection = selectionMode !== null;

    // 选择模式结束处理
    if (selectionMode === 'web') {
      if (selectionHasDragged) {
        // 有选择区域：显示复制按钮
        if (lastTouch) showCopyButton(lastTouch.clientX, lastTouch.clientY);
        debug('touch', 'web selection done → show copy button');
      } else {
        // 长按但未拖动：仅清除选择，不触发粘贴（粘贴统一由右键菜单/按钮处理）
        try { term.clearSelection(); } catch (_) {}
        debug('touch', 'long-press without drag → no action');
      }
    } else if (selectionMode === 'vt') {
      // VT 鼠标模式：发送 release，由程序自行处理选择/点击
      const cell = lastTouch
        ? getTerminalCellFromEvent(term, lastTouch)
        : selectionStartCell;
      if (cell) {
        sendSGRMouse(sid, buildSGRMouse(0, cell.col, cell.row, true, { shiftKey: false }));
      }
      debug('touch', 'vt selection done (dragged=%s)', selectionHasDragged);
    }

    exitSelectionMode();
    pinchInitialDist = 0;
    pinchActive = false;

    // 轻触 = 获取焦点（仅在非捏合、非选择模式、快速且小位移时）
    if (!wasPinch && !wasInSelection && dist < 10 && duration < 300) {
      term.focus();
      debug('touch', 'tap→focus');
    }
    inst._touchAnchor = null;

    // 800ms 后恢复 contextmenu，覆盖浏览器合成 contextmenu 的时间窗口
    setTimeout(() => { suppressContextMenu = false; }, 800);
  }, { passive: true });
}

/**
 * 表现层：全局事件控制器
 *
 * 负责绑定用户交互事件、快捷键、上下文菜单、窗口事件等。
 * 通过调用视图函数和基础设施服务完成具体行为。
 */

import { state, getSessionSizeConfigBySid } from '../../domain/state.js';
import { $, showConfirm, hideConfirm, showToast } from '../../infrastructure/domUtils.js';
import { wsSend } from '../../infrastructure/wsClient.js';
import {
  applyTerminalFrameSize,
  applyTerminalSize,
  replayPending,
  toggleMouseInputOverride,
  applySessionFrameRatio,
  zoomActiveSession,
  resetActiveSessionZoom,
  isFrameAtMaxSize,
} from '../../infrastructure/terminalAdapter.js';
import {
  toggleHistory,
  showNewSessionDialog,
  submitNewSession,
  hideContextMenu,
  closeTab,
  killSession,
  removeSessionTab,
  updateMouseModeButton,
  updateCommandEmptyState,
  showRestartDialog,
  hideRestartDialog,
  checkRestartSidConflict,
  submitRestartSession,
} from '../views/ui.js';
import { hideDetailDialog } from '../views/detail.js';
import { toggleSizeDropdown, updateSizeStatusDisplay } from '../views/sizeSelector.js';
import {
  DEFAULT_SIDEBAR_WIDTH,
  MIN_SIDEBAR_WIDTH,
  MAX_SIDEBAR_WIDTH,
  FRAME_RATIO_STEP,
} from '../../domain/constants.js';
import { getHandlerBySid } from '../views/sessionHandlers.js';
import { debug, info } from '../../domain/logger.js';
import { applySidebarWidth } from '../../infrastructure/storage.js';
import { cycleMode as rimeCycleMode } from '../../infrastructure/rimeManager.js';
import * as settingsStore from '../../application/settingsStore.js';

export function bindGlobalEvents() {
  $('btn-sidebar-toggle').onclick = () => {
    const sb = $('sidebar');
    const collapsed = sb.classList.toggle('collapsed');
    localStorage.setItem('pty_sidebar_collapsed', String(collapsed));
    debug('ui', 'sidebar toggle → collapsed=%s', collapsed);
    if (!collapsed) {
      applySidebarWidth();
    } else {
      sb.style.width = '';
      sb.style.minWidth = '';
      sb.style.maxWidth = '';
    }
    // sidebar 折叠/展开后由 stage 的 ResizeObserver 自动接管尺寸更新
    // 不再这里手动调用 applyTerminalFrameSize
  };

  initSidebarResize();

  $('btn-new-tab').onclick = showNewSessionDialog;
  if ($('empty-new-btn')) {
    $('empty-new-btn').onclick = showNewSessionDialog;
  }

  // 主题切换：写入 settingsStore，由 app.js 订阅者统一应用 setBodyTheme + applyTheme + rimeOnThemeChange
  // （快捷按钮仅在 dark/light 间切换；system 需在设置面板选择）
  $('btn-theme').onclick = () => {
    const isDark = document.body.dataset.theme === 'dark';
    const nextTheme = isDark ? 'light' : 'dark';
    settingsStore.set('basic.theme', nextTheme);
    debug('ui', 'theme toggle → %s', nextTheme);
  };

  $('btn-ime').onclick = () => {
    debug('ui', 'btn-ime click → cycleMode');
    rimeCycleMode();
  };

  // 鼠标模式改为自动检测（DECSET 1000/1002/1003），按钮仅作状态指示。
  // 点击按钮提示用户：按住 Shift 可临时绕过鼠标模式进行滚动/选择。
  $('btn-mouse-mode').onclick = () => {
    const sid = state.activeTab;
    if (!sid) return;
    const inst = state.termInstances[sid];
    if (!inst || !inst.appMouseMode) return;
    const override = toggleMouseInputOverride(sid);
    updateMouseModeButton(sid);
    showToast(override ? '鼠标输入已开启' : '鼠标输入已关闭，滚轮可正常滚动', 'info');
  };

  $('dialog-cancel').onclick = () => { $('dialog-overlay').style.display = 'none'; };
  $('dialog-ok').onclick = submitNewSession;
  ['form-id', 'form-command', 'form-cwd'].forEach(id => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        submitNewSession();
      }
    });
  });
  // 对话框遮罩点击外部关闭：仅在 mousedown 和 mouseup 都发生在遮罩（非对话框内）时关闭，
  // 避免用户在对话框内按下鼠标、拖到外部松开选择文本时误关闭。
  function bindOverlayDismiss(overlayEl, onDismiss) {
    let mouseDownInside = false;
    overlayEl.addEventListener('mousedown', e => {
      mouseDownInside = !!e.target.closest('.dialog');
    });
    overlayEl.addEventListener('click', e => {
      if (mouseDownInside) return;
      if (e.target === overlayEl) onDismiss();
    });
  }
  bindOverlayDismiss($('dialog-overlay'), () => { $('dialog-overlay').style.display = 'none'; });

  $('confirm-cancel').onclick = hideConfirm;
  $('confirm-ok').onclick = () => {
    if (state.confirmOkCallback) state.confirmOkCallback();
    hideConfirm();
  };
  bindOverlayDismiss($('confirm-overlay'), hideConfirm);

  $('restart-cancel').onclick = hideRestartDialog;
  $('restart-ok').onclick = submitRestartSession;
  $('restart-reassign-sid').onchange = () => {
    const checked = $('restart-reassign-sid').checked;
    $('restart-sid-group').style.display = checked ? 'none' : '';
    if (!checked) {
      $('restart-sid-input').value = state.restartTargetSid || '';
      checkRestartSidConflict();
    }
  };
  $('restart-sid-input').addEventListener('input', checkRestartSidConflict);
  bindOverlayDismiss($('restart-overlay'), hideRestartDialog);
  bindOverlayDismiss($('server-addr-overlay'), () => { $('server-addr-overlay').style.display = 'none'; });

  $('win-min').onclick = () => {
    if (!state.activeTab) return;
    debug('session', 'win-min click → closeTab sid=%s', state.activeTab);
    closeTab(state.activeTab);
    updateMouseModeButton(state.activeTab);
  };
  $('win-max').onclick = () => {
    debug('ui', 'win-max click → fullscreen toggle');
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  };

  // 全屏状态变化时切换最大化/还原图标
  const maximizeIconSvg = '<rect x="2.5" y="2.5" width="11" height="11" rx="1" stroke="currentColor" stroke-width="1.5" fill="none"/>';
  const restoreIconSvg = '<rect x="2.5" y="5" width="8.5" height="8.5" rx="1" stroke="currentColor" stroke-width="1.5" fill="none"/>' +
                         '<rect x="5" y="2.5" width="8.5" height="8.5" rx="1" stroke="currentColor" stroke-width="1.5" fill="none"/>' +
                         '<line x1="5" y1="5" x2="13.5" y2="5" stroke="currentColor" stroke-width="1.5"/>' +
                         '<line x1="5" y1="5" x2="5" y2="13.5" stroke="currentColor" stroke-width="1.5"/>';
  function syncMaximizeIcon() {
    const isFullscreen = !!document.fullscreenElement;
    const icon = $('win-max-icon');
    const btn = $('win-max');
    if (icon) icon.innerHTML = isFullscreen ? restoreIconSvg : maximizeIconSvg;
    if (btn) btn.title = isFullscreen ? '退出全屏' : '全屏';
    debug('ui', 'fullscreenchange → isFullscreen=%s', isFullscreen);
  }
  document.addEventListener('fullscreenchange', syncMaximizeIcon);
  syncMaximizeIcon();
  $('win-close').onclick = () => {
    if (!state.activeTab) return;
    debug('session', 'win-close click → closeTab sid=%s', state.activeTab);
    closeTab(state.activeTab);
    updateMouseModeButton(state.activeTab);
  };

  // 状态栏尺寸项点击：弹出尺寸选择器下拉（支持鼠标与触摸）
  // 历史会话禁用尺寸按钮（固定生前最后尺寸，不允许切换模式），但允许 Ctrl+滚轮缩放
  const statusSize = $('status-size');
  if (statusSize) {
    statusSize.onclick = (e) => {
      e.stopPropagation();
      const sid = state.activeTab;
      const s = sid ? state.sessions[sid] : null;
      if (s && s.history) {
        debug('ui', 'status-size click skipped: history session sid=%s', sid);
        return;
      }
      toggleSizeDropdown();
    };
  }

document.addEventListener('keydown', e => {
	    if (e.ctrlKey && e.shiftKey && e.key === 'T') {
      e.preventDefault();
      debug('key', 'Ctrl+Shift+T → new session dialog');
      showNewSessionDialog();
      return;
    }
    if (e.ctrlKey && e.shiftKey && e.key === 'W') {
      e.preventDefault();
      debug('key', 'Ctrl+Shift+W → close tab sid=%s', state.activeTab);
      if (state.activeTab) closeTab(state.activeTab);
      return;
    }
    if (e.ctrlKey && e.key === 'Tab') {
      e.preventDefault();
      if (state.tabOrder.length < 2) return;
      const idx = state.tabOrder.indexOf(state.activeTab);
      const next = e.shiftKey
        ? state.tabOrder[(idx - 1 + state.tabOrder.length) % state.tabOrder.length]
        : state.tabOrder[(idx + 1) % state.tabOrder.length];
      if (!next) return;
      debug('key', 'Ctrl+Tab → switch tab to %s', next);
      import('../views/ui.js').then(ui => ui.openSessionInTab(next));
      return;
    }
    if (e.ctrlKey && /^Digit[1-9]$/.test(e.code)) {
      e.preventDefault();
      const idx = parseInt(e.code.replace('Digit', ''), 10) - 1;
      if (idx >= 0 && idx < state.tabOrder.length) {
        debug('key', 'Ctrl+%s → switch tab to %s', e.code, state.tabOrder[idx]);
        import('../views/ui.js').then(ui => ui.openSessionInTab(state.tabOrder[idx]));
      }
      return;
    }
    if (e.key === 'F11') {
      e.preventDefault();
      debug('key', 'F11 → fullscreen toggle');
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(() => {});
      } else {
        document.exitFullscreen().catch(() => {});
      }
      return;
    }
    if (e.ctrlKey && (e.key === '=' || e.key === '+' || e.key === '-')) {
      // Ctrl+/-/= 调整 frameRatio
      // 所有模式统一：按 ratio 反算字号（cols/rows 不变）
      // （adaptive 的"自适应 stage 宽高比"由 applySessionFrameRatio 在切标签/stage 变化时通过 fit() 完成）
      e.preventDefault();
      const isZoomIn = (e.key !== '-' && e.key !== '_');
      if (isZoomIn && isFrameAtMaxSize()) {
        debug('key', 'Ctrl+/-= zoom skipped: frame at max size');
        return;
      }
      zoomActiveSession(isZoomIn ? FRAME_RATIO_STEP : -FRAME_RATIO_STEP);
      return;
    }
    if (e.ctrlKey && e.key === '0') {
      // Ctrl+0 重置当前会话缩放
      // 所有模式统一：字号回默认，按渲染反算新 ratio（cols/rows 不变）
      e.preventDefault();
      resetActiveSessionZoom();
      debug('key', 'Ctrl+0 → reset active session zoom');
      return;
    }
    if (e.key === 'Escape') {
      if ($('dialog-overlay').style.display !== 'none') {
        $('dialog-overlay').style.display = 'none';
      }
      if ($('confirm-overlay').style.display !== 'none') {
        hideConfirm();
      }
      if ($('detail-overlay') && $('detail-overlay').style.display !== 'none') {
        hideDetailDialog();
      }
      if (state.historyVisible) toggleHistory(false);
      if (state.sizeSelectorVisible) toggleSizeDropdown(false);
      hideContextMenu();
    }
  });

  document.addEventListener('click', e => {
    if (state.historyVisible && !$('history-dropdown').contains(e.target) && e.target !== $('btn-history') && !e.target.closest('#btn-history')) {
      toggleHistory(false);
    }
    // 点击尺寸选择器外部时关闭下拉
    if (state.sizeSelectorVisible) {
      const dd = $('size-dropdown');
      const sizeEl = $('status-size');
      if (dd && !dd.contains(e.target) && sizeEl && !sizeEl.contains(e.target)) {
        toggleSizeDropdown(false);
      }
    }
    hideContextMenu();
  });

  $('context-menu').addEventListener('click', e => {
    const item = e.target.closest('.context-menu-item');
    if (!item) return;
    const action = item.dataset.action;
    const sid = state.contextMenuTarget;
    const context = state.contextMenuContext;
    hideContextMenu();
    if (!sid) return;
    debug('ui', 'context-menu action=%s sid=%s context=%s', action, sid, context);
    if (action === 'close-tab') {
      closeTab(sid);
    } else if (action === 'detail') {
      wsSend({ type: 'session_detail', session_id: sid });
    } else if (action === 'close-session') {
      const s = state.sessions[sid];
      const body = s && s.running
        ? `会话 "${sid}" 仍在运行，确定要关闭吗？`
        : `确定要关闭会话 "${sid}" 吗？`;
      showConfirm('确认关闭会话', body, () => killSession(sid));
    } else if (action === 'delete-session') {
      const body = `确定要永久删除历史会话 "${sid}" 吗？此操作不可恢复。`;
      showConfirm('确认删除会话', body, () => {
        removeSessionTab(sid, false);
        wsSend({ type: 'delete_history', session_id: sid });
      });
    } else if (action === 'restart-session') {
      showRestartDialog(sid);
    }
  });

  window.addEventListener('wheel', e => {
    // 在终端区域内的 Ctrl+滚轮已在 capture 阶段处理缩放并阻止传播；
    // 这里兜底处理页面其它区域以及未阻止到的 Ctrl+滚轮。
    // 统一调用 zoomActiveSession 调整 frameRatio（所有模式按 ratio 反算字号，cols/rows 不变）。
    if (e.ctrlKey && !e.shiftKey) {
      e.preventDefault();
      const isZoomIn = e.deltaY < 0;
      if (isZoomIn && isFrameAtMaxSize()) {
        debug('scroll', 'global Ctrl+wheel zoom skipped: frame at max size');
        return;
      }
      zoomActiveSession(isZoomIn ? FRAME_RATIO_STEP : -FRAME_RATIO_STEP);
    }
  }, { passive: false });

  // 全局双指捏合缩放：终端区域由其自身 touch 处理器接管；此处理器覆盖页面其他区域。
  // 不改变终端聚焦状态（即使原本未聚焦终端）。
  // 捏合改为调整 frameRatio（所有模式按 ratio 反算字号，cols/rows 不变），通过 zoomActiveSession 统一入口。
  let globalPinchDist = 0;
  function globalTouchDist(t1, t2) {
    const dx = t1.clientX - t2.clientX;
    const dy = t1.clientY - t2.clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }
  function isInsideTerminal(target) {
    return !!(target && target.closest && target.closest('.term-instance'));
  }
  window.addEventListener('touchstart', e => {
    if (e.touches.length !== 2) return;
    // 两指都在终端内时，由终端自身处理器接管，避免重复处理
    if (isInsideTerminal(e.touches[0].target) && isInsideTerminal(e.touches[1].target)) return;
    globalPinchDist = globalTouchDist(e.touches[0], e.touches[1]);
    debug('touch', 'global pinch start: dist=%s', globalPinchDist.toFixed(0));
  }, { passive: true });

  window.addEventListener('touchmove', e => {
    if (e.touches.length !== 2 || globalPinchDist <= 0) return;
    if (isInsideTerminal(e.touches[0].target) && isInsideTerminal(e.touches[1].target)) return;
    e.preventDefault();
    const dist = globalTouchDist(e.touches[0], e.touches[1]);
    const pinchRatio = dist / globalPinchDist;
    const isZoomIn = pinchRatio > 1.15;
    const isZoomOut = pinchRatio < 0.85;
    if (!isZoomIn && !isZoomOut) return;
    if (isZoomIn && isFrameAtMaxSize()) {
      debug('touch', 'global pinch zoom skipped: frame at max size');
      return;
    }
    const changed = zoomActiveSession(isZoomIn ? FRAME_RATIO_STEP : -FRAME_RATIO_STEP);
    if (changed) {
      debug('touch', 'global pinch zoom: pinchRatio=%.2f isZoomIn=%s', pinchRatio, isZoomIn);
      // 重置起点为当前 dist，避免连续触发
      globalPinchDist = dist;
    }
  }, { passive: false });

  window.addEventListener('touchend', e => {
    if (e.touches.length === 0) {
      globalPinchDist = 0;
    }
  }, { passive: true });
  window.addEventListener('touchcancel', () => {
    globalPinchDist = 0;
  }, { passive: true });

  // 统一终端 resize 防抖（trailing debounce 150ms）
  // 覆盖所有导致 stage 尺寸变化的场景：sidebar 折叠/展开（CSS transition 0.2s 动画）、
  // sidebar 拖动、fullscreen 切换、window resize。
  // 痛点：sidebar 折叠动画 200ms 期间 ResizeObserver 每帧触发（~12 次），不防抖会导致
  // adaptive 模式频繁 fit() → 频繁 PTY resize + 画面闪烁。
  // trailing debounce：连续触发期间不执行，最后一次触发后 150ms 执行一次，保证最终状态正确。
  let resizeDebounceTimer = null;
  function scheduleTerminalResize() {
    if (!state.activeTab) return;
    if (resizeDebounceTimer) clearTimeout(resizeDebounceTimer);
    resizeDebounceTimer = setTimeout(() => {
      resizeDebounceTimer = null;
      const sid = state.activeTab;
      if (!sid) return;
      const s = state.sessions[sid];
      const isHistory = !!(s && s.history);
      const cfg = getSessionSizeConfigBySid(sid);
      // 历史会话强制非 adaptive 路径（固定生前 cols/rows，按 frameRatio 反算字号）
      if (cfg.mode === 'adaptive' && !isHistory) {
        // 自适应：stage 变了，按保存的 ratio 设 frame 尺寸 + fit() 同步 cols/rows
        // （adaptive 自适应 stage 宽高比，cols/rows 跟着 fit 变；此处是 applySessionFrameRatio 的 adaptive 分支，与 Ctrl+滚轮不同）
        applyTerminalSize(sid, false);
        applyTerminalFrameSize(sid);
      } else {
        // 非自适应（含历史会话）：按保存的 frameRatio + 新 stage 尺寸重新反算字号（cols/rows 不变）。
        // applySessionFrameRatio 内部会触发 applyTerminalFontSize → applyTerminalFrameSize，
        // 若字号未变则手动调用 applyTerminalFrameSize 更新 frame。
        const changed = applySessionFrameRatio(sid);
        if (!changed) {
          applyTerminalFrameSize(sid);
        }
      }
      updateSizeStatusDisplay();
    }, 150);
  }

  // window resize 走统一防抖
  window.addEventListener('resize', scheduleTerminalResize);

  // ResizeObserver 监听 stage，覆盖 sidebar 动画/拖动、fullscreen 等 stage 尺寸变化场景
  // （window resize 也会间接触发，但 window 监听保留用于额外保障）
  const stageEl = $('terminal-stage');
  if (stageEl && window.ResizeObserver) {
    const stageObserver = new ResizeObserver(scheduleTerminalResize);
    stageObserver.observe(stageEl);
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    const sid = state.activeTab;
    if (!sid) return;
    const inst = state.termInstances[sid];
    if (!inst || !inst.term) return;
    debug('ui', 'visibilitychange visible sid=%s', sid);
    requestAnimationFrame(() => {
      applyTerminalFrameSize(sid);
      // 页面重新可见时不强制向守护进程发送 resize（force=false）：
      // 仅刷新 xterm.js 显示，守护进程 PTY 尺寸未变
      applyTerminalSize(sid, false);
      try { inst.term.refresh(0, inst.term.rows - 1); } catch (_) {}
      replayPending(sid);
    });
  });
}

export function initSidebarResize() {
  const resizer = $('sidebar-resizer');
  const sb = $('sidebar');
  if (!resizer || !sb) return;

  let startX = 0;
  let startWidth = 0;

  function applyResize(dx) {
    let w = startWidth + dx;
    w = Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, w));
    sb.style.width = w + 'px';
    sb.style.minWidth = w + 'px';
    sb.style.maxWidth = MAX_SIDEBAR_WIDTH + 'px';
    // sidebar 拖动时由 stage 的 ResizeObserver 接管尺寸更新
    // 不再这里手动调用 applyTerminalFrameSize，避免重复计算和时序问题
    // ResizeObserver 会在下一帧自动触发 applyTerminalSize + applyTerminalFrameSize
  }

  function finishResize() {
    if (!state.isResizingSidebar) return;
    state.isResizingSidebar = false;
    document.body.classList.remove('sidebar-resizing');
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
    document.removeEventListener('touchmove', onTouchMove);
    document.removeEventListener('touchend', onTouchEnd);
    state.sidebarWidth = parseInt(sb.style.width, 10) || DEFAULT_SIDEBAR_WIDTH;
    localStorage.setItem('pty_sidebar_width', String(state.sidebarWidth));
  }

  function onMouseMove(e) {
    if (!state.isResizingSidebar) return;
    applyResize(e.clientX - startX);
  }

  function onMouseUp() {
    finishResize();
  }

  function onTouchMove(e) {
    if (!state.isResizingSidebar) return;
    e.preventDefault();
    applyResize(e.touches[0].clientX - startX);
  }

  function onTouchEnd() {
    finishResize();
  }

  function startResize(clientX) {
    if (sb.classList.contains('collapsed')) {
      sb.classList.remove('collapsed');
      localStorage.setItem('pty_sidebar_collapsed', 'false');
    }
    state.isResizingSidebar = true;
    document.body.classList.add('sidebar-resizing');
    startX = clientX;
    startWidth = sb.getBoundingClientRect().width;
  }

  resizer.addEventListener('mousedown', e => {
    e.preventDefault();
    startResize(e.clientX);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  });

  resizer.addEventListener('touchstart', e => {
    e.preventDefault();
    startResize(e.touches[0].clientX);
    document.addEventListener('touchmove', onTouchMove, { passive: false });
    document.addEventListener('touchend', onTouchEnd);
  });
}


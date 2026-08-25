/**
 * 终端基础设施：光标调试与闪烁重启
 *
 * 拦截 DECSCUSR / DECSET(12) 等光标相关 VT 序列，并提供详细日志与重启工具。
 */

import { state } from '../../domain/state.js';
import { debug, warn } from '../../domain/logger.js';
import { decodeWriteData } from './shared.js';

// 调试钩子：记录影响光标样式/闪烁的 VT 序列，便于排查光标不闪烁问题。
// 返回 false 表示不拦截，仍由 xterm.js 正常处理。
export function trackCursorSequences(term, uid) {
  try {
    const parser = term.parser;
    if (!parser) {
      warn('cursor', 'trackCursorSequences: term.parser not available sid=%s', uid);
      return;
    }
    debug('cursor', 'trackCursorSequences: registering handlers sid=%s', uid);
    // 辅助 textarea：光标隐藏（程序接管光标）时 IME 预编辑会把 textarea
    // 撑宽（预编辑文本显示），光标在右侧/右下角时溢出容器右边界（"顶破
    // 右边"）——加 class 限制宽度（预编辑不显示，候选窗口仍正常跟随）。
    // DECSET CSI ? Pm h: Ps=25 显示光标
    parser.registerCsiHandler({ final: 'h', prefix: '?' }, params => {
      if (params && params.params && params.params.includes(25)) {
        const ta = term.textarea || (term._core && term._core.textarea);
        if (ta) ta.classList.remove('xterm-cursor-hidden');
      }
      debug('cursor', 'DECSET sid=%s params=%o blink=%s style=%s',
            uid, params, term.options.cursorBlink, term.options.cursorStyle);
      return false;
    });
    // DECRST CSI ? Pm l: Ps=25 隐藏光标
    parser.registerCsiHandler({ final: 'l', prefix: '?' }, params => {
      if (params && params.params && params.params.includes(25)) {
        const ta = term.textarea || (term._core && term._core.textarea);
        if (ta) ta.classList.add('xterm-cursor-hidden');
      }
      debug('cursor', 'DECRST sid=%s params=%o blink=%s style=%s',
            uid, params, term.options.cursorBlink, term.options.cursorStyle);
      return false;
    });
  } catch (e) {
    warn('cursor', 'trackCursorSequences parser register failed: %s', e.message);
  }
  // DECSCUSR: CSI Ps SP q 设置光标样式（含闪烁/稳定）。
  // registerCsiHandler 不支持 SP (0x20) prefix，因此通过拦截 term.write 检测。
  const originalWrite = term.write.bind(term);
  term.write = function(data, cb) {
    detectCursorSequencesFromOutput(term, uid, data);
    return originalWrite(data, cb);
  };
}

function detectCursorSequencesFromOutput(term, uid, data) {
  const str = decodeWriteData(data);
  if (!str) return;
  // DECSCUSR: CSI Ps SP q
  const decscusr = /\x1b\[(\d*)\x20q/g;
  let m;
  while ((m = decscusr.exec(str)) !== null) {
    const ps = m[1] === '' ? 0 : parseInt(m[1], 10);
    debug('cursor', 'DECSCUSR write sid=%s ps=%s blink=%s style=%s',
          uid, ps, term.options.cursorBlink, term.options.cursorStyle);
  }
}

// 导出光标内部状态，用于排查光标不闪烁问题。
export function logCursorState(uid) {
  const inst = state.termInstances[uid];
  if (!inst) {
    warn('cursor', 'logCursorState: no instance sid=%s', uid);
    return;
  }
  const term = inst.term;
  const core = term._core;
  const info = {
    uid,
    options: {
      cursorBlink: term.options.cursorBlink,
      cursorStyle: term.options.cursorStyle,
      cursorInactiveStyle: term.options.cursorInactiveStyle,
      disableStdin: term.options.disableStdin,
    },
    inst: {
      _focused: inst._focused,
      _readonly: inst._readonly,
    },
    document: {
      activeElement: document.activeElement && document.activeElement.tagName,
      hasFocus: document.hasFocus(),
      termDivFocused: inst.div === document.activeElement || inst.div.contains(document.activeElement),
    },
  };
  try {
    if (core) {
      const cs = core.optionsService;
      info.coreOptions = cs ? {
        cursorBlink: cs.options.cursorBlink,
        cursorStyle: cs.options.cursorStyle,
        cursorInactiveStyle: cs.options.cursorInactiveStyle,
      } : null;
      if (core.coreService) {
        const cks = Object.keys(core.coreService).filter(k => /focus|blink|cursor/i.test(k));
        info.coreService = {
          keys: cks,
          isCursorInitialized: typeof core.coreService.isCursorInitialized === 'function'
            ? core.coreService.isCursorInitialized() : undefined,
          isCursorHidden: typeof core.coreService.isCursorHidden === 'function'
            ? core.coreService.isCursorHidden() : undefined,
        };
      }
      if (core._renderService) {
        const rks = Object.keys(core._renderService).filter(k => /cursor|blink|render/i.test(k));
        info.renderService = {
          keys: rks,
          dimensions: core._renderService.dimensions ? {
            css: core._renderService.dimensions.css,
          } : null,
        };
      }
      if (core._renderService && core._renderService._renderer) {
        const rend = core._renderService._renderer;
        const rendKeys = Object.keys(rend).filter(k => /cursor|blink|layer/i.test(k));
        info.renderer = {
          keys: rendKeys,
        };
        // 查找光标渲染层
        try {
          const layers = rend._renderLayers || rend._layers || (rend.renderLayers ? rend.renderLayers : null);
          if (Array.isArray(layers)) {
            info.renderer.layers = layers.map((l, i) => ({
              index: i,
              constructor: l && l.constructor && l.constructor.name,
              keys: Object.keys(l || {}).filter(k => /cursor|blink|style/i.test(k)),
            }));
          }
        } catch (e) {
          info.renderer.layerError = e.message;
        }
      }
    }
  } catch (e) {
    info.coreReadError = e.message;
  }
  debug('cursor', 'logCursorState options cursorBlink=%s cursorStyle=%s inactiveStyle=%s disableStdin=%s',
        info.options.cursorBlink, info.options.cursorStyle, info.options.cursorInactiveStyle, info.options.disableStdin);
  debug('cursor', 'logCursorState inst _focused=%s _readonly=%s', info.inst._focused, info.inst._readonly);
  debug('cursor', 'logCursorState document activeElement=%s hasFocus=%s termDivFocused=%s',
        info.document.activeElement, info.document.hasFocus, info.document.termDivFocused);
  if (info.coreOptions) {
    debug('cursor', 'logCursorState coreOptions cursorBlink=%s cursorStyle=%s inactiveStyle=%s',
          info.coreOptions.cursorBlink, info.coreOptions.cursorStyle, info.coreOptions.cursorInactiveStyle);
  }
  if (info.coreService) {
    debug('cursor', 'logCursorState coreService keys=%o isCursorInitialized=%s isCursorHidden=%s',
          info.coreService.keys, info.coreService.isCursorInitialized, info.coreService.isCursorHidden);
  }
  if (info.renderService) {
    debug('cursor', 'logCursorState renderService keys=%o', info.renderService.keys);
  }
  if (info.renderer) {
    debug('cursor', 'logCursorState renderer keys=%o', info.renderer.keys);
    if (info.renderer.layers) {
      debug('cursor', 'logCursorState renderer layers=%o', info.renderer.layers);
    }
    if (info.renderer.layerError) {
      debug('cursor', 'logCursorState renderer layerError=%s', info.renderer.layerError);
    }
  }
  if (info.coreReadError) {
    debug('cursor', 'logCursorState coreReadError=%s', info.coreReadError);
  }
  return info;
}

// 强制重启光标闪烁，用于测试 xterm.js 内部闪烁机制是否正常。
export function forceCursorBlink(uid) {
  const inst = state.termInstances[uid];
  if (!inst) return;
  const term = inst.term;
  debug('cursor', 'forceCursorBlink START sid=%s blink=%s style=%s',
        uid, term.options.cursorBlink, term.options.cursorStyle);
  term.options.cursorStyle = 'bar';
  term.options.cursorInactiveStyle = 'block';
  term.options.cursorBlink = false;
  setTimeout(() => {
    term.options.cursorBlink = true;
    try { term.write('\x1b[?12h\x1b[5 q'); } catch (_) {}
    try { term.refresh(0, term.rows - 1); } catch (_) {}
    debug('cursor', 'forceCursorBlink DONE sid=%s blink=%s style=%s',
          uid, term.options.cursorBlink, term.options.cursorStyle);
    logCursorState(uid);
  }, 50);
}

// 在获得焦点/切换标签时尝试重启光标闪烁。
// 仅对活跃会话且 cursorBlink 期望为 true 时执行，避免覆盖历史会话或应用主动关闭的闪烁。
export function restartCursorBlinkIfNeeded(uid) {
  const inst = state.termInstances[uid];
  const s = state.sessions[uid];
  if (!inst || !s || s.history || !s.running) return;
  const term = inst.term;
  if (!term.options.cursorBlink) return;
  debug('cursor', 'restartCursorBlinkIfNeeded sid=%s', uid);
  // 通过短暂关闭再开启 cursorBlink，触发 xterm.js 内部 CursorBlinkStateManager 重启。
  term.options.cursorBlink = false;
  // CSI ? 12 h: 开始闪烁光标；CSI 5 q: 闪烁竖线（bar）
  try { term.write('\x1b[?12h\x1b[5 q'); } catch (_) {}
  requestAnimationFrame(() => {
    term.options.cursorBlink = true;
    try { term.refresh(0, term.rows - 1); } catch (_) {}
    logCursorState(uid);
  });
}

/**
 * 终端基础设施公共工具
 *
 * 被 terminal/ 子模块共享的常量与纯函数，避免循环依赖。
 *
 * 按 ttyd 风格：
 * - 不使用手动 measureTerminalCellSize（由 FitAddon 内部使用 xterm _renderService.dimensions）
 * - 不使用 computeAdaptiveSize（由 FitAddon.proposeDimensions / fit 替代）
 * - 仅保留按尺寸模式查询目标 cols/rows 的纯函数
 *
 * frameRatio <-> fontSize 互算纯函数：
 * - computeFrameRatio：当前 frame + stage 尺寸 → ratio（取宽高较小值）
 * - computeFontSizeFromRatio：ratio + stage + cols/rows + 当前 cell → 字号
 */

import { getSessionSizeConfig } from '../../domain/state.js';
import { DEFAULT_COLS, DEFAULT_ROWS, MIN_FONT_SIZE, MAX_FONT_SIZE } from '../../domain/constants.js';

/**
 * 根据该会话自身的尺寸模式返回终端应使用的 cols/rows。
 *
 * 按会话 uid 查询配置，每个会话独立维护模式与自定义值。
 *
 * - 'default':  使用会话从守护进程订阅到的 cols/rows（s.cols / s.rows）
 * - 'adaptive': 返回 null，由调用方使用 FitAddon 自动计算（这样 xterm 内部尺寸
 *               完全跟随容器，光标位置永远正确，与 ttyd 行为一致）
 * - 'fixed':    使用该会话保存的 fixedCols / fixedRows
 * - 'custom':   使用该会话保存的 customCols / customRows
 *
 * @param {object} s 会话对象
 * @returns {{cols:number, rows:number}|null} adaptive 模式返回 null
 */
export function applyTerminalSizeFromSession(s) {
  const cfg = getSessionSizeConfig(s && s.uid);
  // 历史会话强制非 adaptive：固定生前最后 cols/rows（s.cols/s.rows），
  // 避免 adaptive 返回 null 导致调用方 fallback 到 fit() 自适应 stage
  const mode = (s && s.history) ? 'default' : cfg.mode;
  if (mode === 'fixed') {
    return { cols: cfg.fixedCols || DEFAULT_COLS, rows: cfg.fixedRows || DEFAULT_ROWS };
  }
  if (mode === 'custom') {
    return { cols: cfg.customCols || DEFAULT_COLS, rows: cfg.customRows || DEFAULT_ROWS };
  }
  if (mode === 'adaptive') {
    // 自适应模式：由 FitAddon 根据容器尺寸计算，此处返回 null
    return null;
  }
  // 'default' 模式：使用会话当前的 cols/rows（来自守护进程）
  return {
    cols: s.cols || DEFAULT_COLS,
    rows: s.rows || DEFAULT_ROWS,
  };
}

/**
 * 获取 xterm 内部 _renderService.dimensions 中的 CSS cell 像素尺寸。
 *
 * 与 FitAddon 同源。比 .xterm-screen.getBoundingClientRect() 更及时：
 * term.options.fontSize 变化后，_renderService.dimensions 同步刷新，
 * 而 .xterm-screen DOM 尺寸要等下一帧 rAF 才更新。
 * 快速连续 Ctrl+滚轮时必须用本函数，否则读到旧 DOM 尺寸导致撑满判断错误。
 *
 * @param {object} term xterm.js Terminal 实例
 * @returns {{w:number, h:number}} cell 宽高（像素），不可用时返回 {w:0, h:0}
 */
export function getTerminalCellSize(term) {
  const core = term._core;
  if (core && core._renderService && core._renderService.dimensions &&
      core._renderService.dimensions.css && core._renderService.dimensions.css.cell) {
    const d = core._renderService.dimensions.css.cell;
    if (d.width > 0 && d.height > 0) return { w: d.width, h: d.height };
  }
  return { w: 0, h: 0 };
}

/**
 * 根据 frame 实际尺寸和 stage 内容区尺寸计算 frameRatio（取宽高较小值）。
 *
 * ratio = min(frameW/stageW, frameH/stageH)
 *
 * 取较小值保证框不超出 stage 任一方向；同时框宽高比 = cols/rows 决定的固定比例，
 * 所以一个方向撑满时另一个方向必有空白，ratio 反映的是"撑满方向的比例"。
 *
 * @param {number} frameW frame 宽度（像素）
 * @param {number} frameH frame 高度（像素）
 * @param {number} stageW stage 内容区宽度（已减去 padding）
 * @param {number} stageH stage 内容区高度（已减去 padding）
 * @returns {number} ratio (0, 1.0]，stage 尺寸无效时返回 1.0
 */
export function computeFrameRatio(frameW, frameH, stageW, stageH) {
  if (!stageW || !stageH || !frameW || !frameH) return 1.0;
  const rW = frameW / stageW;
  const rH = frameH / stageH;
  return Math.max(0.1, Math.min(1.0, Math.min(rW, rH)));
}

/**
 * 根据目标 frameRatio、当前 stage 尺寸、终端 cols/rows、当前 cell 尺寸与字号，
 * 反算出应使用的 fontSize。
 *
 * 原理：xterm.js cell 像素尺寸与 fontSize 近似成正比（由字体度量决定）。
 *   targetFrameW = stageW × ratio
 *   targetCellW  = targetFrameW / cols
 *   fontSize     = currentFontSize × (targetCellW / currentCellW)
 *
 * 取 width/height 方向的较小值，保证 frame 不超出 stage 任一方向。
 *
 * 注意：返回的是"应使用的字号"，调用方负责 clamp 到 [MIN_FONT_SIZE, MAX_FONT_SIZE]
 * 并写入 term.options.fontSize + 更新运行时 sessionFontSizes。
 *
 * @param {number} stageW stage 内容区宽度（已减去 padding）
 * @param {number} stageH stage 内容区高度（已减去 padding）
 * @param {number} cols 终端列数
 * @param {number} rows 终端行数
 * @param {number} ratio 目标 frameRatio (0, 1.0]
 * @param {number} currentCellW 当前 cell 宽度（像素）
 * @param {number} currentCellH 当前 cell 高度（像素）
 * @param {number} currentFontSize 当前字号
 * @returns {number} 反算后的字号（未 clamp），参数无效时返回 currentFontSize
 */
export function computeFontSizeFromRatio(stageW, stageH, cols, rows, ratio,
                                         currentCellW, currentCellH, currentFontSize) {
  if (!stageW || !stageH || !cols || !rows || !ratio ||
      !currentCellW || !currentCellH || !currentFontSize) {
    return currentFontSize;
  }
  // 目标 frame 尺寸 = stage × ratio
  const targetFrameW = stageW * ratio;
  const targetFrameH = stageH * ratio;
  // 目标 cell 尺寸 = 目标 frame / cols(rows)
  const targetCellW = targetFrameW / cols;
  const targetCellH = targetFrameH / rows;
  // cell 尺寸 ∝ fontSize，按比例反算
  const fontSizeByW = currentFontSize * (targetCellW / currentCellW);
  const fontSizeByH = currentFontSize * (targetCellH / currentCellH);
  // 取较小值保证框不超出 stage（任一方向都不超）
  const fontSize = Math.min(fontSizeByW, fontSizeByH);
  // clamp 到字号范围
  return Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, Math.floor(fontSize)));
}

export function isFunctionKey(key) {
  return /^F\d{1,2}$/.test(key);
}

export function decodeWriteData(data) {
  if (typeof data === 'string') return data;
  if (data && typeof data.length === 'number') {
    try {
      let s = '';
      for (let i = 0; i < data.length; i++) s += String.fromCharCode(data[i]);
      return s;
    } catch (_) {}
  }
  return '';
}

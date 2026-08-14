/**
 * 领域层：常量、主题配置、SVG 图标
 *
 * 该文件仅包含纯数据，不依赖任何其他模块，属于 Clean Architecture 中最内层的领域层。
 */

export const ICON_CLOSE = '<svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>';

// VNC 远程桌面 tab 的特殊会话 ID（不与真实会话冲突）
export const VNC_TAB_ID = '__vnc__';

// FastScreen 屏幕查看 tab 的特殊会话 ID（不与真实会话冲突）
export const FASTSCREEN_TAB_ID = '__fastscreen__';

// 设置 tab 的特殊会话 ID（不与真实会话冲突）
export const SETTINGS_TAB_ID = '__settings__';

// 远程桌面标签栏按钮图标（显示器）
export const ICON_VNC = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><rect x="1.5" y="2.5" width="13" height="9" rx="1" stroke="currentColor" stroke-width="1.5" fill="none"/><line x1="5" y1="14.5" x2="11" y2="14.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><line x1="8" y1="11.5" x2="8" y2="14.5" stroke="currentColor" stroke-width="1.5"/></svg>';

// 屏幕查看标签栏按钮图标（眼睛 — 仅查看，区别于 VNC 的交互式远程桌面）
export const ICON_FASTSCREEN = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linejoin="round"/><circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>';

export const LIGHT_THEME = {
  foreground: '#383A42',
  background: '#FAFAFA',
  cursor: '#383A42',
  cursorAccent: '#FAFAFA',
  selectionBackground: 'rgba(56, 58, 66, 0.15)',
  selectionForeground: '#383A42',
  black: '#383A42',
  red: '#E45649',
  green: '#50A14F',
  yellow: '#C18401',
  blue: '#4078F2',
  magenta: '#A626A4',
  cyan: '#0184BC',
  white: '#FAFAFA',
  brightBlack: '#A0A1A7',
  brightRed: '#E45649',
  brightGreen: '#50A14F',
  brightYellow: '#C18401',
  brightBlue: '#4078F2',
  brightMagenta: '#A626A4',
  brightCyan: '#0184BC',
  brightWhite: '#FFFFFF',
};

export const DARK_THEME = {
  foreground: '#CCCCCC',
  background: '#0C0C0C',
  cursor: '#CCCCCC',
  cursorAccent: '#0C0C0C',
  selectionBackground: 'rgba(255, 255, 255, 0.15)',
  selectionForeground: '#CCCCCC',
  black: '#0C0C0C',
  red: '#C50F1F',
  green: '#13A10E',
  yellow: '#C19C00',
  blue: '#0037DA',
  magenta: '#881798',
  cyan: '#3A96DD',
  white: '#CCCCCC',
  brightBlack: '#767676',
  brightRed: '#E74856',
  brightGreen: '#16C60C',
  brightYellow: '#F9F1A5',
  brightBlue: '#3B78FF',
  brightMagenta: '#B4009E',
  brightCyan: '#61D6D6',
  brightWhite: '#F2F2F2',
};

// 终端默认尺寸（80x24，参考 VT100 标准终端）
// 定义在 domain 层以解除对 infrastructure 层的依赖（Clean Architecture 依赖规则：外层→内层）
export const DEFAULT_COLS = 80;
export const DEFAULT_ROWS = 24;

export const DEFAULT_FONT_SIZE = 14;
// 字号缩放范围（Ctrl+滚轮 / Ctrl+-/+/0 / 触摸捏合调整）
// 与 ttyd/WT 一致：通过 fontSize 控制字符显示大小，xterm 内部 cols/rows 跟随容器自动适配
export const MIN_FONT_SIZE = 8;
export const MAX_FONT_SIZE = 32;
export const FONT_SIZE_STEP = 1;       // Ctrl+滚轮 每次调整步长（adaptive 模式下直接调字号用）
export const FONT_SIZE_PINCH_STEP = 1; // 触摸捏合每档步长

// frameRatio（框/stage 占比）相关常量。
// 非 adaptive 模式下 Ctrl+滚轮 / 触摸捏合 / Ctrl+± 调整的是 frameRatio，
// 字号由 frameRatio + stage 尺寸反算得到。
export const FRAME_RATIO_MIN = 0.1;    // ratio 下限（防止框过小）
export const FRAME_RATIO_MAX = 1.0;    // ratio 上限（撑满 stage）
export const FRAME_RATIO_STEP = 0.05;  // Ctrl+滚轮每档 ratio 步长（5%）
// 撑满判断容差：ratio >= MAX - EPSILON 视为已撑满
export const FRAME_RATIO_EPSILON = 0.005;

export const DEFAULT_SIDEBAR_WIDTH = 220;
export const MIN_SIDEBAR_WIDTH = 120;
export const MAX_SIDEBAR_WIDTH = 500;

// 终端尺寸预设列表（"默认"与"自适应"作为模式单独处理，不在此列表中）
// "自定义"也作为模式单独处理
export const TERMINAL_SIZE_PRESETS = [
  { label: '80 x 24',  cols: 80,  rows: 24 },
  { label: '100 x 30', cols: 100, rows: 30 },
  { label: '100 x 35', cols: 100, rows: 35 },
  { label: '120 x 30', cols: 120, rows: 30 },
  { label: '132 x 40', cols: 132, rows: 40 },
  { label: '150 x 45', cols: 150, rows: 45 },
  { label: '180 x 50', cols: 180, rows: 50 },
];

// 自适应模式的最小尺寸下限
export const ADAPTIVE_MIN_COLS = 80;
export const ADAPTIVE_MIN_ROWS = 24;
// 自适应模式的尺寸上限（防止极端值）
export const ADAPTIVE_MAX_COLS = 400;
export const ADAPTIVE_MAX_ROWS = 120;

/**
 * TKL 键盘布局定义
 *
 * 标准 87 键 TKL（Tenkeyless）布局：无数字小键盘，含功能行、导航区、方向键。
 * 仅用于悬浮模式，配合 RimePanel 显示候选词。
 */

/** TKL 按键动作类型 */
export type TKLKeyAction =
  | 'shift'
  | 'ctrl'
  | 'alt'
  | 'meta'
  | 'caps'
  | 'backspace'
  | 'enter'
  | 'space'
  | 'tab'
  | 'escape'
  | 'insert'
  | 'delete'
  | 'home'
  | 'end'
  | 'pageup'
  | 'pagedown'
  | 'printscreen'
  | 'scrolllock'
  | 'pause'
  | 'menu'
  | 'lang'
  | 'fn'
  | 'f1' | 'f2' | 'f3' | 'f4' | 'f5' | 'f6' | 'f7' | 'f8' | 'f9' | 'f10' | 'f11' | 'f12'
  | 'arrow_up' | 'arrow_down' | 'arrow_left' | 'arrow_right'

/** 单个按键定义 */
export interface TKLKeyDef {
  key: string
  label: string
  shiftKey?: string
  shiftLabel?: string
  width?: number
  action?: TKLKeyAction
  /** 修饰键标记 */
  isModifier?: boolean
  /** 不可见占位键（用于方向键倒 T 形布局对齐，不渲染按键、不响应事件） */
  hidden?: boolean
}

/** 一行按键 */
export type TKLKeyRow = TKLKeyDef[]

/** 完整 TKL 布局 */
export type TKLKeyboardLayout = TKLKeyRow[]

// ─── TKL 标准布局 ──────────────────────────────────────────────────

const TKL_LAYOUT: TKLKeyboardLayout = [
  // 行0: 功能键行
  [
    { key: 'Escape', label: 'Esc', action: 'escape', width: 1 },
    // F1~F4 组
    { key: 'F1', label: 'F1', action: 'f1', width: 1 },
    { key: 'F2', label: 'F2', action: 'f2', width: 1 },
    { key: 'F3', label: 'F3', action: 'f3', width: 1 },
    { key: 'F4', label: 'F4', action: 'f4', width: 1 },
    // F5~F8 组
    { key: 'F5', label: 'F5', action: 'f5', width: 1 },
    { key: 'F6', label: 'F6', action: 'f6', width: 1 },
    { key: 'F7', label: 'F7', action: 'f7', width: 1 },
    { key: 'F8', label: 'F8', action: 'f8', width: 1 },
    // F9~F12 组
    { key: 'F9', label: 'F9', action: 'f9', width: 1 },
    { key: 'F10', label: 'F10', action: 'f10', width: 1 },
    { key: 'F11', label: 'F11', action: 'f11', width: 1 },
    { key: 'F12', label: 'F12', action: 'f12', width: 1 },
    // 导航区
    { key: 'PrintScreen', label: 'PrtSc', action: 'printscreen', width: 1 },
    { key: 'ScrollLock', label: 'ScrLk', action: 'scrolllock', width: 1 },
    { key: 'Pause', label: 'Pause', action: 'pause', width: 1 },
  ],
  // 行1: 数字行
  [
    { key: '`', label: '`', shiftKey: '~', shiftLabel: '~', width: 1 },
    { key: '1', label: '1', shiftKey: '!', shiftLabel: '!' },
    { key: '2', label: '2', shiftKey: '@', shiftLabel: '@' },
    { key: '3', label: '3', shiftKey: '#', shiftLabel: '#' },
    { key: '4', label: '4', shiftKey: '$', shiftLabel: '$' },
    { key: '5', label: '5', shiftKey: '%', shiftLabel: '%' },
    { key: '6', label: '6', shiftKey: '^', shiftLabel: '^' },
    { key: '7', label: '7', shiftKey: '&', shiftLabel: '&' },
    { key: '8', label: '8', shiftKey: '*', shiftLabel: '*' },
    { key: '9', label: '9', shiftKey: '(', shiftLabel: '(' },
    { key: '0', label: '0', shiftKey: ')', shiftLabel: ')' },
    { key: '-', label: '-', shiftKey: '_', shiftLabel: '_' },
    { key: '=', label: '=', shiftKey: '+', shiftLabel: '+' },
    { key: 'Backspace', label: '⌫', action: 'backspace', width: 2 },
    // 导航区
    { key: 'Insert', label: 'Ins', action: 'insert', width: 1 },
    { key: 'Home', label: 'Home', action: 'home', width: 1 },
    { key: 'PageUp', label: 'PgUp', action: 'pageup', width: 1 },
  ],
  // 行2: QWERTY 行
  [
    { key: 'Tab', label: 'Tab', action: 'tab', width: 1.5 },
    { key: 'q', label: 'Q', shiftKey: 'Q', shiftLabel: 'Q' },
    { key: 'w', label: 'W', shiftKey: 'W', shiftLabel: 'W' },
    { key: 'e', label: 'E', shiftKey: 'E', shiftLabel: 'E' },
    { key: 'r', label: 'R', shiftKey: 'R', shiftLabel: 'R' },
    { key: 't', label: 'T', shiftKey: 'T', shiftLabel: 'T' },
    { key: 'y', label: 'Y', shiftKey: 'Y', shiftLabel: 'Y' },
    { key: 'u', label: 'U', shiftKey: 'U', shiftLabel: 'U' },
    { key: 'i', label: 'I', shiftKey: 'I', shiftLabel: 'I' },
    { key: 'o', label: 'O', shiftKey: 'O', shiftLabel: 'O' },
    { key: 'p', label: 'P', shiftKey: 'P', shiftLabel: 'P' },
    { key: '[', label: '[', shiftKey: '{', shiftLabel: '{' },
    { key: ']', label: ']', shiftKey: '}', shiftLabel: '}' },
    { key: '\\', label: '\\', shiftKey: '|', shiftLabel: '|', width: 1.5 },
    // 导航区
    { key: 'Delete', label: 'Del', action: 'delete', width: 1 },
    { key: 'End', label: 'End', action: 'end', width: 1 },
    { key: 'PageDown', label: 'PgDn', action: 'pagedown', width: 1 },
  ],
  // 行3: 主键行
  [
    { key: 'CapsLock', label: 'Caps', action: 'caps', width: 1.75 },
    { key: 'a', label: 'A', shiftKey: 'A', shiftLabel: 'A' },
    { key: 's', label: 'S', shiftKey: 'S', shiftLabel: 'S' },
    { key: 'd', label: 'D', shiftKey: 'D', shiftLabel: 'D' },
    { key: 'f', label: 'F', shiftKey: 'F', shiftLabel: 'F' },
    { key: 'g', label: 'G', shiftKey: 'G', shiftLabel: 'G' },
    { key: 'h', label: 'H', shiftKey: 'H', shiftLabel: 'H' },
    { key: 'j', label: 'J', shiftKey: 'J', shiftLabel: 'J' },
    { key: 'k', label: 'K', shiftKey: 'K', shiftLabel: 'K' },
    { key: 'l', label: 'L', shiftKey: 'L', shiftLabel: 'L' },
    { key: ';', label: ';', shiftKey: ':', shiftLabel: ':' },
    { key: "'", label: "'", shiftKey: '"', shiftLabel: '"' },
    { key: 'Enter', label: 'Enter', action: 'enter', width: 2.25 },
  ],
  // 行4: Shift 行
  [
    { key: 'ShiftLeft', label: 'Shift', action: 'shift', width: 2.25, isModifier: true },
    { key: 'z', label: 'Z', shiftKey: 'Z', shiftLabel: 'Z' },
    { key: 'x', label: 'X', shiftKey: 'X', shiftLabel: 'X' },
    { key: 'c', label: 'C', shiftKey: 'C', shiftLabel: 'C' },
    { key: 'v', label: 'V', shiftKey: 'V', shiftLabel: 'V' },
    { key: 'b', label: 'B', shiftKey: 'B', shiftLabel: 'B' },
    { key: 'n', label: 'N', shiftKey: 'N', shiftLabel: 'N' },
    { key: 'm', label: 'M', shiftKey: 'M', shiftLabel: 'M' },
    { key: ',', label: ',', shiftKey: '<', shiftLabel: '<' },
    { key: '.', label: '.', shiftKey: '>', shiftLabel: '>' },
    { key: '/', label: '/', shiftKey: '?', shiftLabel: '?' },
    { key: 'ShiftRight', label: 'Shift', action: 'shift', width: 2.75, isModifier: true },
    // 方向键 ↑（倒 T 形：两侧 spacer 使 ArrowUp 对齐行5的 ArrowDown）
    { key: 'SpacerLeft', label: '', width: 1, hidden: true },
    { key: 'ArrowUp', label: '↑', action: 'arrow_up', width: 1 },
    { key: 'SpacerRight', label: '', width: 1, hidden: true },
  ],
  // 行5: 底部修饰键行
  [
    { key: 'ControlLeft', label: 'Ctrl', action: 'ctrl', width: 1.25, isModifier: true },
    { key: 'MetaLeft', label: 'Win', action: 'meta', width: 1.25, isModifier: true },
    { key: 'AltLeft', label: 'Alt', action: 'alt', width: 1.25, isModifier: true },
    { key: 'space', label: '', action: 'space', width: 6.25 },
    { key: 'AltRight', label: 'Alt', action: 'alt', width: 1.25, isModifier: true },
    { key: 'Lang', label: '中', action: 'lang', width: 1.25 },
    { key: 'ControlRight', label: 'Ctrl', action: 'ctrl', width: 1.25, isModifier: true },
    // 方向键 ← ↓ →
    { key: 'ArrowLeft', label: '←', action: 'arrow_left', width: 1 },
    { key: 'ArrowDown', label: '↓', action: 'arrow_down', width: 1 },
    { key: 'ArrowRight', label: '→', action: 'arrow_right', width: 1 },
  ],
]

/** 获取 TKL 布局 */
export function getTKLLayout(): TKLKeyboardLayout {
  return TKL_LAYOUT
}

// ─── RIME 键名映射 ─────────────────────────────────────────────────

/** 将 TKL 按键的 key/action 映射为 RIME 引擎识别的键名 */
export const TKL_RIME_KEY_MAP: Record<string, string> = {
  Escape: 'Escape',
  F1: 'F1', F2: 'F2', F3: 'F3', F4: 'F4',
  F5: 'F5', F6: 'F6', F7: 'F7', F8: 'F8',
  F9: 'F9', F10: 'F10', F11: 'F11', F12: 'F12',
  Backspace: 'BackSpace',
  Delete: 'Delete',
  Tab: 'Tab',
  Enter: 'Return',
  Home: 'Home',
  End: 'End',
  PageUp: 'Page_Up',
  PageDown: 'Page_Down',
  ArrowUp: 'Up',
  ArrowDown: 'Down',
  ArrowLeft: 'Left',
  ArrowRight: 'Right',
  CapsLock: 'Caps_Lock',
  PrintScreen: 'Print',
  ScrollLock: 'Scroll_Lock',
  Pause: 'Pause',
  Insert: 'Insert',
  ' ': 'space',
  // 符号键 RIME 名称
  '`': 'quoteleft', '~': 'asciitilde',
  '!': 'exclam', '@': 'at', '#': 'numbersign',
  $: 'dollar', '%': 'percent', '^': 'asciicircum',
  '&': 'ampersand', '*': 'asterisk',
  '(': 'parenleft', ')': 'parenright',
  '-': 'minus', _: 'underscore',
  '+': 'plus', '=': 'equal',
  '{': 'braceleft', '[': 'bracketleft',
  '}': 'braceright', ']': 'bracketright',
  ':': 'colon', ';': 'semicolon',
  '"': 'quotedbl', "'": 'apostrophe',
  '|': 'bar', '\\': 'backslash',
  '<': 'less', ',': 'comma',
  '>': 'greater', '.': 'period',
  '?': 'question', '/': 'slash',
}

/** 修饰键名称（用于组合键构建） */
export const MODIFIER_NAMES: Record<string, string> = {
  ctrl: 'Control',
  alt: 'Alt',
  meta: 'Meta',
  shift: 'Shift',
}

/** 构建 RIME 组合键字符串，如 {Control+Alt+Shift+a} */
export function buildRimeCombo(modifiers: string[], key: string): string {
  const parts = [...modifiers, key]
  return `{${parts.join('+')}}`
}

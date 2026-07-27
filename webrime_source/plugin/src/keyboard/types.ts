/**
 * 虚拟键盘类型定义
 *
 * 集中定义 RimeKeyboard 所有公开/内部类型，供其他模块引用。
 */

import type { RimeIMEConfig, CommitCallback } from '../types'
import type { RimeIME } from '../ime'
import type { KeyboardPage, KeyDef } from './layouts'

// ─── 公开类型 ───

export type KeyboardMode = 'docked' | 'floating'
export type RimeKeyboardTheme = 'dark' | 'light'
export type RimeKeyboardSize = 'compact' | 'normal' | 'large'
export type ShiftState = 'off' | 'once' | 'locked'

export interface RimeKeyboardThemeVars {
  kbBg?: string
  kbBorder?: string
  kbRadius?: string
  kbShadow?: string
  kbFontFamily?: string
  kbZIndex?: number
  keyBg?: string
  keyColor?: string
  keyFontSize?: string
  keyHeight?: string
  keyGap?: string
  keyRadius?: string
  keyActiveBg?: string
  keyActiveColor?: string
  keyActiveScale?: string
  fnKeyBg?: string
  fnKeyColor?: string
  spaceBg?: string
  spaceColor?: string
  candBarBg?: string
  candBarHeight?: string
  candColor?: string
  candFontSize?: string
  candActiveBg?: string
  compHeadColor?: string
  compBodyColor?: string
  compTailColor?: string
  compFontSize?: string
  navColor?: string
  navFontSize?: string
  toolbarBg?: string
  toolbarBtnColor?: string
  toolbarBtnActiveColor?: string
  previewBg?: string
  previewColor?: string
  previewFontSize?: string
  previewRadius?: string
  altBg?: string
  altColor?: string
  altFontSize?: string
  altActiveBg?: string
  safeAreaBottom?: string
}

export interface RimeKeyboardConfig extends RimeIMEConfig {
  target: HTMLElement
  theme?: RimeKeyboardTheme
  size?: RimeKeyboardSize
  kbMode?: KeyboardMode
  showOnFocus?: boolean
  haptic?: boolean
  floatingWidth?: number
  floatingHeight?: number
  /** 回车键发送的字符，默认 '\n'；终端集成时可覆盖为 '\r' */
  eol?: string
  /** 隐藏键盘自带的候选栏（预编辑+候选词），由外部 RimePanel 显示。
   * 用于模式3：悬浮键盘（不含候选词）+ 独立 RimePanel。 */
  hideCandidateBar?: boolean
  /** 外部 IME 实例：传入时 Keyboard 不创建自己的 RimeIME，而是共享此实例。
   * 用于 RimeManager 模式2（Panel+Keyboard 共享同一 IME）。 */
  ime?: RimeIME
}

// ─── 内部共享状态 ───

/** 键盘内部状态，主类持有，各模块通过引用访问 */
export interface KeyboardState {
  ime: import('../ime').RimeIME
  target: HTMLElement
  currentPage: KeyboardPage
  shiftState: ShiftState
  isEnglish: boolean
  isFullWidth: boolean
  isEnglishPunct: boolean
  isEmoji: boolean
  isSimplification: boolean
  editing: boolean
  visible: boolean
  destroyed: boolean
  lastResult: import('../types').RimeResult | null
  keyTouched: boolean
  hapticEnabled: boolean
  floatingWidth: number
  currentMode: KeyboardMode
  commitCallbacks: CommitCallback[]
  keyPressCallbacks: ((key: string) => void)[]
}

// ─── DOM 引用 ───

/** 键盘所有 DOM 元素的引用集合 */
export interface KeyboardDOM {
  container: HTMLDivElement
  toolbar: HTMLDivElement
  compBar: HTMLDivElement
  candBar: HTMLDivElement
  cands: HTMLDivElement
  candNav: HTMLDivElement
  keys: HTMLDivElement
  safe: HTMLDivElement
  preview: HTMLDivElement
  alt: HTMLDivElement
  dragHandle: HTMLDivElement
  hideBtn: HTMLButtonElement
}

// ─── 触摸回调 ───

/** 触摸处理器需要的回调接口 */
export interface TouchCallbacks {
  fireKey: (keyDef: KeyDef) => void
  insertText: (text: string) => void
  haptic: () => void
  getKeyDef: (key: string) => KeyDef | null
}

// ─── 常量 ───

/** 标点键长按候选 */
export const PUNCT_ALT = ['，', '！', '？', '、', '：', '；', '…', '—', '·']

/** 样式 ID */
export const STYLE_ID = 'rime-keyboard-style'

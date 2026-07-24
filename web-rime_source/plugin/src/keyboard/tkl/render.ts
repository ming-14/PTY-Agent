/**
 * TKL 键盘渲染逻辑
 *
 * 按键渲染、修饰键高亮状态、全半角符号显示。
 * 纯函数，接收 DOM 引用和状态参数。
 */

import type { TKLKeyDef, TKLKeyAction } from './layouts'
import { getTKLLayout } from './layouts'
import { FULLWIDTH_PUNCT_MAP, toFullWidth } from '../render'

/** 修饰键激活状态集合 */
export type ModifierState = Set<string>

/** Shift 状态 */
export type TKLShiftState = 'off' | 'once' | 'locked'

/** 渲染所有按键 */
export function renderTKLKeys(
  keysEl: HTMLDivElement,
  shiftState: TKLShiftState,
  capsActive: boolean,
  modifiers: ModifierState,
  isEnglish: boolean,
  isFullWidth: boolean,
  isEnglishPunct: boolean
): void {
  const layout = getTKLLayout()
  keysEl.innerHTML = ''

  for (const row of layout) {
    const rowEl = document.createElement('div')
    rowEl.className = 'rime-tkl-row'
    for (const keyDef of row) {
      rowEl.appendChild(createTKLKeyEl(keyDef, shiftState, capsActive, modifiers, isEnglish, isFullWidth, isEnglishPunct))
    }
    keysEl.appendChild(rowEl)
  }
}

/** 创建单个按键元素 */
function createTKLKeyEl(
  keyDef: TKLKeyDef,
  shiftState: TKLShiftState,
  capsActive: boolean,
  modifiers: ModifierState,
  isEnglish: boolean,
  isFullWidth: boolean,
  isEnglishPunct: boolean
): HTMLElement {
  // 不可见占位键：渲染为透明 div，不响应事件（用于方向键倒 T 形对齐）
  if (keyDef.hidden) {
    const spacer = document.createElement('div')
    spacer.className = 'rime-tkl-spacer'
    if (keyDef.width) spacer.style.flex = String(keyDef.width)
    return spacer
  }

  const el = document.createElement('button')
  el.className = keyClass(keyDef, shiftState, capsActive, modifiers)
  el.dataset.key = keyDef.key
  if (keyDef.action) el.dataset.action = keyDef.action
  if (keyDef.width) el.style.flex = String(keyDef.width)

  // 有 shiftLabel 的非字母键：双行显示
  // Shift off: base 大+亮(主), shift 小+灰(次)
  // Shift on:  shift 大+亮(主), base 小+灰(次)
  if (keyDef.shiftLabel && !/^[a-z]$/i.test(keyDef.key) && !keyDef.action) {
    const shiftOn = shiftState !== 'off'
    el.classList.add('rime-tkl-key-dual')
    if (shiftOn) el.classList.add('rime-tkl-key-dual-shift')
    // 次标签（右上角，小+灰）
    const sub = document.createElement('span')
    sub.className = 'rime-tkl-sub'
    sub.textContent = shiftOn
      ? keyMainLabel(keyDef, isFullWidth, isEnglishPunct)
      : keySubLabel(keyDef, isFullWidth, isEnglishPunct)
    // 主标签（居中，大+亮）
    const main = document.createElement('span')
    main.className = 'rime-tkl-main'
    main.textContent = shiftOn
      ? keySubLabel(keyDef, isFullWidth, isEnglishPunct)
      : keyMainLabel(keyDef, isFullWidth, isEnglishPunct)
    el.appendChild(sub)
    el.appendChild(main)
  } else {
    el.textContent = keyLabel(keyDef, shiftState, capsActive, isEnglish, isFullWidth, isEnglishPunct)
  }

  return el
}

/** 按键 CSS 类名 */
function keyClass(
  keyDef: TKLKeyDef,
  shiftState: TKLShiftState,
  capsActive: boolean,
  modifiers: ModifierState
): string {
  let cls = 'rime-tkl-key'
  if (keyDef.action) {
    cls += ' rime-tkl-key-fn'
    if (keyDef.action === 'space') cls += ' rime-tkl-key-space'
    if (keyDef.action === 'shift') cls += ' rime-tkl-key-shift'
    if (keyDef.action === 'ctrl' || keyDef.action === 'alt' || keyDef.action === 'meta') {
      cls += ' rime-tkl-key-mod'
    }
  }
  // 修饰键激活高亮
  if (keyDef.action === 'shift' && shiftState !== 'off') {
    cls += ' rime-tkl-key-active'
  }
  if (keyDef.action === 'caps' && capsActive) {
    cls += ' rime-tkl-key-active'
  }
  if (keyDef.action === 'ctrl' && modifiers.has('ctrl')) {
    cls += ' rime-tkl-key-active'
  }
  if (keyDef.action === 'alt' && modifiers.has('alt')) {
    cls += ' rime-tkl-key-active'
  }
  if (keyDef.action === 'meta' && modifiers.has('meta')) {
    cls += ' rime-tkl-key-active'
  }
  return cls
}

/** 按键显示文字。
 *  符号受 isFullWidth（满月/半月）和 isEnglishPunct（符号全半角）共同控制，全角优先：
 *    isFullWidth=true（满月）→ 强制全部全角：列表内→中文符号，列表外→全角英文符号，字母数字→全角
 *    isFullWidth=false + isEnglishPunct=false（符号全角）→ 仅列表内中文符号，列表外 ASCII
 *    isFullWidth=false + isEnglishPunct=true（符号半角）→ ASCII
 *  字母数字受 isFullWidth 控制：满月→全角，半月→ASCII
 *  功能键/修饰键 label 不转换。 */
function keyLabel(
  keyDef: TKLKeyDef,
  shiftState: TKLShiftState,
  capsActive: boolean,
  isEnglish: boolean,
  isFullWidth: boolean,
  isEnglishPunct: boolean
): string {
  // 功能键/修饰键用固定 label
  if (keyDef.action) {
    if (keyDef.action === 'lang') return isEnglish ? 'En' : '中'
    if (keyDef.action === 'shift') return shiftState === 'locked' ? '⇪' : '⇧'
    if (keyDef.action === 'caps') return capsActive ? '⇪' : 'Caps'
    if (keyDef.action === 'space') return ''
    return keyDef.label
  }
  // 字母键：Shift 或 CapsLock 激活时显示大写
  if (/^[a-z]$/.test(keyDef.key)) {
    const upper = shiftState !== 'off' || capsActive
    const s = upper ? (keyDef.shiftLabel || keyDef.key.toUpperCase()) : keyDef.label
    if (isFullWidth) return toFullWidth(s)
    return s
  }
  // 符号/数字键：Shift 激活时先取 shiftLabel
  let s: string
  if (shiftState !== 'off' && keyDef.shiftLabel) s = keyDef.shiftLabel
  else s = keyDef.label
  // 符号全半角转换
  const useCnPunct = isFullWidth || !isEnglishPunct
  if (useCnPunct) {
    const mapped = FULLWIDTH_PUNCT_MAP[s]
    if (mapped) return mapped
    if (s === "'") return '\u2018'
    if (s === '"') return '\u201C'
  }
  if (isFullWidth) return toFullWidth(s)
  return s
}

/** 双行按键的主标签（base 字符，始终显示基础值） */
function keyMainLabel(
  keyDef: TKLKeyDef,
  isFullWidth: boolean,
  isEnglishPunct: boolean
): string {
  const s = keyDef.label
  const useCnPunct = isFullWidth || !isEnglishPunct
  if (useCnPunct) {
    const mapped = FULLWIDTH_PUNCT_MAP[s]
    if (mapped) return mapped
    if (s === "'") return '\u2018'
    if (s === '"') return '\u201C'
  }
  if (isFullWidth) return toFullWidth(s)
  return s
}

/** 双行按键的右上小标签（shift 字符） */
function keySubLabel(
  keyDef: TKLKeyDef,
  isFullWidth: boolean,
  isEnglishPunct: boolean
): string {
  const s = keyDef.shiftLabel || ''
  if (!s) return ''
  const useCnPunct = isFullWidth || !isEnglishPunct
  if (useCnPunct) {
    const mapped = FULLWIDTH_PUNCT_MAP[s]
    if (mapped) return mapped
    if (s === "'") return '\u2018'
    if (s === '"') return '\u201C'
  }
  if (isFullWidth) return toFullWidth(s)
  return s
}

/** 判断按键是否为修饰键 */
export function isModifierAction(action: TKLKeyAction | undefined): action is 'ctrl' | 'alt' | 'meta' | 'shift' {
  return action === 'ctrl' || action === 'alt' || action === 'meta' || action === 'shift'
}

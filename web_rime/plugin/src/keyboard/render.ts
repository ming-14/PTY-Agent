/**
 * 虚拟键盘渲染逻辑
 *
 * 按键渲染、候选栏渲染、工具栏状态刷新。
 * 纯函数，接收 DOM 引用和状态参数。
 */

import type { RimeResult } from '../types'
import type { KeyboardDOM, ShiftState } from './types'
import type { KeyboardPage, KeyDef } from './layouts'
import { getLayout } from './layouts'

// ─── 全角/半角转换 ───

/** 将字符串中的 ASCII 字符转换为全角。
 *  - 空格 U+0020 → 全角空格 U+3000
 *  - ASCII 0x21-0x7E → 全角 0xFF01-0xFF5E（加 0xFEE0）
 *  - 非 ASCII 字符原样返回（如中文标点 。 ， 不受影响，无双重转换风险） */
export function toFullWidth(s: string): string {
  let out = ''
  for (const ch of s) {
    const code = ch.codePointAt(0)!
    if (code === 0x20) out += '\u3000'
    else if (code >= 0x21 && code <= 0x7E) out += String.fromCharCode(code + 0xFEE0)
    else out += ch
  }
  return out
}

/** 满月/符号全角模式下，列表内 ASCII 符号 → 中文全角符号映射。
 *  输入法满月模式应输出中文符号：·！￥……（）—【】：；''""，。？、
 *  符号受 isFullWidth（满月/半月）和 isEnglishPunct（符号全半角）共同控制，全角优先。
 *  满月强制全部全角（包括符号）：列表内→中文符号，列表外→全角英文符号（＠＆等）。
 *  半月+符号全角：仅列表内中文符号，列表外 ASCII。
 *  半月+符号半角：ASCII。 */
export const FULLWIDTH_PUNCT_MAP: Record<string, string> = {
  '.': '。',
  ',': '，',
  '?': '？',
  '!': '！',
  ':': '：',
  ';': '；',
  '(': '（',
  ')': '）',
  '[': '【',
  ']': '】',
  '-': '—',
  '$': '￥',
  '\\': '、',
  '~': '·',
}

// ─── 按键渲染 ───

/** 根据布局和状态重新渲染所有按键 */
export function renderKeys(
  keysEl: HTMLDivElement,
  page: KeyboardPage,
  shiftState: ShiftState,
  isEnglish: boolean,
  isEnglishPunct: boolean,
  isFullWidth: boolean
): void {
  const layout = getLayout(page)
  keysEl.innerHTML = ''

  for (const row of layout) {
    const rowEl = document.createElement('div')
    rowEl.className = 'rime-kb-row'
    for (const keyDef of row) {
      rowEl.appendChild(createKeyEl(keyDef, shiftState, isEnglish, isEnglishPunct, isFullWidth))
    }
    keysEl.appendChild(rowEl)
  }
}

/** 创建单个按键元素 */
function createKeyEl(
  keyDef: KeyDef,
  shiftState: ShiftState,
  isEnglish: boolean,
  isEnglishPunct: boolean,
  isFullWidth: boolean
): HTMLButtonElement {
  const el = document.createElement('button')
  el.className = keyClass(keyDef)
  // dataset.key 用于 getKeyDef(def.key === key) 查找，必须用 keyDef.key
  // 符号显示只受 isFullWidth 控制，不再用 cnKey 作为 dataset
  el.dataset.key = keyDef.key
  if (keyDef.action) el.dataset.action = keyDef.action
  if (keyDef.width) el.style.flex = String(keyDef.width)
  el.textContent = keyLabel(keyDef, shiftState, isEnglish, isEnglishPunct, isFullWidth)
  return el
}

/** 按键 CSS 类名 */
function keyClass(keyDef: KeyDef): string {
  let cls = 'rime-kb-key'
  if (keyDef.action) {
    cls += ' rime-kb-key-fn'
    if (keyDef.action === 'space') cls += ' rime-kb-key-space'
    if (keyDef.action === 'shift') cls += ' rime-kb-key-shift'
  }
  return cls
}

/** 按键显示文字。
 *  符号受 isFullWidth（满月/半月）和 isEnglishPunct（符号全半角）共同控制，全角优先：
 *    isFullWidth=true（满月）→ 强制全部全角：列表内→中文符号，列表外→全角英文符号，字母数字→全角
 *    isFullWidth=false + isEnglishPunct=false（符号全角）→ 仅列表内中文符号，列表外 ASCII
 *    isFullWidth=false + isEnglishPunct=true（符号半角）→ ASCII
 *  字母数字受 isFullWidth 控制：满月→全角，半月→ASCII
 *  功能键图标（⇧/⇪）、语言指示（中/En）、动作 label 不转换。 */
function keyLabel(
  keyDef: KeyDef,
  shiftState: ShiftState,
  isEnglish: boolean,
  isEnglishPunct: boolean,
  isFullWidth: boolean
): string {
  if (keyDef.action === 'shift') return shiftState === 'locked' ? '\u21EA' : '\u21E7'
  if (keyDef.action === 'lang') return isEnglish ? 'En' : '中'
  if (keyDef.action === 'punct') {
    // 标点键：满月或符号全角 → 。, 符号半角 → .
    const useCnPunct = isFullWidth || !isEnglishPunct
    return useCnPunct ? '。' : '.'
  }
  // 其他动作键（backspace/enter/space/page）的 label 是 UI 指示，不转换
  if (keyDef.action) return keyDef.label
  // 字母键/符号键：统一用 label/shiftLabel
  let s: string
  if (shiftState !== 'off' && keyDef.shiftLabel) s = keyDef.shiftLabel
  else s = keyDef.label
  // 符号受 isFullWidth 和 isEnglishPunct 共同控制，全角优先
  const useCnPunct = isFullWidth || !isEnglishPunct
  if (useCnPunct) {
    // 列表内的 ASCII 符号 → 中文全角符号（显示）
    const mapped = FULLWIDTH_PUNCT_MAP[s]
    if (mapped) return mapped
    // 单/双引号 → 显示左引号（实际输出由 convertChar 处理 toggle）
    if (s === "'") return '\u2018'
    if (s === '"') return '\u201C'
  }
  // 满月模式：字母数字和列表外符号都转全角
  if (isFullWidth) return toFullWidth(s)
  // 半月模式：ASCII
  return s
}

// ─── 候选栏渲染 ───

/** 渲染预编辑显示区 */
export function renderCompBar(compBar: HTMLDivElement, r: RimeResult | null): void {
  if (!r || !r.composition) {
    compBar.innerHTML = ''
    compBar.classList.remove('rime-kb-compbar-visible')
    return
  }
  const comp = r.composition
  const head = comp.head ?? ''
  const body = comp.body ?? ''
  const tail = comp.tail ?? ''
  if (!head && !body && !tail) {
    compBar.innerHTML = ''
    compBar.classList.remove('rime-kb-compbar-visible')
    return
  }
  compBar.classList.add('rime-kb-compbar-visible')
  compBar.innerHTML =
    `<span class="rime-kb-comp-h">${esc(head)}</span>` +
    `<span class="rime-kb-comp-b">${esc(body)}</span>` +
    `<span class="rime-kb-comp-t">${esc(tail)}</span>`
}

/** 渲染候选栏（仅候选词 + 翻页） */
export function renderCandBar(dom: KeyboardDOM, r: RimeResult | null): void {
  if (!r || !r.candidates?.length) {
    dom.cands.innerHTML = ''
    dom.candNav.innerHTML = ''
    dom.candBar.classList.remove('rime-kb-candbar-visible')
    return
  }

  dom.candBar.classList.add('rime-kb-candbar-visible')

  const cands = r.candidates || []
  const labels = r.selectLabels || []
  const hl = r.highlighted ?? 0
  dom.cands.innerHTML = cands.map((c, i) =>
      `<span class="rime-kb-cand${i === hl ? ' rime-kb-cand-hl' : ''}" data-idx="${i}">` +
      `<span class="rime-kb-cand-lb">${esc(labels[i] || String(i + 1))}</span>` +
      esc(c.text) + '</span>'
    ).join('')

  const page = r.page || 1
  const isLast = r.isLastPage
  dom.candNav.innerHTML =
    `<button class="rime-kb-cand-nav-btn" data-dir="prev"${page <= 1 ? ' disabled' : ''}>\u25C0</button>` +
    `<span class="rime-kb-cand-page">${page}</span>` +
    `<button class="rime-kb-cand-nav-btn" data-dir="next"${isLast ? ' disabled' : ''}>\u25B6</button>`
}

// ─── 工具 ───

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

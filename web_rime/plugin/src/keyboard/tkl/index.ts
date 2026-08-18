/**
 * RimeTKLKeyboard — TKL 虚拟悬浮键盘主类
 *
 * 仅悬浮模式，无候选词栏（由外部 RimePanel 显示）。
 * 修饰键 sticky toggle，组合键发送 {Ctrl+Alt+Shift+key} 格式。
 */

import { RimeIME } from '../../ime'
import type { RimeResult, CommitCallback, RimeIMEConfig } from '../../types'
import type { TKLKeyDef, TKLKeyAction } from './layouts'
import { getTKLLayout, TKL_RIME_KEY_MAP, MODIFIER_NAMES, buildRimeCombo } from './layouts'
import { createTKLKeyboardDOM } from './dom'
import type { TKLKeyboardDOM } from './dom'
import { renderTKLKeys, isModifierAction } from './render'
import type { ModifierState, TKLShiftState } from './render'
import { TKLTouchHandler } from './touch'
import { TKLViewportController } from './viewport'
import { resolveTKLThemeVars, applyTKLThemeVars, injectTKLStyle, removeTKLStyle } from './theme'
import type { TKLTheme } from './theme'
import { FULLWIDTH_PUNCT_MAP, toFullWidth } from '../render'

/** RIME 无法处理的功能键/导航键 — 应直接处理，不走路 RIME */
const DIRECT_HANDLE_ACTIONS = new Set<TKLKeyAction>([
  'f1', 'f2', 'f3', 'f4', 'f5', 'f6',
  'f7', 'f8', 'f9', 'f10', 'f11', 'f12',
  'insert', 'delete', 'home', 'end', 'pageup', 'pagedown',
  'printscreen', 'scrolllock', 'pause',
])

export interface RimeTKLKeyboardConfig extends RimeIMEConfig {
  target: HTMLElement
  theme?: TKLTheme
  showOnFocus?: boolean
  floatingWidth?: number
  floatingHeight?: number
  eol?: string
  ime?: RimeIME
}

export class RimeTKLKeyboard {
  private ime: RimeIME
  private _ownsIME: boolean
  private target: HTMLElement
  private dom: TKLKeyboardDOM
  private touch: TKLTouchHandler
  private viewport: TKLViewportController

  private _showOnFocus: boolean
  private _floatingWidth: number
  private _floatingHeight: number
  private _eol: string
  private _currentTheme: TKLTheme
  private _visible = false
  private destroyed = false

  // 修饰键状态
  private shiftState: TKLShiftState = 'off'
  private capsActive = false
  private modifiers: ModifierState = new Set()

  // IME 状态
  private isEnglish = false
  private isFullWidth = false
  private isEnglishPunct = false
  private editing = false
  private lastResult: RimeResult | null = null

  // 智能引号 toggle 状态
  private _singleQuoteLeft = true
  private _doubleQuoteLeft = true

  // 回调
  private showCallbacks: (() => void)[] = []
  private hideCallbacks: (() => void)[] = []
  private keyPressCallbacks: ((key: string) => void)[] = []
  private commitCallbacks: CommitCallback[] = []
  private textInsertCallbacks: ((text: string) => void)[] = []
  private textDeleteCallbacks: (() => void)[] = []

  constructor(config: RimeTKLKeyboardConfig) {
    this.target = config.target
    this._showOnFocus = config.showOnFocus ?? true
    this._floatingWidth = config.floatingWidth ?? 780
    this._floatingHeight = config.floatingHeight ?? 260
    this._eol = config.eol ?? '\r'
    this._currentTheme = config.theme ?? 'dark'

    if (config.ime) {
      this.ime = config.ime
      this._ownsIME = false
    } else {
      this.ime = new RimeIME(config)
      this._ownsIME = true
    }

    // 创建 DOM
    this.dom = createTKLKeyboardDOM()

    // 初始隐藏（先加 hidden class 再挂载，避免 appendChild 时闪现）
    this.dom.container.classList.add('rime-tkl-hidden')

    // 挂载到 body
    document.body.appendChild(this.dom.container)
    document.body.appendChild(this.dom.preview)

    // 主题
    injectTKLStyle()
    const themeVars = resolveTKLThemeVars(this._currentTheme)
    applyTKLThemeVars(this.dom.container, themeVars)

    // 渲染初始键盘
    this.renderKeys()

    // 创建触摸处理器
    this.touch = new TKLTouchHandler(
      this.dom.keys, this.dom.container, this.dom,
      {
        fireKey: (kd) => this.fireKey(kd),
        haptic: () => this.haptic(),
        getKeyDef: (k) => this.getKeyDef(k),
      }
    )

    // 创建视口控制器
    this.viewport = new TKLViewportController(
      this.dom.container, this.target, this.dom.dragHandle,
      () => this._visible,
      () => this.touch.keyTouched,
      () => this._showOnFocus,
      () => { this._visible = true; this.showCallbacks.forEach(cb => cb()) },
      () => { this._visible = false; this.hideCallbacks.forEach(cb => cb()) },
      this._floatingWidth,
      this._floatingHeight,
    )

    this.viewport.setupTarget()

    // 绑定事件
    this.touch.bind()
    this.viewport.bind()
    this.bindHideBtn()
    this.bindIME()

    // 初始定位
    this.applyFloatingPosition()
  }

  async init(): Promise<void> {
    await this.ime.init()
  }

  destroy(): void {
    this.destroyed = true
    if (this._ownsIME) this.ime.destroy()
    this.touch.destroy()
    this.viewport.destroy()
    this.viewport.restoreTarget()

    this.dom.container.remove()
    this.dom.preview.remove()

    removeTKLStyle()
  }

  getIME(): RimeIME { return this.ime }
  isInitialized(): boolean { return this.ime.isInitialized() }
  getElement(): HTMLDivElement { return this.dom.container }

  show(): void {
    if (this._visible) return
    this.viewport.show()
  }

  hide(): void {
    if (!this._visible) return
    this.viewport.hide()
  }

  toggle(): void { this._visible ? this.hide() : this.show() }
  isVisible(): boolean { return this._visible }

  setTheme(theme: TKLTheme): void {
    this._currentTheme = theme
    const themeVars = resolveTKLThemeVars(theme)
    applyTKLThemeVars(this.dom.container, themeVars)
  }

  onShow(cb: () => void): void { this.showCallbacks.push(cb) }
  onHide(cb: () => void): void { this.hideCallbacks.push(cb) }
  onKeyPress(cb: (key: string) => void): void { this.keyPressCallbacks.push(cb) }
  onCommit(cb: CommitCallback): void { this.commitCallbacks.push(cb) }
  onTextInsert(cb: (text: string) => void): void { this.textInsertCallbacks.push(cb) }
  onTextDelete(cb: () => void): void { this.textDeleteCallbacks.push(cb) }

  offShow(cb: () => void): void { this.showCallbacks = this.showCallbacks.filter(c => c !== cb) }
  offHide(cb: () => void): void { this.hideCallbacks = this.hideCallbacks.filter(c => c !== cb) }
  offKeyPress(cb: (key: string) => void): void { this.keyPressCallbacks = this.keyPressCallbacks.filter(c => c !== cb) }
  offCommit(cb: CommitCallback): void { this.commitCallbacks = this.commitCallbacks.filter(c => c !== cb) }
  offTextInsert(cb: (text: string) => void): void { this.textInsertCallbacks = this.textInsertCallbacks.filter(c => c !== cb) }
  offTextDelete(cb: () => void): void { this.textDeleteCallbacks = this.textDeleteCallbacks.filter(c => c !== cb) }

  // ─── 渲染 ───

  private renderKeys(): void {
    renderTKLKeys(this.dom.keys, this.shiftState, this.capsActive, this.modifiers, this.isEnglish, this.isFullWidth, this.isEnglishPunct)
  }

  private refreshUI(): void {
    this.renderKeys()
  }

  // ─── 隐藏按钮 ───

  private bindHideBtn(): void {
    this.dom.hideBtn.addEventListener('click', (e) => {
      e.preventDefault()
      e.stopPropagation()
      this.hide()
    })
  }

  // ─── IME 事件 ───

  private bindIME(): void {
    this.ime.onOptionChange(opts => {
      if ('ascii_mode' in opts) {
        this.isEnglish = opts.ascii_mode
        if (this.isEnglish) this.shiftState = 'off'
      }
      if ('full_shape' in opts) this.isFullWidth = opts.full_shape
      if ('ascii_punct' in opts) this.isEnglishPunct = opts.ascii_punct
      this.refreshUI()
    })

    this.ime.onSchemaChange(() => {
      this.refreshUI()
    })
  }

  // ─── 按键动作 ───

  private fireKey(keyDef: TKLKeyDef): void {
    const action = keyDef.action

    // 修饰键 toggle
    if (action === 'shift') { this.handleShift(); return }
    if (action === 'ctrl') { this.toggleModifier('ctrl'); return }
    if (action === 'alt') { this.toggleModifier('alt'); return }
    if (action === 'meta') { this.toggleModifier('meta'); return }
    if (action === 'caps') { this.handleCaps(); return }
    if (action === 'lang') { this.handleLang(); return }
    if (action === 'fn') { return }

    // 构建 RIME 键名
    const rimeKey = this.buildRimeKey(keyDef)
    if (!rimeKey) return

    // 清除非锁定修饰键
    this.clearNonLockModifiers()

    // 发送按键通知（外部监听器如终端可转换为转义序列）
    this.keyPressCallbacks.forEach(cb => cb(rimeKey))

    // RIME 无法处理的键（功能键/导航键/修饰键组合）：直接处理，不走路 RIME
    if (this.isDirectHandleKey(action, rimeKey)) {
      if (this.editing && !this.isEnglish) {
        // 中文编辑态：先取消 RIME 组词，再直接处理
        this.ime.processKey('{Escape}').then(() => {
          this.editing = false
          this.handleSpecialKey(action)
        }).catch(() => {
          this.handleSpecialKey(action)
        })
      } else {
        this.handleSpecialKey(action)
      }
      return
    }

    // Escape 和方向键：编辑态走 RIME（取消组词/候选导航），非编辑态直接处理
    if (action === 'escape' || (action && action.startsWith('arrow_'))) {
      if (this.isEnglish || !this.editing) {
        this.handleSpecialKey(action)
        return
      }
      // 中文编辑态：走 RIME
    }

    // 字母键/符号键/Backspace/Enter/Space/Tab
    if (this.isEnglish || !this.editing) {
      if (this.handleDirectKey(action, rimeKey, keyDef)) return
    }

    // 中文编辑态或字母键：走 RIME
    this.ime.processKey(rimeKey).then(r => this.analyze(r, rimeKey)).catch(() => {})
  }

  /** 判断是否为 RIME 无法处理的键（功能键/导航键/修饰键组合） */
  private isDirectHandleKey(action: TKLKeyAction | undefined, rimeKey: string): boolean {
    // 修饰键组合 {Control+x}, {Alt+x} 等
    if (rimeKey.startsWith('{') && rimeKey.includes('+')) return true
    // 功能键 F1-F12 / 导航键 / 特殊键
    if (action && DIRECT_HANDLE_ACTIONS.has(action)) return true
    return false
  }

  /** 处理 RIME 无法支持的特殊键（功能键/导航键/方向键/Escape/修饰键组合）
   *
   * onKeyPress 回调已在 fireKey 中触发，外部监听器（如终端）可获取 RIME 格式键名
   * 并转换为终端转义序列。此处为 textarea/input 目标提供基本光标移动支持。
   */
  private handleSpecialKey(action: TKLKeyAction | undefined): void {
    if (!this.isTextInput(this.target)) return
    const el = this.target as HTMLTextAreaElement | HTMLInputElement
    const s = el.selectionStart ?? el.value.length
    const e = el.selectionEnd ?? s
    const v = el.value

    switch (action) {
      case 'arrow_left':
        el.selectionStart = el.selectionEnd = Math.max(0, s === e ? s - 1 : s)
        break
      case 'arrow_right':
        el.selectionStart = el.selectionEnd = Math.min(v.length, s === e ? s + 1 : e)
        break
      case 'home':
        el.selectionStart = el.selectionEnd = 0
        break
      case 'end':
        el.selectionStart = el.selectionEnd = v.length
        break
      case 'delete':
        // 向前删除：删除选区或光标后一个字符
        if (s !== e) {
          el.value = v.slice(0, s) + v.slice(e)
          el.selectionStart = el.selectionEnd = s
        } else if (e < v.length) {
          el.value = v.slice(0, s) + v.slice(e + 1)
          el.selectionStart = el.selectionEnd = s
        }
        this.target.dispatchEvent(new Event('input', { bubbles: true }))
        break
      // F1-F12, Insert, PageUp/Down, PrintScreen 等：textarea 无标准行为
      // onKeyPress 已通知外部（终端集成可转换为转义序列）
    }
    el.focus()
  }

  /** 构建发送给 RIME 引擎的键名 */
  private buildRimeKey(keyDef: TKLKeyDef): string {
    const action = keyDef.action

    // 功能键/特殊键
    if (action) {
      const rimeName = TKL_RIME_KEY_MAP[keyDef.key]
      if (rimeName) {
        // 带修饰键组合
        if (this.modifiers.size > 0) {
          const modNames: string[] = []
          this.modifiers.forEach(m => modNames.push(MODIFIER_NAMES[m]))
          return buildRimeCombo(modNames, rimeName)
        }
        return `{${rimeName}}`
      }
      // 回退：用 action 名
      return `{${action}}`
    }

    // 可打印字符键
    let ch = keyDef.key
    // Shift 激活时使用 shiftKey
    if (this.shiftState !== 'off' && keyDef.shiftKey) {
      ch = keyDef.shiftKey
    }
    // CapsLock 对字母键的影响
    if (this.capsActive && /^[a-z]$/i.test(ch)) {
      ch = ch.toUpperCase()
    }

    // 带修饰键组合
    if (this.modifiers.size > 0) {
      const rimeName = TKL_RIME_KEY_MAP[ch] || ch
      const modNames: string[] = []
      this.modifiers.forEach(m => modNames.push(MODIFIER_NAMES[m]))
      return buildRimeCombo(modNames, rimeName)
    }

    // 单字符直接返回
    return ch
  }

  /** 英文/非编辑态下直接处理的按键，返回 true 表示已处理 */
  private handleDirectKey(action: TKLKeyAction | undefined, rimeKey: string, keyDef: TKLKeyDef): boolean {
    // Backspace
    if (action === 'backspace') {
      this.deleteBackward()
      return true
    }
    // Enter
    if (action === 'enter') {
      this.insertText(this._eol)
      return true
    }
    // Space
    if (action === 'space') {
      this.insertText(this.isFullWidth ? '\u3000' : ' ')
      return true
    }
    // Tab
    if (action === 'tab') {
      this.insertText('\t')
      return true
    }
    // 其余 action 键（如 menu）已在 fireKey 中被 isDirectHandleKey / escape-arrow 检查拦截；
    // 若仍有遗漏到达此处，直接消费避免无效 RIME 调用
    if (action) {
      return true
    }
    // 字母键在中文模式非编辑态：走 RIME 启动组词
    if (!this.isEnglish && /^[a-z]$/.test(keyDef.key)) {
      return false
    }
    // 英文模式可打印字符：经过全半角转换后插入
    if (!action && rimeKey.length === 1) {
      this.insertText(this.convertChar(rimeKey))
      return true
    }
    return false
  }

  // ─── 修饰键处理 ───

  private handleShift(): void {
    if (this.shiftState === 'off') this.shiftState = 'once'
    else if (this.shiftState === 'once') this.shiftState = 'locked'
    else this.shiftState = 'off'
    this.refreshUI()
  }

  private handleCaps(): void {
    this.capsActive = !this.capsActive
    this.refreshUI()
  }

  private handleLang(): void {
    this.isEnglish = !this.isEnglish
    this.ime.setOption('ascii_mode', this.isEnglish).catch(() => {})
    if (!this.ime.punctLocked) {
      this.ime.setOption('ascii_punct', this.isEnglish).catch(() => {})
    }
    if (this.isEnglish) this.shiftState = 'off'
    this.refreshUI()
  }

  private toggleModifier(mod: string): void {
    if (this.modifiers.has(mod)) {
      this.modifiers.delete(mod)
    } else {
      this.modifiers.add(mod)
    }
    this.refreshUI()
  }

  private clearNonLockModifiers(): void {
    // Shift once 模式：按键后自动清除
    if (this.shiftState === 'once') {
      this.shiftState = 'off'
    }
    // Ctrl/Alt/Meta：按键后自动清除
    this.modifiers.clear()
    this.refreshUI()
  }

  // ─── 分析 RIME 返回结果 ───

  private analyze(r: RimeResult, rimeKey: string): void {
    this.lastResult = r
    const wasEditing = this.editing

    if (r.state === 'committed') {
      this.editing = false
      if (r.committed) {
        if (this._ownsIME) this.insertText(r.committed)
        this.commitCallbacks.forEach(cb => cb(r.committed))
      }
    } else if (r.state === 'accepted') {
      if (r.committed) {
        if (this._ownsIME) this.insertText(r.committed)
        this.commitCallbacks.forEach(cb => cb(r.committed))
      }
      this.editing = true
    } else {
      this.editing = false
      if (r.state === 'rejected' && r.updatedSchema) {
        this.ime.setIME(r.updatedSchema.split('/')[0]).then(nr => this.analyze(nr, '')).catch(() => {})
      }
      if (r.state === 'unhandled' && !wasEditing) {
        if (rimeKey === '{BackSpace}' || rimeKey === 'BackSpace') {
          this.deleteBackward()
        } else if (rimeKey === '{Return}' || rimeKey === 'Return') {
          this.insertText(this._eol)
        } else if (rimeKey.length === 1 && this.isPrintable(rimeKey)) {
          this.insertText(this.convertChar(rimeKey))
        }
      }
    }

    if (this.shiftState === 'once') { this.shiftState = 'off'; this.renderKeys() }
    this.viewport.focusTarget()
  }

  // ─── 文字操作 ───

  insertText(text: string): void {
    // insertText 被调用意味着文本已提交/插入，不再是组词态。
    // tkl+panel 模式下，面板选词触发 ime.onCommit → insertText（wirePanelTKL），
    // 但不经过 analyze，导致 editing 残留为 true，首次删除键被 RIME 吞掉。
    // 此处重置 editing 防止状态不同步。
    this.editing = false
    if (this.isTextInput(this.target)) {
      const el = this.target as HTMLTextAreaElement | HTMLInputElement
      const s = el.selectionStart ?? el.value.length
      const e = el.selectionEnd ?? s
      const v = el.value
      el.value = v.slice(0, s) + text + v.slice(e)
      el.selectionStart = el.selectionEnd = s + text.length
      this.target.dispatchEvent(new Event('input', { bubbles: true }))
    } else {
      this.textInsertCallbacks.forEach(cb => cb(text))
    }
  }

  private deleteBackward(): void {
    if (this.isTextInput(this.target)) {
      const el = this.target as HTMLTextAreaElement | HTMLInputElement
      const s = el.selectionStart ?? el.value.length
      const e = el.selectionEnd ?? s
      const v = el.value
      if (s !== e) {
        el.value = v.slice(0, s) + v.slice(e)
        el.selectionStart = el.selectionEnd = s
      } else if (s > 0) {
        el.value = v.slice(0, s - 1) + v.slice(e)
        el.selectionStart = el.selectionEnd = s - 1
      }
      this.target.dispatchEvent(new Event('input', { bubbles: true }))
    } else {
      this.textDeleteCallbacks.forEach(cb => cb())
    }
  }

  // ─── 工具 ───

  private getKeyDef(key: string): TKLKeyDef | null {
    const layout = getTKLLayout()
    for (const row of layout) {
      for (const def of row.main) {
        if (def.key === key) return def
      }
      for (const def of row.nav) {
        if (def.key === key) return def
      }
    }
    return null
  }

  private isTextInput(el: HTMLElement): el is HTMLTextAreaElement | HTMLInputElement {
    return el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement
  }

  private isPrintable(key: string): boolean {
    return /^[a-z0-9!"#$%&'()*+,./:;<=>?@[\] ^_`{|}~\\-]$/i.test(key)
  }

  /**
   * 符号/字母数字转换（不受中英文影响）。
   * 符号受 isFullWidth（满月/半月）和 isEnglishPunct（符号全半角）共同控制，全角优先：
   *   - isFullWidth=true（满月）→ 强制全部全角：列表内→中文符号，列表外→全角英文符号，字母数字→全角
   *   - isFullWidth=false + isEnglishPunct=false（符号全角）→ 仅列表内中文符号，列表外 ASCII
   *   - isFullWidth=false + isEnglishPunct=true（符号半角）→ ASCII
   * 字母数字受 isFullWidth 控制：满月→全角，半月→ASCII
   */
  private convertChar(ch: string): string {
    const useCnPunct = this.isFullWidth || !this.isEnglishPunct
    if (useCnPunct) {
      const mapped = FULLWIDTH_PUNCT_MAP[ch]
      if (mapped) return mapped
      if (ch === "'") {
        const result = this._singleQuoteLeft ? '\u2018' : '\u2019'
        this._singleQuoteLeft = !this._singleQuoteLeft
        return result
      }
      if (ch === '"') {
        const result = this._doubleQuoteLeft ? '\u201C' : '\u201D'
        this._doubleQuoteLeft = !this._doubleQuoteLeft
        return result
      }
    }
    if (this.isFullWidth) return toFullWidth(ch)
    return ch
  }

  private haptic(): void {
    try { navigator.vibrate?.(8) } catch {}
  }

  private applyFloatingPosition(): void {
    const el = this.dom.container
    el.style.width = this._floatingWidth + 'px'
    const vw = window.innerWidth
    const vh = window.innerHeight
    const w = Math.min(this._floatingWidth, vw - 16)
    const h = Math.min(this._floatingHeight, vh - 16)
    let left = (vw - w) / 2
    let top = vh - h - 40
    left = Math.max(8, Math.min(left, vw - w - 8))
    top = Math.max(8, Math.min(top, vh - h - 8))
    el.style.left = left + 'px'
    el.style.top = top + 'px'
  }
}

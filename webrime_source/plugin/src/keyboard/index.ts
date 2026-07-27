/**
 * RimeKeyboard — 虚拟悬浮键盘主类
 *
 * 薄编排层：持有状态，创建子模块，委托调用。
 * 子模块：keyboard-dom / keyboard-render / keyboard-touch / keyboard-viewport / keyboard-theme。
 */

import { RimeIME } from '../ime'
import type { RimeResult, CommitCallback } from '../types'
import { getLayout, getPageSwitchTarget } from './layouts'
import type { KeyboardPage, KeyDef } from './layouts'
import type {
  KeyboardMode, RimeKeyboardTheme, RimeKeyboardSize,
  RimeKeyboardConfig, RimeKeyboardThemeVars, KeyboardState, KeyboardDOM,
} from './types'
import { createKeyboardDOM } from './dom'
import { renderKeys, renderCandBar, renderCompBar, toFullWidth, FULLWIDTH_PUNCT_MAP } from './render'
import { KeyboardTouchHandler } from './touch'
import { KeyboardViewportController } from './viewport'
import { resolveThemeVars, applyThemeVars, injectKeyboardStyle, removeKeyboardStyle } from './theme'

const KB_STORAGE_KEY = 'rime-kb-state'

export class RimeKeyboard {

  // ─── 状态 ───

  private ime: RimeIME
  private _ownsIME: boolean
  private target: HTMLElement
  private dom: KeyboardDOM
  private touch: KeyboardTouchHandler
  private viewport: KeyboardViewportController

  private _showOnFocus: boolean
  private _floatingWidth: number
  private _floatingHeight: number
  private _hapticEnabled: boolean
  private _eol: string
  private _hideCandidateBar: boolean
  private _currentTheme: RimeKeyboardTheme
  private _currentSize: RimeKeyboardSize
  private _currentMode: KeyboardMode
  private _currentThemeVars: RimeKeyboardThemeVars

  // IME 状态
  private currentPage: KeyboardPage = 'letters'
  private shiftState: 'off' | 'once' | 'locked' = 'off'
  private isEnglish = false
  private isFullWidth = false
  private isEnglishPunct = false
  private isEmoji = false
  private isSimplification = true
  private editing = false
  private _visible = false
  private destroyed = false
  private lastResult: RimeResult | null = null

  // 智能引号 toggle 状态（独立跟踪单/双引号）
  private _singleQuoteLeft = true
  private _doubleQuoteLeft = true

  // 回调
  private showCallbacks: (() => void)[] = []
  private hideCallbacks: (() => void)[] = []
  private keyPressCallbacks: ((key: string) => void)[] = []
  private commitCallbacks: CommitCallback[] = []
  // 非 text input 目标（如终端 div）的文字插入/删除回调
  private textInsertCallbacks: ((text: string) => void)[] = []
  private textDeleteCallbacks: (() => void)[] = []

  // ─── 构造 ───

  constructor(config: RimeKeyboardConfig) {
    this.target = config.target
    this._showOnFocus = config.showOnFocus ?? true
    this._floatingWidth = config.floatingWidth ?? 320
    this._floatingHeight = config.floatingHeight ?? 220
    this._hapticEnabled = config.haptic ?? true
    this._currentTheme = config.theme ?? 'dark'
    this._currentSize = config.size ?? 'normal'
    this._currentMode = config.kbMode ?? 'docked'
    this._eol = config.eol ?? '\n'
    this._hideCandidateBar = config.hideCandidateBar ?? false

    if (config.ime) {
      this.ime = config.ime
      this._ownsIME = false
    } else {
      this.ime = new RimeIME(config)
      this._ownsIME = true
    }

    this._currentThemeVars = resolveThemeVars(
      this._currentTheme, this._currentSize, undefined
    )

    this.loadKbState()

    // 创建 DOM
    this.dom = createKeyboardDOM()
    // 模式3：隐藏键盘自带候选栏（预编辑+候选词），由外部 RimePanel 显示
    if (this._hideCandidateBar) {
      this.dom.compBar.style.display = 'none'
      this.dom.candBar.style.display = 'none'
    }
    this.applyModeToContainer()

    // 挂载到 body
    document.body.appendChild(this.dom.container)
    document.body.appendChild(this.dom.preview)
    document.body.appendChild(this.dom.alt)

    // 主题
    injectKeyboardStyle()
    applyThemeVars(this.dom.container, this._currentThemeVars)

    // 渲染初始键盘
    this.renderKeys()

    // 创建触摸处理器
    this.touch = new KeyboardTouchHandler(
      this.dom.keys, this.dom.container, this.dom.preview, this.dom.alt,
      {
        fireKey: (kd) => this.fireKey(kd),
        insertText: (t) => this.insertText(t),
        haptic: () => this.haptic(),
        getKeyDef: (k) => this.getKeyDef(k),
      }
    )

    // 创建视口控制器
    this.viewport = new KeyboardViewportController(
      this.dom.container, this.target, this.dom.dragHandle,
      () => this._currentMode,
      () => this._visible,
      () => this.touch.keyTouched,
      () => this._showOnFocus,
      () => { this._visible = true; this.showCallbacks.forEach(cb => cb()) },
      () => { this._visible = false; this.hideCallbacks.forEach(cb => cb()) },
      this._floatingWidth,
      this._floatingHeight,
    )

    // 设置目标元素
    this.viewport.setupTarget()

    // 绑定事件
    this.touch.bind()
    this.viewport.bind()
    this.bindCandBar()
    this.bindIME()
    this.bindHideBtn()

    // 初始隐藏
    this.dom.container.classList.add('rime-kb-hidden')
  }

  // ─── 公开 API ───

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
    this.dom.alt.remove()

    removeKeyboardStyle()
  }

  getIME(): RimeIME { return this.ime }
  isInitialized(): boolean { return this.ime.isInitialized() }
  /** 获取键盘容器元素（供外部设置透明度等样式） */
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

  setPage(page: KeyboardPage): void {
    this.currentPage = page
    this.renderKeys()
  }

  getPage(): KeyboardPage { return this.currentPage }

  setMode(mode: KeyboardMode): void {
    this._currentMode = mode
    this.applyModeToContainer()
  }

  getMode(): KeyboardMode { return this._currentMode }

  setTheme(theme: RimeKeyboardTheme, vars?: RimeKeyboardThemeVars): void {
    this._currentTheme = theme
    this._currentThemeVars = resolveThemeVars(theme, this._currentSize, vars)
    applyThemeVars(this.dom.container, this._currentThemeVars)
  }

  setSize(size: RimeKeyboardSize): void {
    this._currentSize = size
    this._currentThemeVars = resolveThemeVars(this._currentTheme, size)
    applyThemeVars(this.dom.container, this._currentThemeVars)
  }

  onShow(cb: () => void): void { this.showCallbacks.push(cb) }
  onHide(cb: () => void): void { this.hideCallbacks.push(cb) }
  onKeyPress(cb: (key: string) => void): void { this.keyPressCallbacks.push(cb) }
  onCommit(cb: CommitCallback): void { this.commitCallbacks.push(cb) }
  /** 当目标非 text input（如终端 div）时，文字插入会通过此回调通知外部 */
  onTextInsert(cb: (text: string) => void): void { this.textInsertCallbacks.push(cb) }
  /** 当目标非 text input（如终端 div）时，退格删除会通过此回调通知外部 */
  onTextDelete(cb: () => void): void { this.textDeleteCallbacks.push(cb) }

  offShow(cb: () => void): void { this.showCallbacks = this.showCallbacks.filter(c => c !== cb) }
  offHide(cb: () => void): void { this.hideCallbacks = this.hideCallbacks.filter(c => c !== cb) }
  offKeyPress(cb: (key: string) => void): void { this.keyPressCallbacks = this.keyPressCallbacks.filter(c => c !== cb) }
  offCommit(cb: CommitCallback): void { this.commitCallbacks = this.commitCallbacks.filter(c => c !== cb) }
  offTextInsert(cb: (text: string) => void): void { this.textInsertCallbacks = this.textInsertCallbacks.filter(c => c !== cb) }
  offTextDelete(cb: () => void): void { this.textDeleteCallbacks = this.textDeleteCallbacks.filter(c => c !== cb) }

  // ─── 渲染 ───

  private renderKeys(): void {
    renderKeys(this.dom.keys, this.currentPage, this.shiftState, this.isEnglish, this.isEnglishPunct, this.isFullWidth)
  }

  private renderCand(r: RimeResult | null): void {
    // 模式3：候选栏已隐藏，由外部 RimePanel 显示，无需更新 DOM 或调整高度
    if (this._hideCandidateBar) return
    const savedH = this.dom.container.style.height
    const savedMaxH = this.dom.container.style.maxHeight
    const savedMaxVal = parseFloat(savedMaxH) || 0
    // 临时清除 height 和 maxHeight 以测量内容自然高度
    // maxHeight 会将 offsetHeight 截断为 min(自然高度, maxHeight)，
    // 当容器已触达 maxHeight 时 delta 计算为 0，候选栏出现后按键区被
    // flex 收缩 + overflow:hidden 裁剪，导致底部按键行被挤下去
    this.dom.container.style.height = ''
    this.dom.container.style.maxHeight = ''
    const prevNaturalH = this.dom.container.offsetHeight
    // 清除 maxHeight 前的可见高度（受 maxHeight 截断）
    const prevVisibleH = savedMaxVal > 0 ? Math.min(prevNaturalH, savedMaxVal) : prevNaturalH
    renderCompBar(this.dom.compBar, r)
    renderCandBar(this.dom, r)
    const newNaturalH = this.dom.container.offsetHeight
    // 恢复 maxHeight；若内容自然高度超过原 maxHeight，则扩大以容纳候选栏，
    // 避免容器仍被 maxHeight 截断导致按键区被挤压
    if (savedMaxVal > 0 && newNaturalH > savedMaxVal) {
      this.dom.container.style.maxHeight = newNaturalH + 'px'
    } else {
      this.dom.container.style.maxHeight = savedMaxH
    }
    // delta 基于可见高度变化，保持底边不动
    const newVisibleH = newNaturalH
    const delta = newVisibleH - prevVisibleH
    if (savedH) {
      this.dom.container.style.height = (parseFloat(savedH) + delta) + 'px'
    }
    if (delta !== 0) {
      const cur = parseFloat(this.dom.container.style.top) || 0
      this.dom.container.style.top = (cur - delta) + 'px'
    }
    this.clampToViewport()
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

  // ─── 候选栏事件 ───

  private bindCandBar(): void {
    this.dom.cands.addEventListener('click', (e) => {
      const t = e.target as HTMLElement
      const cand = t.closest('.rime-kb-cand') as HTMLElement
      if (cand && cand.dataset.idx != null) {
        this.ime.selectCandidate(parseInt(cand.dataset.idx, 10))
          .then(r => this.analyze(r, ''))
          .catch(() => {})
      }
    })

    this.dom.candNav.addEventListener('click', (e) => {
      const t = e.target as HTMLElement
      const btn = t.closest('.rime-kb-cand-nav-btn') as HTMLElement
      if (btn && btn.dataset.dir) {
        this.ime.changePage(btn.dataset.dir === 'prev')
          .then(r => this.analyze(r, ''))
          .catch(() => {})
      }
    })
  }

  // ─── IME 事件 ───

  private bindIME(): void {
    this.ime.onOptionChange(opts => {
      console.log('[RimeKB] onOptionChange', opts)
      if ('ascii_mode' in opts) {
        this.isEnglish = opts.ascii_mode
        if (this.isEnglish) this.shiftState = 'off'
      }
      if ('full_shape' in opts) this.isFullWidth = opts.full_shape
      if ('ascii_punct' in opts) this.isEnglishPunct = opts.ascii_punct
      if ('emoji_suggestion' in opts) this.isEmoji = opts.emoji_suggestion
      if ('simplification' in opts) this.isSimplification = opts.simplification
      console.log('[RimeKB] state after change', { isFullWidth: this.isFullWidth, isEnglish: this.isEnglish, isEnglishPunct: this.isEnglishPunct })
      this.saveKbState()
      this.refreshUI()
    })

    this.ime.onSchemaChange(() => {
      this.saveKbState()
      this.refreshUI()
    })
  }

  // ─── 按键动作 ───

  private fireKey(keyDef: KeyDef): void {
    const action = keyDef.action
    console.log('[RimeKB] fireKey START', { action, key: keyDef.key, isFullWidth: this.isFullWidth, isEnglish: this.isEnglish, editing: this.editing })

    if (action === 'shift') { this.handleShift(); return }
    if (action === 'page') {
      this.currentPage = keyDef.page ?? getPageSwitchTarget(this.currentPage)
      this.renderKeys()
      return
    }
    if (action === 'lang') {
      this.isEnglish = !this.isEnglish
      this.ime.setOption('ascii_mode', this.isEnglish).catch(() => {})
      // 切换语言时自动设置标点：中→中文标点(。)，En→英文标点(.)
      // 标点锁定时不自动改变 ascii_punct
      if (!this.ime.punctLocked) {
        this.ime.setOption('ascii_punct', this.isEnglish).catch(() => {})
      }
      if (this.isEnglish) this.shiftState = 'off'
      this.refreshUI()
      return
    }

    let rimeKey = ''
    if (action === 'backspace') rimeKey = '{BackSpace}'
    else if (action === 'enter') rimeKey = '{Return}'
    else if (action === 'space') rimeKey = ' '
    else if (action === 'punct') rimeKey = '.'
    else rimeKey = this.resolveCharKey(keyDef)

    if (!rimeKey) return

    if (this.isEnglish || !this.editing) {
      if (rimeKey === '{BackSpace}') { this.deleteBackward(); return }
      if (rimeKey === '{Return}') { this.insertText(this._eol); return }
      if (rimeKey === ' ') { this.insertText(this.isFullWidth ? '\u3000' : ' '); return }
      if (action === 'punct') {
        // 标点键：满月或符号全角 → 。, 符号半角 → .
        const useCnPunct = this.isFullWidth || !this.isEnglishPunct
        const out = useCnPunct ? '。' : '.'
        console.log('[RimeKB] fireKey punct branch', { isFullWidth: this.isFullWidth, isEnglishPunct: this.isEnglishPunct, out })
        this.insertText(out); return
      }
      if (!this.editing && /^[a-zA-Z]$/.test(rimeKey)) {
        // 中文非编辑态，字母走 RIME 启动组词（RIME 内部 full_shape 选项负责全角输出）
        console.log('[RimeKB] fireKey letter→RIME branch', { rimeKey })
      } else {
        // 英文模式直接输入，或中文模式非字母键（符号/数字键）
        // 符号受 isFullWidth 和 isEnglishPunct 共同控制，全角优先；字母数字受 isFullWidth 控制
        const out = this.convertChar(rimeKey)
        console.log('[RimeKB] fireKey symbol branch', { rimeKey, out })
        this.insertText(out); return
      }
    }

    console.log('[RimeKB] fireKey → RIME processKey', { rimeKey, isEnglish: this.isEnglish, editing: this.editing })
    this.keyPressCallbacks.forEach(cb => cb(rimeKey))
    this.ime.processKey(rimeKey).then(r => this.analyze(r, rimeKey)).catch(() => {})
  }

  private resolveCharKey(keyDef: KeyDef): string {
    if (this.shiftState !== 'off' && keyDef.shiftKey) {
      console.log('[RimeKB] resolveCharKey shift', { keyDefKey: keyDef.key, shiftKey: keyDef.shiftKey })
      return keyDef.shiftKey
    }
    // 符号只受 isFullWidth（半月/满月）控制，不再返回 cnKey
    // 满月时由 fireKey 的 toFullWidthPunct 统一转换为中文全角符号
    console.log('[RimeKB] resolveCharKey', { keyDefKey: keyDef.key, returns: keyDef.key })
    return keyDef.key
  }

  /**
   * 符号/字母数字转换（不受中英文影响）。
   * 符号受 isFullWidth（满月/半月）和 isEnglishPunct（符号全半角）共同控制，全角优先：
   *   - isFullWidth=true（满月）→ 强制全部全角：列表内→中文符号，列表外→全角英文符号（＠＆等），字母数字→全角
   *   - isFullWidth=false + isEnglishPunct=false（符号全角）→ 仅列表内中文符号，列表外 ASCII
   *   - isFullWidth=false + isEnglishPunct=true（符号半角）→ ASCII
   * 字母数字受 isFullWidth 控制：满月→全角，半月→ASCII
   */
  private convertChar(ch: string): string {
    const useCnPunct = this.isFullWidth || !this.isEnglishPunct
    if (useCnPunct) {
      const mapped = FULLWIDTH_PUNCT_MAP[ch]
      if (mapped) {
        console.log('[RimeKB] convertChar mapped', { ch, mapped, isFullWidth: this.isFullWidth, isEnglishPunct: this.isEnglishPunct })
        return mapped
      }
      if (ch === "'") {
        const result = this._singleQuoteLeft ? '\u2018' : '\u2019'
        this._singleQuoteLeft = !this._singleQuoteLeft
        console.log('[RimeKB] convertChar singleQuote', { ch, result })
        return result
      }
      if (ch === '"') {
        const result = this._doubleQuoteLeft ? '\u201C' : '\u201D'
        this._doubleQuoteLeft = !this._doubleQuoteLeft
        console.log('[RimeKB] convertChar doubleQuote', { ch, result })
        return result
      }
    }
    // 满月模式：字母数字和列表外符号都转全角
    if (this.isFullWidth) {
      const result = toFullWidth(ch)
      console.log('[RimeKB] convertChar fullwidth', { ch, result })
      return result
    }
    // 半月模式：ASCII
    console.log('[RimeKB] convertChar ascii', { ch, useCnPunct, isFullWidth: this.isFullWidth, isEnglishPunct: this.isEnglishPunct })
    return ch
  }

  private handleShift(): void {
    if (this.isEnglish) {
      this.shiftState = this.shiftState === 'off' ? 'once'
        : this.shiftState === 'once' ? 'locked' : 'off'
    } else {
      this.isEnglish = !this.isEnglish
      this.ime.setOption('ascii_mode', this.isEnglish).catch(() => {})
      // 切换语言时自动设置标点：中→中文标点(。)，En→英文标点(.)
      // 标点锁定时不自动改变 ascii_punct
      if (!this.ime.punctLocked) {
        this.ime.setOption('ascii_punct', this.isEnglish).catch(() => {})
      }
      this.shiftState = 'off'
    }
    this.refreshUI()
  }

  /** 分析 RIME 返回结果 */
  private analyze(r: RimeResult, rimeKey: string): void {
    this.lastResult = r
    const wasEditing = this.editing

    if (r.state === 'committed') {
      this.editing = false
      if (r.committed) {
        if (this._ownsIME) this.insertText(r.committed)
        this.commitCallbacks.forEach(cb => cb(r.committed))
      }
      this.renderCand(null)
    } else if (r.state === 'accepted') {
      if (r.committed) {
        if (this._ownsIME) this.insertText(r.committed)
        this.commitCallbacks.forEach(cb => cb(r.committed))
      }
      this.editing = true
      this.renderCand(r)
    } else {
      this.editing = false
      this.renderCand(null)
      if (r.state === 'rejected' && r.updatedSchema) {
        this.ime.setIME(r.updatedSchema.split('/')[0]).then(nr => this.analyze(nr, '')).catch(() => {})
      }
      if (r.state === 'unhandled' && !wasEditing) {
        if (rimeKey === '{BackSpace}') {
          this.deleteBackward()
        } else if (rimeKey === '{Return}') {
          this.insertText(this._eol)
        } else if (rimeKey.length === 1 && this.isPrintable(rimeKey)) {
          // RIME 未处理的可打印字符直接插入：isFullWidth 时转全角
          this.insertText(this.isFullWidth ? toFullWidth(rimeKey) : rimeKey)
        }
      }
    }

    if (this.shiftState === 'once') { this.shiftState = 'off'; this.renderKeys() }
    this.viewport.focusTarget()
  }

  // ─── 工具 ───

  private getKeyDef(key: string): KeyDef | null {
    const layout = getLayout(this.currentPage)
    for (const row of layout) {
      for (const def of row) {
        if (def.key === key) return def
      }
    }
    return null
  }

  insertText(text: string): void {
    // insertText 被调用意味着文本已提交/插入，不再是组词态。
    // panel+keyboard 模式下同样存在面板选词后 editing 残留问题，此处统一重置。
    this.editing = false
    console.log('[RimeKB] insertText', JSON.stringify(text))
    if (this.isTextInput(this.target)) {
      const el = this.target as HTMLTextAreaElement | HTMLInputElement
      const s = el.selectionStart ?? el.value.length
      const e = el.selectionEnd ?? s
      const v = el.value
      el.value = v.slice(0, s) + text + v.slice(e)
      el.selectionStart = el.selectionEnd = s + text.length
      this.target.dispatchEvent(new Event('input', { bubbles: true }))
    } else {
      // 非 text input 目标（如终端 div），通过回调通知外部处理
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
      // 非 text input 目标（如终端 div），通过回调通知外部处理
      this.textDeleteCallbacks.forEach(cb => cb())
    }
  }

  private isTextInput(el: HTMLElement): el is HTMLTextAreaElement | HTMLInputElement {
    return el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement
  }

  private isPrintable(key: string): boolean {
    return /^[a-z0-9!"#$%&'()*+,./:;<=>?@[\] ^_`{|}~\\-]$/i.test(key)
  }

  private haptic(): void {
    if (!this._hapticEnabled) return
    try { navigator.vibrate?.(8) } catch {}
  }

  private applyModeToContainer(): void {
    const el = this.dom.container
    el.classList.toggle('rime-kb-docked', this._currentMode === 'docked')
    el.classList.toggle('rime-kb-floating', this._currentMode === 'floating')
    if (this._currentMode === 'floating') {
      el.style.width = this._floatingWidth + 'px'
      if (!el.style.left) {
        const vw = window.innerWidth
        const vh = window.innerHeight
        const w = Math.min(this._floatingWidth, vw - 16)
        const h = Math.min(this._floatingHeight, vh - 16)
        let left = (vw - w) / 2
        let top = vh - h - 80
        left = Math.max(8, Math.min(left, vw - w - 8))
        top = Math.max(8, Math.min(top, vh - h - 8))
        el.style.left = left + 'px'
        el.style.top = top + 'px'
      }
    } else {
      el.style.left = ''
      el.style.top = ''
      el.style.width = ''
    }
  }

  private clampToViewport(): void {
    if (this._currentMode === 'docked') return
    const el = this.dom.container
    requestAnimationFrame(() => {
      const rect = el.getBoundingClientRect()
      if (rect.top < 0) {
        const currentTop = parseFloat(el.style.top) || 0
        el.style.top = (currentTop - rect.top) + 'px'
      }
    })
  }

  private saveKbState(): void {
    try {
      localStorage.setItem(KB_STORAGE_KEY, JSON.stringify({
        isEnglish: this.isEnglish,
        isEnglishPunct: this.isEnglishPunct,
        isSimplification: this.isSimplification,
        isFullWidth: this.isFullWidth,
        isEmoji: this.isEmoji,
        currentPage: this.currentPage,
        mode: this._currentMode,
        size: this._currentSize,
      }))
    } catch {}
  }

  private loadKbState(): void {
    try {
      const raw = localStorage.getItem(KB_STORAGE_KEY)
      if (!raw) return
      const s = JSON.parse(raw)
      if (typeof s.isEnglish === 'boolean') this.isEnglish = s.isEnglish
      if (typeof s.isEnglishPunct === 'boolean') this.isEnglishPunct = s.isEnglishPunct
      if (typeof s.isSimplification === 'boolean') this.isSimplification = s.isSimplification
      if (typeof s.isFullWidth === 'boolean') this.isFullWidth = s.isFullWidth
      if (typeof s.isEmoji === 'boolean') this.isEmoji = s.isEmoji
      if (s.currentPage) this.currentPage = s.currentPage
      if (s.mode) this._currentMode = s.mode
      if (s.size) this._currentSize = s.size
      // 注意：theme 不从 localStorage 恢复，主题是页面级设置，
      // 由 config.theme（ensurePanel 读取 body dataset）和 setTheme() 控制
    } catch {}
  }
}

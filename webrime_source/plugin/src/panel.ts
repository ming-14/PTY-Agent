import { RimeIME } from './ime'
import type { RimeResult, RimeIMEConfig, CommitCallback, OptionChangeCallback, SchemaChangeCallback, ErrorCallback, DeployStatusCallback, ResultChangeCallback } from './types'

export type { RimeIME as RimeIMEType } from './ime'

export type RimeTheme = 'dark' | 'light'

export interface RimePanelThemeVars {
  panelBg?: string
  panelBorder?: string
  panelRadius?: string
  panelShadow?: string
  panelFontFamily?: string
  panelMaxWidth?: string
  panelZIndex?: number
  compFontSize?: string
  compPadding?: string
  compHeadColor?: string
  compBodyColor?: string
  compTailColor?: string
  candFontSize?: string
  candPadding?: string
  candGap?: string
  candColor?: string
  candHoverBg?: string
  candHighlightBg?: string
  labelColor?: string
  labelFontSize?: string
  commentColor?: string
  commentFontSize?: string
  navBtnBg?: string
  navBtnColor?: string
  navBtnSize?: string
  navPageColor?: string
  navPageFontSize?: string
}

export type RimePanelSize = 'compact' | 'normal' | 'large'

export interface RimePanelConfig extends RimeIMEConfig {
  target: HTMLElement
  theme?: RimeTheme
  themeVars?: RimePanelThemeVars
  size?: RimePanelSize
  pageSize?: number
  showComment?: boolean
  showNavigation?: boolean
  vertical?: boolean
  positionOffset?: { x?: number; y?: number }
  className?: string
  style?: Partial<CSSStyleDeclaration>
  /** 外部按键处理模式：true 时跳过绑定 keydown/keyup/compositionstart/input 监听器，
   * 由外部（如 rimeManager）通过 handleKey() 调用。避免重复处理导致字符加倍。 */
  externalKeyHandling?: boolean
  /** 外部 IME 实例：传入时 Panel 不创建自己的 RimeIME，而是共享此实例。
   * 用于 RimeManager 模式2（Panel+Keyboard 共享同一 IME）。 */
  ime?: RimeIME
  /** 仅渲染模式：true 时 Panel 只负责显示候选词，不执行文字插入。
   * 用于 RimeManager 模式2（由 Keyboard 负责文字插入）。 */
  renderOnly?: boolean
}

const THEME_PRESETS: Record<RimeTheme, RimePanelThemeVars> = {
  dark: {
    panelBg: 'rgba(24,24,28,.96)',
    panelBorder: 'rgba(255,255,255,.08)',
    panelRadius: '6px',
    panelShadow: 'drop-shadow(0 2px 8px rgba(0,0,0,.45))',
    compHeadColor: '#63e2b7',
    compBodyColor: '#70c0e8',
    compTailColor: 'rgba(255,255,255,.82)',
    candColor: 'rgba(255,255,255,.82)',
    candHoverBg: 'rgba(255,255,255,.08)',
    candHighlightBg: 'rgba(255,255,255,.08)',
    labelColor: '#70c0e8',
    commentColor: 'rgba(255,255,255,.38)',
    navBtnBg: 'rgba(255,255,255,.06)',
    navBtnColor: '#70c0e8',
    navPageColor: 'rgba(255,255,255,.38)'
  },
  light: {
    panelBg: 'rgba(255,255,255,.98)',
    panelBorder: 'rgba(0,0,0,.12)',
    panelRadius: '6px',
    panelShadow: 'drop-shadow(0 2px 8px rgba(0,0,0,.15))',
    compHeadColor: '#18a058',
    compBodyColor: '#2080f0',
    compTailColor: 'rgba(0,0,0,.82)',
    candColor: 'rgba(0,0,0,.82)',
    candHoverBg: 'rgba(0,0,0,.06)',
    candHighlightBg: 'rgba(0,0,0,.06)',
    labelColor: '#2080f0',
    commentColor: 'rgba(0,0,0,.38)',
    navBtnBg: 'rgba(0,0,0,.04)',
    navBtnColor: '#2080f0',
    navPageColor: 'rgba(0,0,0,.38)'
  }
}

const SIZE_PRESETS: Record<RimePanelSize, Partial<RimePanelThemeVars>> = {
  compact: {
    compFontSize: '12px',
    compPadding: '2px 6px',
    candFontSize: '12px',
    candPadding: '2px 6px',
    candGap: '1px',
    labelFontSize: '10px',
    commentFontSize: '10px',
    navBtnSize: '16px',
    navPageFontSize: '10px'
  },
  normal: {
    compFontSize: '16px',
    compPadding: '4px 12px',
    candFontSize: '16px',
    candPadding: '4px 10px',
    candGap: '3px',
    labelFontSize: '13px',
    commentFontSize: '13px',
    navBtnSize: '22px',
    navPageFontSize: '12px'
  },
  large: {
    compFontSize: '20px',
    compPadding: '6px 16px',
    candFontSize: '20px',
    candPadding: '6px 14px',
    candGap: '4px',
    labelFontSize: '16px',
    commentFontSize: '16px',
    navBtnSize: '28px',
    navPageFontSize: '14px'
  }
}

export class RimePanel {
  private ime: RimeIME
  private _ownsIME: boolean
  private _renderOnly: boolean
  private target: HTMLElement
  private floatEl: HTMLDivElement
  private compEl: HTMLDivElement
  private candsEl: HTMLDivElement
  lastResult: RimeResult | null = null
  private isEnglish = false
  private isFullWidth = false
  private isEnglishPunct = false
  private isEmoji = false
  private destroyed = false
  private _showComment: boolean
  private _showNavigation: boolean
  private _vertical: boolean
  private _positionOffset: { x: number; y: number }
  private _currentTheme: RimeTheme
  private _currentThemeVars: RimePanelThemeVars
  private _currentSize: RimePanelSize
  private _className: string
  private _externalKeyHandling: boolean

  private static STYLE_ID = 'rime-panel-style'
  private static instanceCount = 0

  constructor(config: RimePanelConfig) {
    this.target = config.target
    if (config.ime) {
      this.ime = config.ime
      this._ownsIME = false
    } else {
      this.ime = new RimeIME(config)
      this._ownsIME = true
    }
    this._renderOnly = config.renderOnly ?? false

    this._currentTheme = config.theme ?? 'dark'
    this._currentSize = config.size ?? 'normal'
    this._showComment = config.showComment ?? true
    this._showNavigation = config.showNavigation ?? true
    this._vertical = config.vertical ?? false
    this._positionOffset = {
      x: config.positionOffset?.x ?? 0,
      y: config.positionOffset?.y ?? 2
    }
    this._className = config.className ?? ''
    this._externalKeyHandling = config.externalKeyHandling ?? false

    this._currentThemeVars = this.resolveThemeVars(this._currentTheme, this._currentSize, config.themeVars)

    if (this.target instanceof HTMLTextAreaElement || this.target instanceof HTMLInputElement) {
      this.savedInputMode = this.target.getAttribute('inputmode')
      this.savedAutoComplete = this.target.getAttribute('autocomplete')
      this.target.setAttribute('inputmode', 'none')
      this.target.setAttribute('autocomplete', 'off')
    }

    this.floatEl = document.createElement('div')
    this.floatEl.className = 'rime-panel' + (this._className ? ' ' + this._className : '')
    this.floatEl.style.display = 'none'
    if (config.style) Object.assign(this.floatEl.style, config.style)

    this.compEl = document.createElement('div')
    this.compEl.className = 'rime-comp'

    this.candsEl = document.createElement('div')
    this.candsEl.className = 'rime-cands'

    this.floatEl.appendChild(this.compEl)
    this.floatEl.appendChild(this.candsEl)
    document.body.appendChild(this.floatEl)

    this.injectStyle()
    this.applyThemeVars()
    this.bindIME()
    this.bindTarget()
    this.bindDrag()
  }

  async init(): Promise<void> {
    await this.ime.init()
  }

  destroy(): void {
    this.destroyed = true
    if (this._ownsIME) this.ime.destroy()
    this.floatEl.remove()
    // 清理拖拽监听
    if (this._dragMoveHandler) document.removeEventListener('mousemove', this._dragMoveHandler)
    if (this._dragTouchMoveHandler) document.removeEventListener('touchmove', this._dragTouchMoveHandler)
    if (this._dragEndHandler) {
      document.removeEventListener('mouseup', this._dragEndHandler)
      document.removeEventListener('touchend', this._dragEndHandler)
    }
    if (this.target instanceof HTMLTextAreaElement || this.target instanceof HTMLInputElement) {
      if (this.savedInputMode !== null) {
        this.target.setAttribute('inputmode', this.savedInputMode)
      } else {
        this.target.removeAttribute('inputmode')
      }
      if (this.savedAutoComplete !== null) {
        this.target.setAttribute('autocomplete', this.savedAutoComplete)
      } else {
        this.target.removeAttribute('autocomplete')
      }
    }
    RimePanel.instanceCount--
    if (RimePanel.instanceCount <= 0) {
      const st = document.getElementById(RimePanel.STYLE_ID)
      if (st) st.remove()
      RimePanel.instanceCount = 0
    }
  }

  getIME(): RimeIME { return this.ime }

  getState() { return this.ime.getState() }
  getCandidates() { return this.ime.getCandidates() }
  getComposition() { return this.ime.getComposition() }
  getCurrentSchema() { return this.ime.getCurrentSchema() }
  getPageSize() { return this.ime.getPageSize() }
  isInitialized() { return this.ime.isInitialized() }
  getTheme(): RimeTheme { return this._currentTheme }
  getSize(): RimePanelSize { return this._currentSize }
  getShowComment(): boolean { return this._showComment }
  getShowNavigation(): boolean { return this._showNavigation }
  getVertical(): boolean { return this._vertical }

  async processKey(key: string) { return this.ime.processKey(key) }

  /** 处理一个按键并自动执行 analyze 流程，供外部（如虚拟键盘）调用 */
  async handleKey(rimeKey: string): Promise<RimeResult | null> {
    try {
      const r = await this.ime.processKey(rimeKey)
      this.analyze(r, rimeKey)
      return r
    } catch {
      return null
    }
  }

  async selectCandidate(index: number) { return this.ime.selectCandidate(index) }
  async changePage(backward: boolean) { return this.ime.changePage(backward) }
  async setOption(option: string, value: boolean) { return this.ime.setOption(option, value) }
  async setIME(schema: string) { return this.ime.setIME(schema) }
  async setPageSize(size: number) { return this.ime.setPageSize(size) }
  async deploy() { return this.ime.deploy() }

  setTheme(theme: RimeTheme, vars?: RimePanelThemeVars): void {
    this._currentTheme = theme
    this._currentThemeVars = this.resolveThemeVars(theme, this._currentSize, vars)
    this.applyThemeVars()
  }

  setSize(size: RimePanelSize): void {
    this._currentSize = size
    this._currentThemeVars = this.resolveThemeVars(this._currentTheme, size)
    this.applyThemeVars()
  }

  setThemeVar(key: keyof RimePanelThemeVars, value: string | number | undefined): void {
    if (value === undefined) return
    ;(this._currentThemeVars as any)[key] = value
    this.applyThemeVars()
  }

  setShowComment(show: boolean): void { this._showComment = show }
  setShowNavigation(show: boolean): void { this._showNavigation = show }
  setVertical(vertical: boolean): void {
    this._vertical = vertical
    this.candsEl.style.flexDirection = vertical ? 'column' : 'row'
  }
  setPositionOffset(offset: { x?: number; y?: number }): void {
    if (offset.x !== undefined) this._positionOffset.x = offset.x
    if (offset.y !== undefined) this._positionOffset.y = offset.y
  }

  onCommit(cb: CommitCallback) { this.ime.onCommit(cb) }
  onOptionChange(cb: OptionChangeCallback) { this.ime.onOptionChange(cb) }
  onSchemaChange(cb: SchemaChangeCallback) { this.ime.onSchemaChange(cb) }
  onError(cb: ErrorCallback) { this.ime.onError(cb) }
  onDeployStatus(cb: DeployStatusCallback) { this.ime.onDeployStatus(cb) }
  onResultChange(cb: ResultChangeCallback) { this.ime.onResultChange(cb) }

  offCommit(cb: CommitCallback) { this.ime.offCommit(cb) }
  offOptionChange(cb: OptionChangeCallback) { this.ime.offOptionChange(cb) }
  offSchemaChange(cb: SchemaChangeCallback) { this.ime.offSchemaChange(cb) }
  offError(cb: ErrorCallback) { this.ime.offError(cb) }
  offDeployStatus(cb: DeployStatusCallback) { this.ime.offDeployStatus(cb) }
  offResultChange(cb: ResultChangeCallback) { this.ime.offResultChange(cb) }

  show() { this.floatEl.style.display = 'flex'; this.position() }
  hide() { this.floatEl.style.display = 'none' }

  /** 外部驱动渲染：仅更新候选词显示和 editing 状态，不插入文字。
   * 供 RimeManager 在 renderOnly 模式下使用。 */
  renderResult(r: RimeResult): void {
    this.analyze(r, '')
  }

  private resolveThemeVars(theme: RimeTheme, size: RimePanelSize, overrides?: RimePanelThemeVars): RimePanelThemeVars {
    const base = { ...THEME_PRESETS[theme] }
    const sizeVars = { ...SIZE_PRESETS[size] }
    const defaults: RimePanelThemeVars = {
      panelBg: 'rgba(24,24,28,.96)',
      panelBorder: 'rgba(255,255,255,.08)',
      panelRadius: '6px',
      panelShadow: 'drop-shadow(0 2px 8px rgba(0,0,0,.45))',
      panelFontFamily: '-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif',
      panelMaxWidth: '480px',
      panelZIndex: 99999,
      compFontSize: '14px',
      compPadding: '4px 10px',
      compHeadColor: '#63e2b7',
      compBodyColor: '#70c0e8',
      compTailColor: 'rgba(255,255,255,.82)',
      candFontSize: '14px',
      candPadding: '3px 8px',
      candGap: '2px',
      candColor: 'rgba(255,255,255,.82)',
      candHoverBg: 'rgba(255,255,255,.08)',
      candHighlightBg: 'rgba(255,255,255,.08)',
      labelColor: '#70c0e8',
      labelFontSize: '12px',
      commentColor: 'rgba(255,255,255,.38)',
      commentFontSize: '12px',
      navBtnBg: 'rgba(255,255,255,.06)',
      navBtnColor: '#70c0e8',
      navBtnSize: '20px',
      navPageColor: 'rgba(255,255,255,.38)',
      navPageFontSize: '11px'
    }
    return { ...defaults, ...base, ...sizeVars, ...overrides }
  }

  private applyThemeVars(): void {
    const v = this._currentThemeVars
    const s = this.floatEl.style
    s.setProperty('--rime-panel-bg', v.panelBg ?? '')
    s.setProperty('--rime-panel-border', v.panelBorder ?? '')
    s.setProperty('--rime-panel-radius', v.panelRadius ?? '')
    s.setProperty('--rime-panel-shadow', v.panelShadow ?? '')
    s.setProperty('--rime-panel-font-family', v.panelFontFamily ?? '')
    s.setProperty('--rime-panel-max-width', v.panelMaxWidth ?? '')
    s.setProperty('--rime-panel-z-index', String(v.panelZIndex ?? 99999))
    s.setProperty('--rime-comp-font-size', v.compFontSize ?? '')
    s.setProperty('--rime-comp-padding', v.compPadding ?? '')
    s.setProperty('--rime-comp-head-color', v.compHeadColor ?? '')
    s.setProperty('--rime-comp-body-color', v.compBodyColor ?? '')
    s.setProperty('--rime-comp-tail-color', v.compTailColor ?? '')
    s.setProperty('--rime-cand-font-size', v.candFontSize ?? '')
    s.setProperty('--rime-cand-padding', v.candPadding ?? '')
    s.setProperty('--rime-cand-gap', v.candGap ?? '')
    s.setProperty('--rime-cand-color', v.candColor ?? '')
    s.setProperty('--rime-cand-hover-bg', v.candHoverBg ?? '')
    s.setProperty('--rime-cand-highlight-bg', v.candHighlightBg ?? '')
    s.setProperty('--rime-label-color', v.labelColor ?? '')
    s.setProperty('--rime-label-font-size', v.labelFontSize ?? '')
    s.setProperty('--rime-comment-color', v.commentColor ?? '')
    s.setProperty('--rime-comment-font-size', v.commentFontSize ?? '')
    s.setProperty('--rime-nav-btn-bg', v.navBtnBg ?? '')
    s.setProperty('--rime-nav-btn-color', v.navBtnColor ?? '')
    s.setProperty('--rime-nav-btn-size', v.navBtnSize ?? '')
    s.setProperty('--rime-nav-page-color', v.navPageColor ?? '')
    s.setProperty('--rime-nav-page-font-size', v.navPageFontSize ?? '')
  }

  private injectStyle() {
    if (document.getElementById(RimePanel.STYLE_ID)) {
      RimePanel.instanceCount++
      return
    }
    const s = document.createElement('style')
    s.id = RimePanel.STYLE_ID
    s.textContent = `
.rime-panel{position:fixed;z-index:var(--rime-panel-z-index,99999);pointer-events:auto;display:flex;flex-direction:column;min-width:40px;max-width:var(--rime-panel-max-width,480px);filter:var(--rime-panel-shadow,drop-shadow(0 2px 8px rgba(0,0,0,.45)));font-family:var(--rime-panel-font-family,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif);opacity:var(--ime-panel-opacity,1);transform:scale(var(--ime-panel-scale,1));transform-origin:top left}
.rime-comp{background:var(--rime-panel-bg,rgba(24,24,28,.96));border-radius:var(--rime-panel-radius,6px) var(--rime-panel-radius,6px) 0 0;padding:var(--rime-comp-padding,4px 10px);font-size:calc(var(--rime-comp-font-size,14px) * var(--ime-panel-scale,1));display:flex;align-items:center;gap:1px;border:1px solid var(--rime-panel-border,rgba(255,255,255,.08));border-bottom:none}
.rime-comp:empty{display:none;border:none;padding:0}
.rime-comp:empty+.rime-cands{border-radius:var(--rime-panel-radius,6px)}
.rime-ch{color:var(--rime-comp-head-color,#63e2b7)}.rime-cb{color:var(--rime-comp-body-color,#70c0e8);text-decoration:underline}.rime-ct{color:var(--rime-comp-tail-color,rgba(255,255,255,.82))}
.rime-cands{background:var(--rime-panel-bg,rgba(24,24,28,.96));border:1px solid var(--rime-panel-border,rgba(255,255,255,.08));border-radius:0 0 var(--rime-panel-radius,6px) var(--rime-panel-radius,6px);padding:4px 6px;display:flex;flex-wrap:wrap;gap:var(--rime-cand-gap,2px)}
.rime-comp:empty+.rime-cands{border-radius:var(--rime-panel-radius,6px)}
.rime-cands:empty{display:none}
.rime-cd{display:inline-flex;align-items:center;gap:4px;padding:var(--rime-cand-padding,3px 8px);border-radius:3px;cursor:pointer;font-size:calc(var(--rime-cand-font-size,14px) * var(--ime-panel-scale,1));transition:background .1s;user-select:none;color:var(--rime-cand-color,rgba(255,255,255,.82))}
.rime-cd:hover{background:var(--rime-cand-hover-bg,rgba(255,255,255,.08))}
.rime-cd.rime-hl{background:var(--rime-cand-highlight-bg,rgba(255,255,255,.08))}
.rime-ci{color:var(--rime-label-color,#70c0e8);font-size:calc(var(--rime-label-font-size,12px) * var(--ime-panel-scale,1));font-weight:500;min-width:14px}
.rime-cm{color:var(--rime-comment-color,rgba(255,255,255,.38));font-size:calc(var(--rime-comment-font-size,12px) * var(--ime-panel-scale,1));margin-left:2px}
.rime-nav{display:flex;align-items:center;gap:4px;margin-left:auto}
.rime-nb{background:var(--rime-nav-btn-bg,rgba(255,255,255,.06));border:none;color:var(--rime-nav-btn-color,#70c0e8);width:var(--rime-nav-btn-size,20px);height:var(--rime-nav-btn-size,20px);border-radius:3px;cursor:pointer;font-size:calc(10px * var(--ime-panel-scale,1));display:flex;align-items:center;justify-content:center}
.rime-nb:hover{background:var(--rime-cand-hover-bg,rgba(255,255,255,.12))}.rime-nb:disabled{opacity:.3;cursor:default}
.rime-np{color:var(--rime-nav-page-color,rgba(255,255,255,.38));font-size:calc(var(--rime-nav-page-font-size,11px) * var(--ime-panel-scale,1))}
`
    document.head.appendChild(s)
    RimePanel.instanceCount++
  }

  private editing = false
  private exclusiveShift = false
  private dragging = false
  private dragged = false
  private dragX = 0
  private dragY = 0
  private panelX = 0
  private panelY = 0
  private savedInputMode: string | null = null
  private savedAutoComplete: string | null = null
  private androidChromium = false
  private acStart = 0
  private acEnd = 0

  // 拖拽事件引用（destroy 时需移除）
  private _dragMoveHandler: ((e: MouseEvent) => void) | null = null
  private _dragTouchMoveHandler: ((e: TouchEvent) => void) | null = null
  private _dragEndHandler: (() => void) | null = null

  private static CONTROL_ALLOWLIST = ['`']

  private static RIME_KEY_MAP: Record<string, string> = {
    Escape: 'Escape', F4: 'F4',
    Backspace: 'BackSpace', Delete: 'Delete',
    Tab: 'Tab', Enter: 'Return', Return: 'Return',
    Home: 'Home', End: 'End',
    PageUp: 'Page_Up', PageDown: 'Page_Down',
    ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right',
    Alt: 'Alt_L', ' ': 'space',
    '~': 'asciitilde', '`': 'quoteleft',
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

  private insertText(text: string): void {
    if (this.target instanceof HTMLTextAreaElement || this.target instanceof HTMLInputElement) {
      const s = this.target.selectionStart ?? this.target.value.length
      const e = this.target.selectionEnd ?? s
      const v = this.target.value
      this.target.value = v.slice(0, s) + text + v.slice(e)
      this.target.selectionStart = this.target.selectionEnd = s + text.length
    }
    this.target.dispatchEvent(new Event('input', { bubbles: true }))
  }

  private isPrintable(key: string): boolean {
    return /^[a-z0-9!"#$%&'()*+,./:;<=>?@[\] ^_`{|}~\\-]$/i.test(key)
  }

  private analyze(r: RimeResult, rimeKey: string): void {
    if (r.state === 'committed') {
      this.editing = false
      this.dragged = false
      if (r.committed && !this._renderOnly) this.insertText(r.committed)
      this.hide()
    } else if (r.state === 'accepted') {
      if (r.committed && !this._renderOnly) this.insertText(r.committed)
      this.editing = true
      this.render(r)
      this.show()
    } else {
      this.editing = false
      this.dragged = false
      this.hide()
      if (r.state === 'rejected' && r.updatedSchema) {
        this.ime.setIME(r.updatedSchema.split('/')[0]).then(nr => {
          this.analyze(nr, '')
        }).catch(() => {})
      }
      if (r.state === 'unhandled' && !this._renderOnly && this.isPrintable(rimeKey)) {
        this.insertText(rimeKey)
      }
    }
    this.lastResult = r
    if (this.target instanceof HTMLTextAreaElement || this.target instanceof HTMLInputElement) {
      this.target.focus()
    }
  }

  private bindIME() {
    this.ime.onOptionChange(opts => {
      if ('ascii_mode' in opts) this.isEnglish = opts.ascii_mode
      if ('full_shape' in opts) this.isFullWidth = opts.full_shape
      if ('ascii_punct' in opts) this.isEnglishPunct = opts.ascii_punct
      if ('emoji_suggestion' in opts) this.isEmoji = opts.emoji_suggestion
    })
  }

  private bindTarget() {
    const el = this.target

    if (!this._externalKeyHandling) {
      el.addEventListener('compositionstart', (e: Event) => {
        e.preventDefault()
        try { (el as any).value = (el as any).value } catch {}
      })

      el.addEventListener('keydown', (e: KeyboardEvent) => {
        if (this.destroyed) return
        const { key, code } = e

        if (key === 'Unidentified') {
          if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
            this.androidChromium = true
            this.acStart = el.selectionStart ?? 0
            this.acEnd = el.selectionEnd ?? 0
          }
          return
        }

        if (key === 'Shift' && !e.ctrlKey && !e.altKey && !e.metaKey) {
          this.exclusiveShift = true
          return
        }
        this.exclusiveShift = false

        const isPrintableKey = this.isPrintable(key)
        const isAlt = key === 'Alt'
        const hasControl = e.ctrlKey || e.metaKey || e.altKey
        const hasShift = e.shiftKey
        const isShortcut = hasControl || (hasShift && !isPrintableKey)

        if (!this.editing) {
          if (document.activeElement !== el) return
          if (!isPrintableKey && key !== 'F4') return
          if (isShortcut && !hasShift && !(e.ctrlKey && RimePanel.CONTROL_ALLOWLIST.includes(key))) return
        }

        let rimeKey: string | undefined
        const wrap = (s: string) => `{${s}}`

        if (isShortcut || !isPrintableKey) {
          rimeKey = /^[0-9a-z]$/i.test(key) ? key : RimePanel.RIME_KEY_MAP[key]
          if (rimeKey === undefined) return
          if (isAlt && code === 'AltRight') rimeKey = 'Alt_R'
          const modifiers: string[] = []
          if (e.ctrlKey) modifiers.push('Control')
          if (e.metaKey) modifiers.push('Meta')
          if (e.altKey && !isAlt) modifiers.push('Alt')
          if (e.shiftKey) modifiers.push('Shift')
          modifiers.push(rimeKey)
          rimeKey = wrap(modifiers.join('+'))
        } else if (code.startsWith('Numpad')) {
          rimeKey = wrap(`KP_${code.substring(6)}`)
        } else {
          rimeKey = key
        }

        if (!rimeKey) return

        if (!this.dragged) {
          this.updatePosition()
        }

        e.preventDefault()
        this.ime.processKey(rimeKey).then(r => {
          this.analyze(r, rimeKey)
        }).catch(() => {})
      })

      el.addEventListener('keyup', (e: KeyboardEvent) => {
        if (this.destroyed) return
        const { key } = e
        if (key === 'Shift' && this.exclusiveShift) {
          this.isEnglish = !this.isEnglish
          this.ime.setOption('ascii_mode', this.isEnglish).catch(() => {})
        }
        this.exclusiveShift = false
        if (this.editing) {
          const releaseKey = RimePanel.RIME_KEY_MAP[key] || key
          this.ime.processKey(`{Release+${releaseKey}}`).catch(() => {})
        }
      })
    }

    el.addEventListener('blur', () => { this.hide() })
    el.addEventListener('focus', () => { if (this.editing) this.show() })
    el.addEventListener('scroll', () => { this.position() })
    window.addEventListener('resize', () => { this.position() })

    if (!this._externalKeyHandling && (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement)) {
      el.addEventListener('input', () => {
        if (!this.androidChromium) return
        this.androidChromium = false
        const ta = el as HTMLTextAreaElement
        const newText = ta.value
        const oldText = newText.slice(0, this.acStart) + newText.slice(this.acStart + 1)
        if (oldText.length + 1 === newText.length &&
            oldText.substring(0, this.acStart) === newText.substring(0, this.acStart) &&
            oldText.substring(this.acEnd) === newText.substring(this.acEnd + 1)) {
          const ch = newText[this.acStart]
          ta.value = oldText
          ta.selectionEnd = this.acStart
          this.editing = true
          this.ime.processKey(ch).then(r => {
            this.analyze(r, ch)
          }).catch(() => {})
        }
      })
    }
  }

  private updatePosition(): void {
    if (!(this.target instanceof HTMLTextAreaElement) && !(this.target instanceof HTMLInputElement)) return
    const el = this.target as HTMLTextAreaElement
    const box = el.getBoundingClientRect()
    const caret = this.getCaretCoords(el)
    this.panelX = box.x + caret.left
    this.panelY = box.y + caret.top + caret.height - el.scrollTop
  }

  private bindDrag(): void {
    this.floatEl.addEventListener('mousedown', (e: MouseEvent) => {
      this.dragX = e.clientX
      this.dragY = e.clientY
      if (this.dragged) {
        this.panelX = this.floatEl.getBoundingClientRect().left
        this.panelY = this.floatEl.getBoundingClientRect().top
      }
      this.dragging = true
      e.preventDefault()
    })

    this.floatEl.addEventListener('touchstart', (e: TouchEvent) => {
      if (e.touches.length !== 1) return
      const t = e.touches[0]
      this.dragX = t.clientX
      this.dragY = t.clientY
      if (this.dragged) {
        this.panelX = this.floatEl.getBoundingClientRect().left
        this.panelY = this.floatEl.getBoundingClientRect().top
      }
      this.dragging = true
    })

    this._dragMoveHandler = (e: MouseEvent) => {
      if (!this.dragging) return
      this.dragged = true
      this.panelX += e.clientX - this.dragX
      this.panelY += e.clientY - this.dragY
      this.dragX = e.clientX
      this.dragY = e.clientY
      this.floatEl.style.left = this.panelX + 'px'
      this.floatEl.style.top = this.panelY + 'px'
    }
    document.addEventListener('mousemove', this._dragMoveHandler)

    this._dragTouchMoveHandler = (e: TouchEvent) => {
      if (!this.dragging || e.touches.length !== 1) return
      const t = e.touches[0]
      this.dragged = true
      this.panelX += t.clientX - this.dragX
      this.panelY += t.clientY - this.dragY
      this.dragX = t.clientX
      this.dragY = t.clientY
      this.floatEl.style.left = this.panelX + 'px'
      this.floatEl.style.top = this.panelY + 'px'
    }
    document.addEventListener('touchmove', this._dragTouchMoveHandler)

    this._dragEndHandler = () => { this.dragging = false }
    document.addEventListener('mouseup', this._dragEndHandler)
    document.addEventListener('touchend', this._dragEndHandler)
  }

  private esc(s: string) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }

  render(r: RimeResult) {
    const comp = r.composition || {}
    const head = comp.head ?? (r as any).head ?? ''
    const body = comp.body ?? (r as any).body ?? ''
    const tail = comp.tail ?? (r as any).tail ?? ''
    if (head || body || tail) {
      this.compEl.innerHTML =
        `<span class="rime-ch">${this.esc(head || '')}</span>` +
        `<span class="rime-cb">${this.esc(body || '')}</span>` +
        `<span class="rime-ct">${this.esc(tail || '')}</span>`
    } else {
      this.compEl.innerHTML = ''
    }

    const cands = r.candidates || []
    if (cands.length) {
      const lbs = r.selectLabels || []
      const hl = r.highlighted ?? 0
      let h = ''
      cands.forEach((c, i) => {
        h += `<div class="rime-cd${i === hl ? ' rime-hl' : ''}" data-idx="${i}">` +
          `<span class="rime-ci">${this.esc(lbs[i] || String(i + 1))}</span>` +
          this.esc(c.text) +
          (this._showComment && c.comment ? `<span class="rime-cm">${this.esc(c.comment)}</span>` : '') +
          `</div>`
      })
      if (this._showNavigation) {
        const pg = r.page || 1, last = r.isLastPage
        h += `<div class="rime-nav">` +
          `<button class="rime-nb" data-pg="prev"${pg <= 1 ? ' disabled' : ''}>&#9664;</button>` +
          `<span class="rime-np">${pg}</span>` +
          `<button class="rime-nb" data-pg="next"${last ? ' disabled' : ''}>&#9654;</button></div>`
      }
      this.candsEl.innerHTML = h
      this.candsEl.style.flexDirection = this._vertical ? 'column' : 'row'
    } else {
      this.candsEl.innerHTML = ''
    }

    const has = (head || body || tail) || cands.length
    if (has) {
      this.floatEl.style.display = 'flex'
      if (!this.dragged) this.position()
      this.delegateClicks()
      if (!this._vertical) {
        const panelWidth = this.floatEl.getBoundingClientRect().width
        const targetWidth = (this.target instanceof HTMLElement) ? this.target.getBoundingClientRect().width : 0
        if (targetWidth > 0 && panelWidth > targetWidth) {
          this.candsEl.style.flexDirection = 'column'
        }
      }
    } else {
      this.floatEl.style.display = 'none'
    }
  }

  private delegateClicks() {
    this.candsEl.onclick = (e) => {
      const t = e.target as HTMLElement
      const cd = t.closest('.rime-cd') as HTMLElement
      if (cd && cd.dataset.idx != null) {
        this.ime.selectCandidate(parseInt(cd.dataset.idx, 10))
          .then(r => { this.analyze(r, '') }).catch(() => {})
        return
      }
      const nb = t.closest('.rime-nb') as HTMLElement
      if (nb && nb.dataset.pg) {
        this.ime.changePage(nb.dataset.pg === 'prev')
          .then(r => { this.analyze(r, '') }).catch(() => {})
      }
    }
  }

  private position() {
    if (!(this.target instanceof HTMLTextAreaElement) && !(this.target instanceof HTMLInputElement)) return
    const el = this.target as HTMLTextAreaElement
    const box = el.getBoundingClientRect()
    const caret = this.getCaretCoords(el)
    const fw = this.floatEl.offsetWidth, fh = this.floatEl.offsetHeight
    const vw = window.innerWidth, vh = window.innerHeight
    let x = box.x + caret.left + this._positionOffset.x
    let y = box.y + caret.top + caret.height - el.scrollTop + this._positionOffset.y
    if (x + fw > vw - 8) x = vw - fw - 8
    if (x < 8) x = 8
    if (y + fh > vh - 8) y = box.y + caret.top - el.scrollTop - fh
    this.floatEl.style.left = x + 'px'
    this.floatEl.style.top = y + 'px'
  }

  private getCaretCoords(el: HTMLTextAreaElement): { left: number; top: number; height: number } {
    const cs = getComputedStyle(el)
    const isInput = el.nodeName === 'INPUT'
    const div = document.createElement('div')
    const ps = ['direction','boxSizing','width','height','overflowX','overflowY',
      'borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth','borderStyle',
      'paddingTop','paddingRight','paddingBottom','paddingLeft','fontStyle','fontVariant',
      'fontWeight','fontStretch','fontSize','fontSizeAdjust','lineHeight','fontFamily',
      'textAlign','textTransform','textIndent','textDecoration','letterSpacing','wordSpacing',
      'tabSize','MozTabSize']
    div.style.position = 'absolute'; div.style.visibility = 'hidden'
    div.style.whiteSpace = 'pre-wrap'
    if (!isInput) div.style.wordWrap = 'break-word'
    div.style.overflow = 'hidden'
    ps.forEach(p => { (div.style as any)[p] = (cs as any)[p] })
    if (isInput) {
      if (cs.boxSizing === 'border-box') {
        const h = parseInt(cs.height)
        const outer = parseInt(cs.paddingTop) + parseInt(cs.paddingBottom) + parseInt(cs.borderTopWidth) + parseInt(cs.borderBottomWidth)
        const target = outer + parseInt(cs.lineHeight)
        if (h > target) (div.style as any).lineHeight = (h - outer) + 'px'
        else if (h === target) (div.style as any).lineHeight = cs.lineHeight
        else (div.style as any).lineHeight = '0'
      } else {
        (div.style as any).lineHeight = cs.height
      }
    }
    div.textContent = el.value.substring(0, el.selectionEnd)
    if (isInput) div.textContent = div.textContent.replace(/\s/g, '\u00a0')
    const span = document.createElement('span')
    span.textContent = el.value.substring(el.selectionEnd) || '.'
    div.appendChild(span)
    document.body.appendChild(div)
    const coordinates = {
      top: span.offsetTop + parseInt(cs.borderTopWidth),
      left: span.offsetLeft + parseInt(cs.borderLeftWidth),
      height: parseInt(cs.lineHeight)
    }
    document.body.removeChild(div)
    return coordinates
  }
}

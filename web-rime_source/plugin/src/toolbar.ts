import { RimeIME } from './ime'
import type { RimeIMEConfig } from './types'
import type { RimeTheme } from './panel'

interface IMEProvider {
  getIME(): RimeIME
  isInitialized(): boolean
}

/** 工具栏主题变量集合 */
export interface RimeToolbarThemeVars {
  bg?: string
  border?: string
  btnColor?: string
  btnHoverColor?: string
  btnHoverBg?: string
  btnActiveColor?: string
  btnActiveBg?: string
  btnLockedShadow?: string
  btnDisabledOpacity?: string
  dragColor?: string
  settingsBg?: string
  settingsBorder?: string
  settingsShadow?: string
  settingsColor?: string
  settingsLabelColor?: string
  toggleBg?: string
  toggleBorder?: string
  toggleColor?: string
  toggleHoverColor?: string
  toggleHoverBg?: string
  toggleOnColor?: string
  toggleOnBg?: string
  toggleOnBorder?: string
  sliderBg?: string
  sliderThumbBg?: string
}

/** dark/light 主题预设 */
const TOOLBAR_THEME_PRESETS: Record<RimeTheme, RimeToolbarThemeVars> = {
  dark: {
    bg: 'rgba(24,24,28,.96)',
    border: 'rgba(255,255,255,.08)',
    btnColor: 'rgba(255,255,255,.45)',
    btnHoverColor: 'rgba(255,255,255,.82)',
    btnHoverBg: 'rgba(255,255,255,.06)',
    btnActiveColor: '#70c0e8',
    btnActiveBg: 'rgba(112,192,232,.1)',
    btnLockedShadow: 'inset 0 0 0 2px #f0a020',
    btnDisabledOpacity: '.3',
    dragColor: 'rgba(255,255,255,.25)',
    settingsBg: 'rgba(24,24,28,.98)',
    settingsBorder: 'rgba(255,255,255,.1)',
    settingsShadow: '0 4px 16px rgba(0,0,0,.5)',
    settingsColor: 'rgba(255,255,255,.82)',
    settingsLabelColor: 'rgba(255,255,255,.7)',
    toggleBg: 'rgba(255,255,255,.06)',
    toggleBorder: 'rgba(255,255,255,.12)',
    toggleColor: 'rgba(255,255,255,.7)',
    toggleHoverColor: 'rgba(255,255,255,.9)',
    toggleHoverBg: 'rgba(255,255,255,.1)',
    toggleOnColor: '#70c0e8',
    toggleOnBg: 'rgba(112,192,232,.15)',
    toggleOnBorder: 'rgba(112,192,232,.4)',
    sliderBg: 'rgba(255,255,255,.15)',
    sliderThumbBg: '#70c0e8',
  },
  light: {
    bg: 'rgba(255,255,255,.98)',
    border: 'rgba(0,0,0,.1)',
    btnColor: 'rgba(0,0,0,.5)',
    btnHoverColor: 'rgba(0,0,0,.85)',
    btnHoverBg: 'rgba(0,0,0,.06)',
    btnActiveColor: '#2080f0',
    btnActiveBg: 'rgba(32,128,240,.12)',
    btnLockedShadow: 'inset 0 0 0 2px #f0a020',
    btnDisabledOpacity: '.3',
    dragColor: 'rgba(0,0,0,.25)',
    settingsBg: 'rgba(255,255,255,.99)',
    settingsBorder: 'rgba(0,0,0,.12)',
    settingsShadow: '0 4px 16px rgba(0,0,0,.15)',
    settingsColor: 'rgba(0,0,0,.85)',
    settingsLabelColor: 'rgba(0,0,0,.6)',
    toggleBg: 'rgba(0,0,0,.05)',
    toggleBorder: 'rgba(0,0,0,.12)',
    toggleColor: 'rgba(0,0,0,.6)',
    toggleHoverColor: 'rgba(0,0,0,.85)',
    toggleHoverBg: 'rgba(0,0,0,.08)',
    toggleOnColor: '#2080f0',
    toggleOnBg: 'rgba(32,128,240,.12)',
    toggleOnBorder: 'rgba(32,128,240,.4)',
    sliderBg: 'rgba(0,0,0,.15)',
    sliderThumbBg: '#2080f0',
  },
}

/** 合并主题预设与用户覆盖 */
function resolveToolbarThemeVars(theme: RimeTheme, overrides?: RimeToolbarThemeVars): RimeToolbarThemeVars {
  return { ...TOOLBAR_THEME_PRESETS[theme], ...overrides }
}

/** 将主题变量写入容器 CSS 自定义属性 */
function applyToolbarThemeVars(el: HTMLElement, vars: RimeToolbarThemeVars): void {
  const s = el.style
  const set = (prop: string, val: string | undefined) => {
    if (val !== undefined) s.setProperty(prop, val)
  }
  set('--rime-tb-bg', vars.bg)
  set('--rime-tb-border', vars.border)
  set('--rime-tb-btn-color', vars.btnColor)
  set('--rime-tb-btn-hover-color', vars.btnHoverColor)
  set('--rime-tb-btn-hover-bg', vars.btnHoverBg)
  set('--rime-tb-btn-active-color', vars.btnActiveColor)
  set('--rime-tb-btn-active-bg', vars.btnActiveBg)
  set('--rime-tb-btn-locked-shadow', vars.btnLockedShadow)
  set('--rime-tb-btn-disabled-opacity', vars.btnDisabledOpacity)
  set('--rime-tb-drag-color', vars.dragColor)
  set('--rime-tb-settings-bg', vars.settingsBg)
  set('--rime-tb-settings-border', vars.settingsBorder)
  set('--rime-tb-settings-shadow', vars.settingsShadow)
  set('--rime-tb-settings-color', vars.settingsColor)
  set('--rime-tb-settings-label-color', vars.settingsLabelColor)
  set('--rime-tb-toggle-bg', vars.toggleBg)
  set('--rime-tb-toggle-border', vars.toggleBorder)
  set('--rime-tb-toggle-color', vars.toggleColor)
  set('--rime-tb-toggle-hover-color', vars.toggleHoverColor)
  set('--rime-tb-toggle-hover-bg', vars.toggleHoverBg)
  set('--rime-tb-toggle-on-color', vars.toggleOnColor)
  set('--rime-tb-toggle-on-bg', vars.toggleOnBg)
  set('--rime-tb-toggle-on-border', vars.toggleOnBorder)
  set('--rime-tb-slider-bg', vars.sliderBg)
  set('--rime-tb-slider-thumb-bg', vars.sliderThumbBg)
}

export interface RimeToolbarConfig extends RimeIMEConfig {
  provider: IMEProvider
  theme?: RimeTheme
  position?: 'top' | 'bottom' | 'float'
  target?: HTMLElement
  /** 键盘容器元素（移动端传入），用于设置面板中的键盘透明度调节。
   * 桌面端不传，键盘透明度滑块自动隐藏。 */
  keyboardEl?: HTMLElement
}

export class RimeToolbar {
  private provider: IMEProvider
  private el: HTMLDivElement
  private dragHandle: HTMLDivElement
  private btnLang: HTMLButtonElement
  private btnVariant: HTMLButtonElement
  private btnWidth: HTMLButtonElement
  private btnPunct: HTMLButtonElement
  private btnSettings: HTMLButtonElement
  private settingsPanel: HTMLDivElement
  private emojiToggle: HTMLButtonElement
  private tbOpacitySlider: HTMLInputElement
  private kbOpacityRow: HTMLDivElement | null = null
  private kbOpacitySlider: HTMLInputElement | null = null
  private keyboardEl: HTMLElement | null
  private target: HTMLElement | null
  private destroyed = false
  private _currentTheme: RimeTheme

  private isEnglish = false
  private isFullWidth = false
  private isEnglishPunct = false
  private isEmoji = false
  private isSimplification = true
  private isPunctLocked = false

  private tbOpacity = 1.0
  private kbOpacity = 1.0

  private settingsOpen = false
  private _outsideClickHandler: ((e: MouseEvent | TouchEvent) => void) | null = null

  private punctClickTimer: number | null = null

  private dragging = false
  private dragX = 0
  private dragY = 0
  private posX = 0
  private posY = 0

  private _dragMoveHandler: ((e: MouseEvent) => void) | null = null
  private _dragTouchMoveHandler: ((e: TouchEvent) => void) | null = null
  private _dragEndHandler: (() => void) | null = null

  private static STYLE_ID = 'rime-toolbar-style'
  private static instanceCount = 0

  constructor(config: RimeToolbarConfig) {
    this.provider = config.provider
    this.target = config.target ?? null
    this.keyboardEl = config.keyboardEl ?? null
    this._currentTheme = config.theme ?? 'dark'

    this.el = document.createElement('div')
    this.el.className = 'rime-toolbar'

    // 应用主题 CSS 变量（构造时立即写入，CSS 规则通过 var() 消费）
    applyToolbarThemeVars(this.el, resolveToolbarThemeVars(this._currentTheme))

    this.dragHandle = document.createElement('div')
    this.dragHandle.className = 'rime-tb-drag'
    this.dragHandle.textContent = '\u2261'

    this.btnLang = this.createBtn('中', 'rime-tb-lang')
    this.btnVariant = this.createBtn('简', 'rime-tb-variant')
    this.btnWidth = this.createBtn('半月', 'rime-tb-width')
    this.btnPunct = this.createBtn('。', 'rime-tb-punct')
    this.btnSettings = this.createBtn('\u2699', 'rime-tb-settings')

    // 设置面板
    const panelResult = this.createSettingsPanel()
    this.settingsPanel = panelResult.panel
    this.emojiToggle = panelResult.emojiToggle
    this.tbOpacitySlider = panelResult.tbOpacitySlider
    this.kbOpacityRow = panelResult.kbOpacityRow
    this.kbOpacitySlider = panelResult.kbOpacitySlider

    this.el.appendChild(this.dragHandle)
    this.el.appendChild(this.btnLang)
    this.el.appendChild(this.btnVariant)
    this.el.appendChild(this.btnWidth)
    this.el.appendChild(this.btnPunct)
    this.el.appendChild(this.btnSettings)
    this.el.appendChild(this.settingsPanel)

    // 加载持久化的透明度
    this.loadOpacity()
    this.applyOpacity()

    this.injectStyle()
    this.bindEvents()
    this.bindIME()
    this.bindDrag()
    this.updateState()
    this.el.addEventListener('mousedown', (e) => {
      // 滑动条需要默认行为以支持鼠标拖动，不能 preventDefault
      const t = e.target as HTMLElement
      if (t.tagName === 'INPUT' && (t as HTMLInputElement).type === 'range') return
      e.preventDefault()
    })

    const target = config.target
    if (target && config.position !== 'float') {
      if (config.position === 'top') {
        target.parentElement?.insertBefore(this.el, target)
      } else {
        target.parentElement?.insertBefore(this.el, target.nextSibling)
      }
    } else {
      document.body.appendChild(this.el)
      this.el.style.position = 'fixed'
      const vw = window.innerWidth
      const vh = window.innerHeight
      const tbW = this.el.offsetWidth || 200
      const tbH = this.el.offsetHeight || 32
      const savedPos = this.loadPosition()
      if (savedPos) {
        this.posX = savedPos.rx * vw
        this.posY = savedPos.ry * vh
      } else {
        const statusBar = document.getElementById('status-bar')
        const sbH = statusBar ? statusBar.offsetHeight : 28
        this.posX = vw - tbW - 8
        this.posY = vh - sbH - tbH - 8
      }
      this.clampPosition()
      this.el.style.left = this.posX + 'px'
      this.el.style.top = this.posY + 'px'
    }
  }

  destroy(): void {
    this.destroyed = true
    this.el.remove()
    if (this._dragMoveHandler) document.removeEventListener('mousemove', this._dragMoveHandler)
    if (this._dragTouchMoveHandler) document.removeEventListener('touchmove', this._dragTouchMoveHandler)
    if (this._dragEndHandler) {
      document.removeEventListener('mouseup', this._dragEndHandler)
      document.removeEventListener('touchend', this._dragEndHandler)
    }
    if (this._outsideClickHandler) {
      document.removeEventListener('mousedown', this._outsideClickHandler, true)
      this._outsideClickHandler = null
    }
    RimeToolbar.instanceCount--
    if (RimeToolbar.instanceCount <= 0) {
      const st = document.getElementById(RimeToolbar.STYLE_ID)
      if (st) st.remove()
      RimeToolbar.instanceCount = 0
    }
  }

  getElement(): HTMLDivElement { return this.el }

  /** 切换主题（由外部 onThemeChange 调用） */
  setTheme(theme: RimeTheme): void {
    this._currentTheme = theme
    applyToolbarThemeVars(this.el, resolveToolbarThemeVars(theme))
  }

  updateState(): void {
    const ime = this.provider.getIME()
    if (!ime || !this.provider.isInitialized()) return
    const state = ime.getState()
    this.isEnglish = state.isEnglish
    this.isFullWidth = state.isFullWidth
    this.isEnglishPunct = state.isEnglishPunct
    this.isEmoji = state.isEmoji
    this.isSimplification = state.isSimplification
    this.isPunctLocked = !!(ime as any).punctLocked
    this.refreshUI()
  }

  private createBtn(text: string, cls: string): HTMLButtonElement {
    const btn = document.createElement('button')
    btn.className = 'rime-tb-btn' + (cls ? ' ' + cls : '')
    btn.textContent = text
    return btn
  }

  private refreshUI(): void {
    this.btnLang.textContent = this.isEnglish ? 'En' : '中'
    this.btnLang.classList.toggle('rime-tb-active', !this.isEnglish)

    this.btnVariant.textContent = this.isSimplification ? '简' : '繁'
    this.btnVariant.classList.toggle('rime-tb-active', this.isSimplification)
    this.btnVariant.disabled = this.isEnglish

    this.btnWidth.textContent = this.isFullWidth ? '全角' : '半月'
    this.btnWidth.classList.toggle('rime-tb-active', this.isFullWidth)

    this.btnPunct.textContent = this.isEnglishPunct ? '.' : '。'
    this.btnPunct.classList.toggle('rime-tb-active', !this.isEnglishPunct)
    this.btnPunct.classList.toggle('rime-tb-locked', this.isPunctLocked)

    // Emoji 开关移至设置面板
    if (this.emojiToggle) {
      this.emojiToggle.classList.toggle('rime-tb-toggle-on', this.isEmoji)
      this.emojiToggle.textContent = this.isEmoji ? 'Emoji: 开' : 'Emoji: 关'
    }
  }

  private bindEvents(): void {
    this.btnLang.addEventListener('click', () => {
      this.isEnglish = !this.isEnglish
      this.provider.getIME().setOption('ascii_mode', this.isEnglish)
      // 切换语言时自动设置标点：中→中文标点(。)，En→英文标点(.)
      // 标点锁定时不自动改变 ascii_punct
      if (!this.isPunctLocked) {
        this.provider.getIME().setOption('ascii_punct', this.isEnglish)
      }
      this.refreshUI()
    })

    this.btnVariant.addEventListener('click', () => {
      if (this.isEnglish) return
      this.isSimplification = !this.isSimplification
      this.provider.getIME().setOption('simplification', this.isSimplification)
      this.refreshUI()
    })

    this.btnWidth.addEventListener('click', () => {
      this.isFullWidth = !this.isFullWidth
      this.provider.getIME().setOption('full_shape', this.isFullWidth)
      this.refreshUI()
    })

    this.btnPunct.addEventListener('click', () => {
      // 双击检测：250ms 内第二次点击 → 切换标点锁定
      if (this.punctClickTimer !== null) {
        clearTimeout(this.punctClickTimer)
        this.punctClickTimer = null
        this.isPunctLocked = !this.isPunctLocked
        this.provider.getIME().punctLocked = this.isPunctLocked
        this.refreshUI()
        return
      }
      // 单击：延迟执行，等待可能的双击
      this.punctClickTimer = window.setTimeout(() => {
        this.punctClickTimer = null
        this.isEnglishPunct = !this.isEnglishPunct
        this.provider.getIME().setOption('ascii_punct', this.isEnglishPunct)
        this.refreshUI()
      }, 250)
    })

    this.btnSettings.addEventListener('click', (e) => {
      e.stopPropagation()
      this.toggleSettings()
    })

    this.emojiToggle.addEventListener('click', () => {
      this.isEmoji = !this.isEmoji
      this.provider.getIME().setOption('emoji_suggestion', this.isEmoji)
      this.refreshUI()
    })

    this.tbOpacitySlider.addEventListener('input', () => {
      this.tbOpacity = parseFloat(this.tbOpacitySlider.value)
      this.el.style.opacity = String(this.tbOpacity)
      this.saveOpacity()
    })

    if (this.kbOpacitySlider && this.kbOpacityRow) {
      const kbSlider = this.kbOpacitySlider
      kbSlider.addEventListener('input', () => {
        this.kbOpacity = parseFloat(kbSlider.value)
        this.saveOpacity()
      })
    }

    // 点击外部关闭设置面板
    this._outsideClickHandler = (e) => {
      if (!this.settingsOpen) return
      const target = e.target as Node
      if (this.settingsPanel.contains(target) || this.btnSettings.contains(target)) return
      this.closeSettings()
    }
    document.addEventListener('mousedown', this._outsideClickHandler, true)
  }

  /** 创建设置面板：emoji 开关 + 工具栏透明度 + 键盘透明度（移动端） */
  private createSettingsPanel(): {
    panel: HTMLDivElement
    emojiToggle: HTMLButtonElement
    tbOpacitySlider: HTMLInputElement
    kbOpacityRow: HTMLDivElement | null
    kbOpacitySlider: HTMLInputElement | null
  } {
    const panel = document.createElement('div')
    panel.className = 'rime-tb-settings-panel'
    panel.style.display = 'none'

    // Emoji 开关行
    const emojiRow = document.createElement('div')
    emojiRow.className = 'rime-tb-set-row'
    const emojiLabel = document.createElement('span')
    emojiLabel.className = 'rime-tb-set-label'
    emojiLabel.textContent = 'Emoji'
    const emojiToggle = document.createElement('button')
    emojiToggle.className = 'rime-tb-toggle'
    emojiToggle.type = 'button'
    emojiToggle.textContent = 'Emoji: 关'
    emojiRow.appendChild(emojiLabel)
    emojiRow.appendChild(emojiToggle)
    panel.appendChild(emojiRow)

    // 工具栏透明度滑块行
    const tbRow = document.createElement('div')
    tbRow.className = 'rime-tb-set-row'
    const tbLabel = document.createElement('span')
    tbLabel.className = 'rime-tb-set-label'
    tbLabel.textContent = '工具栏'
    const tbSlider = document.createElement('input')
    tbSlider.className = 'rime-tb-slider'
    tbSlider.type = 'range'
    tbSlider.min = '0.3'
    tbSlider.max = '1'
    tbSlider.step = '0.05'
    tbSlider.value = '1'
    tbRow.appendChild(tbLabel)
    tbRow.appendChild(tbSlider)
    panel.appendChild(tbRow)

    // 键盘透明度滑块行（仅当传入 keyboardEl 时显示）
    let kbRow: HTMLDivElement | null = null
    let kbSlider: HTMLInputElement | null = null
    if (this.keyboardEl) {
      kbRow = document.createElement('div')
      kbRow.className = 'rime-tb-set-row'
      const kbLabel = document.createElement('span')
      kbLabel.className = 'rime-tb-set-label'
      kbLabel.textContent = '键盘'
      kbSlider = document.createElement('input')
      kbSlider.className = 'rime-tb-slider'
      kbSlider.type = 'range'
      kbSlider.min = '0.3'
      kbSlider.max = '1'
      kbSlider.step = '0.05'
      kbSlider.value = '1'
      kbRow.appendChild(kbLabel)
      kbRow.appendChild(kbSlider)
      panel.appendChild(kbRow)
    }

    return { panel, emojiToggle, tbOpacitySlider: tbSlider, kbOpacityRow: kbRow, kbOpacitySlider: kbSlider }
  }

  private loadOpacity(): void {
    try {
      const tb = localStorage.getItem('pty_rime_tb_opacity')
      const kb = localStorage.getItem('pty_rime_kb_opacity')
      if (tb) this.tbOpacity = Math.max(0.3, Math.min(1, parseFloat(tb) || 1))
      if (kb) this.kbOpacity = Math.max(0.3, Math.min(1, parseFloat(kb) || 1))
    } catch (_) { /* ignore */ }
  }

  private applyOpacity(): void {
    this.el.style.opacity = String(this.tbOpacity)
    this.tbOpacitySlider.value = String(this.tbOpacity)
    if (this.kbOpacitySlider) this.kbOpacitySlider.value = String(this.kbOpacity)
  }

  private saveOpacity(): void {
    try {
      localStorage.setItem('pty_rime_tb_opacity', String(this.tbOpacity))
      localStorage.setItem('pty_rime_kb_opacity', String(this.kbOpacity))
    } catch (_) { /* ignore */ }
  }

  private loadPosition(): { rx: number; ry: number } | null {
    try {
      const raw = localStorage.getItem('pty_rime_tb_pos')
      if (!raw) return null
      const pos = JSON.parse(raw)
      if (typeof pos.rx === 'number' && typeof pos.ry === 'number') return pos
      return null
    } catch (_) { return null }
  }

  private savePosition(): void {
    try {
      const vw = window.innerWidth
      const vh = window.innerHeight
      if (vw <= 0 || vh <= 0) return
      const rect = this.el.getBoundingClientRect()
      const rx = rect.left / vw
      const ry = rect.top / vh
      localStorage.setItem('pty_rime_tb_pos', JSON.stringify({ rx, ry }))
    } catch (_) { /* ignore */ }
  }

  private toggleSettings(): void {
    this.settingsOpen ? this.closeSettings() : this.openSettings()
  }

  private openSettings(): void {
    if (this.destroyed) return
    this.settingsOpen = true
    this.settingsPanel.style.display = ''
    this.btnSettings.classList.add('rime-tb-active')
  }

  private closeSettings(): void {
    this.settingsOpen = false
    this.settingsPanel.style.display = 'none'
    this.btnSettings.classList.remove('rime-tb-active')
  }

  private bindIME(): void {
    const ime = this.provider.getIME()
    ime.onOptionChange(opts => {
      if ('ascii_mode' in opts) this.isEnglish = opts.ascii_mode
      if ('full_shape' in opts) this.isFullWidth = opts.full_shape
      if ('ascii_punct' in opts) this.isEnglishPunct = opts.ascii_punct
      if ('emoji_suggestion' in opts) this.isEmoji = opts.emoji_suggestion
      if ('simplification' in opts) this.isSimplification = opts.simplification
      this.refreshUI()
    })
    ime.onSchemaChange(() => {
      this.updateState()
    })
  }

  private bindDrag(): void {
    this.dragHandle.addEventListener('mousedown', (e: MouseEvent) => {
      e.preventDefault()
      this.startDrag(e.clientX, e.clientY)
    })

    this.dragHandle.addEventListener('touchstart', (e: TouchEvent) => {
      if (e.touches.length !== 1) return
      e.preventDefault()
      const t = e.touches[0]
      this.startDrag(t.clientX, t.clientY)
    })

    this._dragMoveHandler = (e: MouseEvent) => {
      if (!this.dragging) return
      this.moveDrag(e.clientX, e.clientY)
    }
    document.addEventListener('mousemove', this._dragMoveHandler)

    this._dragTouchMoveHandler = (e: TouchEvent) => {
      if (!this.dragging || e.touches.length !== 1) return
      const t = e.touches[0]
      this.moveDrag(t.clientX, t.clientY)
    }
    document.addEventListener('touchmove', this._dragTouchMoveHandler)

    this._dragEndHandler = () => {
      this.dragging = false
      this.savePosition()
    }
    document.addEventListener('mouseup', this._dragEndHandler)
    document.addEventListener('touchend', this._dragEndHandler)
  }

  private startDrag(cx: number, cy: number): void {
    this.dragging = true
    this.dragX = cx
    this.dragY = cy
    const rect = this.el.getBoundingClientRect()
    this.posX = rect.left
    this.posY = rect.top
    this.el.style.position = 'fixed'
  }

  private moveDrag(cx: number, cy: number): void {
    this.posX += cx - this.dragX
    this.posY += cy - this.dragY
    this.dragX = cx
    this.dragY = cy
    this.clampPosition()
    this.el.style.left = this.posX + 'px'
    this.el.style.top = this.posY + 'px'
  }

  private clampPosition(): void {
    const vw = window.innerWidth
    const vh = window.innerHeight
    const rect = this.el.getBoundingClientRect()
    const w = rect.width || 200
    const h = rect.height || 40
    if (this.posX < 0) this.posX = 0
    if (this.posY < 0) this.posY = 0
    if (this.posX + w > vw) this.posX = vw - w
    if (this.posY + h > vh) this.posY = vh - h
  }

  private injectStyle(): void {
    if (document.getElementById(RimeToolbar.STYLE_ID)) {
      RimeToolbar.instanceCount++
      return
    }
    const s = document.createElement('style')
    s.id = RimeToolbar.STYLE_ID
    s.textContent = `
.rime-toolbar{display:inline-flex;align-items:center;gap:3px;padding:4px 8px;background:var(--rime-tb-bg,rgba(24,24,28,.96));border:1px solid var(--rime-tb-border,rgba(255,255,255,.08));border-radius:6px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;user-select:none;filter:drop-shadow(0 2px 8px rgba(0,0,0,.45));z-index:100002;touch-action:none}
.rime-tb-drag{cursor:grab;padding:2px 4px;color:var(--rime-tb-drag-color,rgba(255,255,255,.25));font-size:16px;line-height:1;letter-spacing:1px;font-weight:700}
.rime-tb-drag:active{cursor:grabbing}
.rime-tb-btn{background:transparent;border:none;color:var(--rime-tb-btn-color,rgba(255,255,255,.45));padding:3px 10px;border-radius:3px;cursor:pointer;font-size:13px;line-height:1.6;transition:all .1s;white-space:nowrap;min-width:34px;text-align:center;-webkit-tap-highlight-color:transparent}
.rime-tb-btn:hover{color:var(--rime-tb-btn-hover-color,rgba(255,255,255,.82));background:var(--rime-tb-btn-hover-bg,rgba(255,255,255,.06))}
.rime-tb-btn.rime-tb-active{color:var(--rime-tb-btn-active-color,#70c0e8);background:var(--rime-tb-btn-active-bg,rgba(112,192,232,.1))}
.rime-tb-btn.rime-tb-locked{box-shadow:var(--rime-tb-btn-locked-shadow,inset 0 0 0 2px #f0a020)}
.rime-tb-btn:disabled{opacity:var(--rime-tb-btn-disabled-opacity,.3);cursor:default;background:transparent;pointer-events:none}
.rime-tb-settings-panel{position:absolute;top:calc(100% + 4px);right:0;min-width:180px;padding:8px 10px;background:var(--rime-tb-settings-bg,rgba(24,24,28,.98));border:1px solid var(--rime-tb-settings-border,rgba(255,255,255,.1));border-radius:6px;box-shadow:var(--rime-tb-settings-shadow,0 4px 16px rgba(0,0,0,.5));z-index:100003;display:flex;flex-direction:column;gap:8px;font-size:12px;color:var(--rime-tb-settings-color,rgba(255,255,255,.82))}
.rime-tb-set-row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.rime-tb-set-label{color:var(--rime-tb-settings-label-color,rgba(255,255,255,.7));font-size:12px;white-space:nowrap}
.rime-tb-toggle{background:var(--rime-tb-toggle-bg,rgba(255,255,255,.06));border:1px solid var(--rime-tb-toggle-border,rgba(255,255,255,.12));color:var(--rime-tb-toggle-color,rgba(255,255,255,.7));padding:3px 8px;border-radius:3px;cursor:pointer;font-size:11px;line-height:1.4;-webkit-tap-highlight-color:transparent;transition:all .1s}
.rime-tb-toggle:hover{color:var(--rime-tb-toggle-hover-color,rgba(255,255,255,.9));background:var(--rime-tb-toggle-hover-bg,rgba(255,255,255,.1))}
.rime-tb-toggle-on{color:var(--rime-tb-toggle-on-color,#70c0e8);background:var(--rime-tb-toggle-on-bg,rgba(112,192,232,.15));border-color:var(--rime-tb-toggle-on-border,rgba(112,192,232,.4))}
.rime-tb-slider{flex:1;min-width:80px;cursor:pointer;-webkit-appearance:none;appearance:none;height:4px;background:var(--rime-tb-slider-bg,rgba(255,255,255,.15));border-radius:2px;outline:none}
.rime-tb-slider::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:14px;height:14px;border-radius:50%;background:var(--rime-tb-slider-thumb-bg,#70c0e8);cursor:pointer;border:none}
.rime-tb-slider::-moz-range-thumb{width:14px;height:14px;border-radius:50%;background:var(--rime-tb-slider-thumb-bg,#70c0e8);cursor:pointer;border:none}
`
    document.head.appendChild(s)
    RimeToolbar.instanceCount++
  }
}

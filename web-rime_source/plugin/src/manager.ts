import { RimeIME } from './ime'
import { RimePanel } from './panel'
import type { RimePanelConfig, RimeTheme as RimePanelTheme, RimePanelSize } from './panel'
import { RimeKeyboard } from './keyboard/index'
import type { RimeKeyboardConfig, RimeKeyboardTheme, RimeKeyboardSize, KeyboardMode } from './keyboard/types'
import { RimeTKLKeyboard } from './keyboard/tkl'
import type { TKLTheme } from './keyboard/tkl/theme'
import { RimeToolbar } from './toolbar'
import type { RimeToolbarConfig } from './toolbar'
import type { RimeIMEConfig, RimeResult, RimeMode, CommitCallback, OptionChangeCallback, SchemaChangeCallback, ErrorCallback, DeployStatusCallback, ResultChangeCallback } from './types'

export type RimeManagerMode = 'panel' | 'panel+keyboard' | 'keyboard' | 'tkl+panel'

export interface RimeManagerConfig extends RimeIMEConfig {
  target: HTMLElement
  /** 调用模式：
   *  - 'panel'           : 纯 RimePanel（物理键盘输入，候选词面板显示）
   *  - 'panel+keyboard'  : RimePanel + 虚拟键盘（键盘不含候选词，Panel 显示候选词）
   *  - 'keyboard'        : 纯虚拟键盘（自带候选词栏）
   *  - 'tkl+panel'       : TKL 悬浮键盘（无候选词）+ RimePanel 显示候选词 */
  managerMode: RimeManagerMode
  // ─── Panel 选项（managerMode 为 panel / panel+keyboard 时生效）───
  panelTheme?: RimePanelTheme
  panelThemeVars?: RimePanelConfig['themeVars']
  panelSize?: RimePanelSize
  pageSize?: number
  showComment?: boolean
  showNavigation?: boolean
  vertical?: boolean
  positionOffset?: { x?: number; y?: number }
  panelClassName?: string
  panelStyle?: Partial<CSSStyleDeclaration>
  /** Panel 外部按键处理：true 时跳过绑定 keydown/keyup 监听器，
   * 由外部通过 handleKey() 调用。桌面端终端集成时设为 true。 */
  externalKeyHandling?: boolean
  // ─── Keyboard 选项（managerMode 为 panel+keyboard / keyboard 时生效）───
  kbTheme?: RimeKeyboardTheme
  kbSize?: RimeKeyboardSize
  kbMode?: KeyboardMode
  showOnFocus?: boolean
  haptic?: boolean
  floatingWidth?: number
  floatingHeight?: number
  eol?: string
  // ─── Toolbar 选项 ───
  showToolbar?: boolean
  toolbarPosition?: RimeToolbarConfig['position']
  // ─── TKL 选项（managerMode 为 tkl+panel 时生效）───
  tklTheme?: TKLTheme
  tklFloatingWidth?: number
  tklFloatingHeight?: number
}

export class RimeManager {
  private ime: RimeIME
  private _mode: RimeManagerMode
  private _panel: RimePanel | null = null
  private _keyboard: RimeKeyboard | null = null
  private _tklKeyboard: RimeTKLKeyboard | null = null
  private _toolbar: RimeToolbar | null = null
  private destroyed = false

  constructor(config: RimeManagerConfig) {
    this._mode = config.managerMode
    this.ime = new RimeIME(config)

    switch (this._mode) {
      case 'panel':
        this._panel = new RimePanel({
          ...config,
          ime: this.ime,
          externalKeyHandling: config.externalKeyHandling,
          theme: config.panelTheme,
          themeVars: config.panelThemeVars,
          size: config.panelSize,
          showComment: config.showComment,
          showNavigation: config.showNavigation,
          vertical: config.vertical,
          positionOffset: config.positionOffset,
          className: config.panelClassName,
          style: config.panelStyle,
        })
        break

      case 'panel+keyboard':
        this._panel = new RimePanel({
          ...config,
          ime: this.ime,
          renderOnly: true,
          externalKeyHandling: true,
          theme: config.panelTheme,
          themeVars: config.panelThemeVars,
          size: config.panelSize,
          showComment: config.showComment,
          showNavigation: config.showNavigation,
          vertical: config.vertical,
          positionOffset: config.positionOffset,
          className: config.panelClassName,
          style: config.panelStyle,
        })
        this._keyboard = new RimeKeyboard({
          ...config,
          ime: this.ime,
          hideCandidateBar: true,
          theme: config.kbTheme,
          size: config.kbSize,
          kbMode: config.kbMode,
          showOnFocus: config.showOnFocus,
          haptic: config.haptic,
          floatingWidth: config.floatingWidth,
          floatingHeight: config.floatingHeight,
          eol: config.eol,
        })
        this.wirePanelKeyboard()
        break

      case 'keyboard':
        this._keyboard = new RimeKeyboard({
          ...config,
          ime: this.ime,
          theme: config.kbTheme,
          size: config.kbSize,
          kbMode: config.kbMode,
          showOnFocus: config.showOnFocus,
          haptic: config.haptic,
          floatingWidth: config.floatingWidth,
          floatingHeight: config.floatingHeight,
          eol: config.eol,
        })
        break

      case 'tkl+panel':
        this._panel = new RimePanel({
          ...config,
          ime: this.ime,
          renderOnly: true,
          externalKeyHandling: true,
          theme: config.panelTheme,
          themeVars: config.panelThemeVars,
          size: config.panelSize,
          showComment: config.showComment,
          showNavigation: config.showNavigation,
          vertical: config.vertical,
          positionOffset: config.positionOffset,
          className: config.panelClassName,
          style: config.panelStyle,
        })
        this._tklKeyboard = new RimeTKLKeyboard({
          ...config,
          ime: this.ime,
          theme: config.tklTheme,
          floatingWidth: config.tklFloatingWidth,
          floatingHeight: config.tklFloatingHeight,
          eol: config.eol,
        })
        this.wirePanelTKL()
        break
    }

    if (config.showToolbar !== false) {
      const provider = this._panel ?? this._keyboard ?? this._tklKeyboard!
      this._toolbar = new RimeToolbar({
        ...config,
        provider,
        theme: (config.panelTheme ?? config.kbTheme) as any,
        position: config.toolbarPosition,
        target: config.target,
        keyboardEl: (this._keyboard ?? this._tklKeyboard)?.getElement(),
      })
    }
  }

  async init(): Promise<void> {
    await this.ime.init()
  }

  destroy(): void {
    if (this.destroyed) return
    this.destroyed = true
    this._toolbar?.destroy()
    this._panel?.destroy()
    this._keyboard?.destroy()
    this._tklKeyboard?.destroy()
    this.ime.destroy()
  }

  getIME(): RimeIME { return this.ime }
  getPanel(): RimePanel | null { return this._panel }
  getKeyboard(): RimeKeyboard | null { return this._keyboard }
  getTKLKeyboard(): RimeTKLKeyboard | null { return this._tklKeyboard }
  getToolbar(): RimeToolbar | null { return this._toolbar }
  getMode(): RimeManagerMode { return this._mode }

  isInitialized(): boolean { return this.ime.isInitialized() }

  // ─── 事件代理 ───

  onCommit(cb: CommitCallback): void { this.ime.onCommit(cb) }
  onOptionChange(cb: OptionChangeCallback): void { this.ime.onOptionChange(cb) }
  onSchemaChange(cb: SchemaChangeCallback): void { this.ime.onSchemaChange(cb) }
  onError(cb: ErrorCallback): void { this.ime.onError(cb) }
  onDeployStatus(cb: DeployStatusCallback): void { this.ime.onDeployStatus(cb) }
  onResultChange(cb: ResultChangeCallback): void { this.ime.onResultChange(cb) }

  offCommit(cb: CommitCallback): void { this.ime.offCommit(cb) }
  offOptionChange(cb: OptionChangeCallback): void { this.ime.offOptionChange(cb) }
  offSchemaChange(cb: SchemaChangeCallback): void { this.ime.offSchemaChange(cb) }
  offError(cb: ErrorCallback): void { this.ime.offError(cb) }
  offDeployStatus(cb: DeployStatusCallback): void { this.ime.offDeployStatus(cb) }
  offResultChange(cb: ResultChangeCallback): void { this.ime.offResultChange(cb) }

  // ─── 模式2 专用：Panel + Keyboard 联动 ───

  private wirePanelKeyboard(): void {
    if (!this._panel || !this._keyboard) return

    this.ime.onResultChange((r: RimeResult) => {
      this._panel!.renderResult(r)
    })

    this.ime.onCommit((text: string) => {
      this._keyboard!.insertText(text)
    })
  }

  // ─── 模式4 专用：Panel + TKL Keyboard 联动 ───

  private wirePanelTKL(): void {
    if (!this._panel || !this._tklKeyboard) return

    this.ime.onResultChange((r: RimeResult) => {
      this._panel!.renderResult(r)
    })

    this.ime.onCommit((text: string) => {
      this._tklKeyboard!.insertText(text)
    })
  }
}

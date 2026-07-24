import type { RimeIMEConfig, RimeResult, Composition, Candidate, CommitCallback, OptionChangeCallback, SchemaChangeCallback, ErrorCallback, DeployStatusCallback, ResultChangeCallback } from './types'
import { RemoteBackend } from './backends/remote'
import { WasmBackend } from './backends/wasm'
import type { IBackend } from './backends/base'

export interface RimeState {
  isEnglish: boolean
  isFullWidth: boolean
  isEnglishPunct: boolean
  isEmoji: boolean
  isSimplification: boolean
  currentSchema: string
}

const STORAGE_KEY = 'rime-ime-state'

export class RimeIME {
  private backend: IBackend
  private commitCallbacks: CommitCallback[] = []
  private optionCallbacks: OptionChangeCallback[] = []
  private schemaCallbacks: SchemaChangeCallback[] = []
  private errorCallbacks: ErrorCallback[] = []
  private deployStatusCallbacks: DeployStatusCallback[] = []
  private resultCallbacks: ResultChangeCallback[] = []
  private initialized = false
  private config: Required<RimeIMEConfig>
  private _lastResult: RimeResult | null = null
  private _state: RimeState = {
    isEnglish: false,
    isFullWidth: false,
    isEnglishPunct: false,
    isEmoji: false,
    isSimplification: true,
    currentSchema: ''
  }
  private _persist: boolean

  /** 标点锁定（UI 层概念，非 RIME 引擎选项）。
   * true 时，中/En 语言切换不会自动改变 ascii_punct。
   * 由 RimeToolbar 双击 。/. 按钮设置，RimeToolbar 和 RimeKeyboard 共同尊重。 */
  public punctLocked = false

  constructor(config: RimeIMEConfig & { persist?: boolean }) {
    this._persist = config.persist ?? true
    this.config = {
      mode: config.mode,
      persist: this._persist,
      serverUrl: config.serverUrl ?? '',
      wasmUrl: config.wasmUrl ?? '',
      schema: config.schema ?? 'luna_pinyin',
      pageSize: config.pageSize ?? 5
    }

    this.loadState()

    if (config.mode === 'remote') {
      if (!config.serverUrl) throw new Error('serverUrl is required for remote mode')
      const remote = new RemoteBackend(config.serverUrl)
      remote.setHandler((msg) => {
        if (msg.type === 'result') {
          const r = msg as any
          if (r.updatedOptions && Object.keys(r.updatedOptions).length > 0) {
            this.syncState(r.updatedOptions)
            this.optionCallbacks.forEach(cb => cb(r.updatedOptions))
          }
          if (r.updatedSchema) {
            this._state.currentSchema = r.updatedSchema
            this.schemaCallbacks.forEach(cb => cb(r.updatedSchema))
          }
        } else if (msg.type === 'deployStatus') {
          const d = msg as any
          this.deployStatusCallbacks.forEach(cb => cb(d.status))
        }
      })
      this.backend = remote
    } else if (config.mode === 'wasm') {
      if (!config.wasmUrl) throw new Error('wasmUrl is required for wasm mode')
      this.backend = new WasmBackend(config.wasmUrl)
    } else {
      throw new Error(`Unknown mode: ${config.mode}`)
    }
  }

  async init(): Promise<void> {
    if (this.initialized) return
    try {
      const schema = this._state.currentSchema || this.config.schema
      await this.backend.init(schema, this.config.pageSize)
      this._state.currentSchema = schema
      if (this._state.isEnglish) await this.backend.setOption('ascii_mode', true)
      // 始终显式下发 simplification，避免引擎使用 schema 默认值（本工程默认繁体）与界面状态不一致
      await this.backend.setOption('simplification', this._state.isSimplification)
      if (this._state.isEnglishPunct) await this.backend.setOption('ascii_punct', true)
      if (this._state.isFullWidth) await this.backend.setOption('full_shape', true)
      this.initialized = true
    } catch (err) {
      this.errorCallbacks.forEach(cb => cb(err as Error))
      throw err
    }
  }

  async processKey(key: string): Promise<RimeResult> {
    this.ensureInit()
    try {
      const result = await this.backend.process(key)
      this._lastResult = result
      this.handleResult(result)
      this.resultCallbacks.forEach(cb => cb(result))
      return result
    } catch (err) {
      this.errorCallbacks.forEach(cb => cb(err as Error))
      throw err
    }
  }

  async selectCandidate(index: number): Promise<RimeResult> {
    this.ensureInit()
    try {
      const result = await this.backend.selectCandidate(index)
      this._lastResult = result
      this.handleResult(result)
      this.resultCallbacks.forEach(cb => cb(result))
      return result
    } catch (err) {
      this.errorCallbacks.forEach(cb => cb(err as Error))
      throw err
    }
  }

  async changePage(backward: boolean): Promise<RimeResult> {
    this.ensureInit()
    try {
      const result = await this.backend.changePage(backward)
      this._lastResult = result
      this.handleResult(result)
      this.resultCallbacks.forEach(cb => cb(result))
      return result
    } catch (err) {
      this.errorCallbacks.forEach(cb => cb(err as Error))
      throw err
    }
  }

  async setOption(option: string, value: boolean): Promise<void> {
    this.ensureInit()
    await this.backend.setOption(option, value)
    const opts = { [option]: value } as Record<string, boolean>
    this.syncState(opts)
    this.optionCallbacks.forEach(cb => cb(opts))
  }

  async setIME(schema: string): Promise<RimeResult> {
    this.ensureInit()
    try {
      const result = await this.backend.setIME(schema)
      this._lastResult = result
      this._state.currentSchema = schema
      this._state.isEnglish = false
      // 切换方案后引擎会重置选项为 schema 默认值，需重新下发简繁设置
      await this.backend.setOption('simplification', this._state.isSimplification)
      this.handleResult(result)
      this.resultCallbacks.forEach(cb => cb(result))
      return result
    } catch (err) {
      this.errorCallbacks.forEach(cb => cb(err as Error))
      throw err
    }
  }

  async setPageSize(size: number): Promise<void> {
    this.ensureInit()
    this.config.pageSize = size
    await this.backend.setPageSize(size)
  }

  async deploy(): Promise<void> {
    this.ensureInit()
    await this.backend.deploy()
  }

  getState(): RimeState { return { ...this._state } }

  getCandidates(): Candidate[] { return this._lastResult?.candidates ?? [] }

  getComposition(): Composition {
    const r = this._lastResult
    if (!r) return { head: '', body: '', tail: '' }
    const comp = r.composition || {}
    return {
      head: comp.head ?? (r as any).head ?? '',
      body: comp.body ?? (r as any).body ?? '',
      tail: comp.tail ?? (r as any).tail ?? ''
    }
  }

  getLastResult(): RimeResult | null { return this._lastResult }

  getCurrentSchema(): string { return this._state.currentSchema }

  getPageSize(): number { return this.config.pageSize }

  isInitialized(): boolean { return this.initialized }

  onCommit(callback: CommitCallback): void { this.commitCallbacks.push(callback) }
  onOptionChange(callback: OptionChangeCallback): void { this.optionCallbacks.push(callback) }
  onSchemaChange(callback: SchemaChangeCallback): void { this.schemaCallbacks.push(callback) }
  onError(callback: ErrorCallback): void { this.errorCallbacks.push(callback) }
  onDeployStatus(callback: DeployStatusCallback): void { this.deployStatusCallbacks.push(callback) }
  onResultChange(callback: ResultChangeCallback): void { this.resultCallbacks.push(callback) }

  offCommit(callback: CommitCallback): void { this.commitCallbacks = this.commitCallbacks.filter(cb => cb !== callback) }
  offOptionChange(callback: OptionChangeCallback): void { this.optionCallbacks = this.optionCallbacks.filter(cb => cb !== callback) }
  offSchemaChange(callback: SchemaChangeCallback): void { this.schemaCallbacks = this.schemaCallbacks.filter(cb => cb !== callback) }
  offError(callback: ErrorCallback): void { this.errorCallbacks = this.errorCallbacks.filter(cb => cb !== callback) }
  offDeployStatus(callback: DeployStatusCallback): void { this.deployStatusCallbacks = this.deployStatusCallbacks.filter(cb => cb !== callback) }
  offResultChange(callback: ResultChangeCallback): void { this.resultCallbacks = this.resultCallbacks.filter(cb => cb !== callback) }

  destroy(): void {
    this.initialized = false
    this._lastResult = null
    this.backend.destroy()
    this.commitCallbacks = []
    this.optionCallbacks = []
    this.schemaCallbacks = []
    this.errorCallbacks = []
    this.deployStatusCallbacks = []
    this.resultCallbacks = []
  }

  private handleResult(result: RimeResult): void {
    if (result.committed) {
      this.commitCallbacks.forEach(cb => cb(result.committed))
    }
    if (result.updatedOptions && Object.keys(result.updatedOptions).length > 0) {
      this.syncState(result.updatedOptions)
      this.optionCallbacks.forEach(cb => cb(result.updatedOptions))
    }
    if (result.updatedSchema) {
      this._state.currentSchema = result.updatedSchema
      this.saveState()
      this.schemaCallbacks.forEach(cb => cb(result.updatedSchema))
    }
  }

  private syncState(opts: Record<string, boolean>): void {
    if ('ascii_mode' in opts) this._state.isEnglish = opts.ascii_mode
    if ('full_shape' in opts) this._state.isFullWidth = opts.full_shape
    if ('ascii_punct' in opts) this._state.isEnglishPunct = opts.ascii_punct
    if ('emoji_suggestion' in opts) this._state.isEmoji = opts.emoji_suggestion
    if ('simplification' in opts) this._state.isSimplification = opts.simplification
    this.saveState()
  }

  private saveState(): void {
    if (!this._persist) return
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this._state))
    } catch {}
  }

  private loadState(): void {
    if (!this._persist) return
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (!raw) return
      const saved = JSON.parse(raw) as Partial<RimeState>
      if (saved.currentSchema) this._state.currentSchema = saved.currentSchema
      if (typeof saved.isEnglish === 'boolean') this._state.isEnglish = saved.isEnglish
      if (typeof saved.isFullWidth === 'boolean') this._state.isFullWidth = saved.isFullWidth
      if (typeof saved.isEnglishPunct === 'boolean') this._state.isEnglishPunct = saved.isEnglishPunct
      if (typeof saved.isEmoji === 'boolean') this._state.isEmoji = saved.isEmoji
      if (typeof saved.isSimplification === 'boolean') this._state.isSimplification = saved.isSimplification
    } catch {}
  }

  private ensureInit(): void {
    if (!this.initialized) throw new Error('RimeIME not initialized. Call init() first.')
  }
}

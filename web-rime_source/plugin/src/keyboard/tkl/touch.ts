/**
 * TKL 键盘触摸/鼠标处理
 *
 * 管理触摸/鼠标事件、修饰键 sticky 逻辑、
 * 按键高亮、Backspace 自动重复。
 * 无长按候选弹出、无滑动选键（TKL 不需要）。
 */

import type { TKLKeyDef, TKLKeyAction } from './layouts'
import type { TKLKeyboardDOM } from './dom'
import type { ModifierState, TKLShiftState } from './render'
import { isModifierAction } from './render'

export interface TKLTouchCallbacks {
  fireKey: (keyDef: TKLKeyDef) => void
  haptic: () => void
  getKeyDef: (key: string) => TKLKeyDef | null
}

export class TKLTouchHandler {
  private keysEl: HTMLDivElement
  private containerEl: HTMLDivElement
  private dom: TKLKeyboardDOM
  private cb: TKLTouchCallbacks

  private activeKeyEl: HTMLElement | null = null
  private activeKeyDef: TKLKeyDef | null = null
  private backspaceTimer: ReturnType<typeof setTimeout> | null = null
  private destroyed = false

  // 绑定引用
  private boundTouchStart: (e: TouchEvent) => void
  private boundTouchMove: (e: TouchEvent) => void
  private boundTouchEnd: (e: TouchEvent) => void
  private boundMouseDown: (e: MouseEvent) => void
  private boundMouseMove: (e: MouseEvent) => void
  private boundMouseUp: (e: MouseEvent) => void
  private boundContainerTouchStart: (e: TouchEvent) => void
  private boundContainerMouseDown: (e: MouseEvent) => void

  /** 标记触摸在键盘上 */
  keyTouched = false

  constructor(
    keysEl: HTMLDivElement,
    containerEl: HTMLDivElement,
    dom: TKLKeyboardDOM,
    callbacks: TKLTouchCallbacks
  ) {
    this.keysEl = keysEl
    this.containerEl = containerEl
    this.dom = dom
    this.cb = callbacks

    this.boundTouchStart = (e) => this.onTouchStart(e)
    this.boundTouchMove = (e) => this.onTouchMove(e)
    this.boundTouchEnd = (e) => this.onTouchEnd(e)
    this.boundMouseDown = (e) => this.onMouseDown(e)
    this.boundMouseMove = (e) => this.onMouseMove(e)
    this.boundMouseUp = (e) => this.onMouseUp(e)
    this.boundContainerTouchStart = (e) => this.onContainerTouchStart(e)
    this.boundContainerMouseDown = (e) => this.onContainerMouseDown(e)
  }

  bind(): void {
    this.keysEl.addEventListener('touchstart', this.boundTouchStart, { passive: false })
    this.keysEl.addEventListener('touchmove', this.boundTouchMove, { passive: false })
    this.keysEl.addEventListener('touchend', this.boundTouchEnd, { passive: false })
    this.keysEl.addEventListener('touchcancel', this.boundTouchEnd, { passive: false })
    this.keysEl.addEventListener('mousedown', this.boundMouseDown)
    document.addEventListener('mousemove', this.boundMouseMove)
    document.addEventListener('mouseup', this.boundMouseUp)

    this.containerEl.addEventListener('touchmove', (e: TouchEvent) => {
      e.preventDefault()
    }, { passive: false })
    this.containerEl.addEventListener('touchstart', this.boundContainerTouchStart, { passive: true })
    this.containerEl.addEventListener('mousedown', this.boundContainerMouseDown)
  }

  destroy(): void {
    this.destroyed = true
    this.clearTimers()
    this.keysEl.removeEventListener('touchstart', this.boundTouchStart)
    this.keysEl.removeEventListener('touchmove', this.boundTouchMove)
    this.keysEl.removeEventListener('touchend', this.boundTouchEnd)
    this.keysEl.removeEventListener('touchcancel', this.boundTouchEnd)
    this.keysEl.removeEventListener('mousedown', this.boundMouseDown)
    document.removeEventListener('mousemove', this.boundMouseMove)
    document.removeEventListener('mouseup', this.boundMouseUp)
    this.containerEl.removeEventListener('touchstart', this.boundContainerTouchStart)
    this.containerEl.removeEventListener('mousedown', this.boundContainerMouseDown)
  }

  // ─── 触摸事件 ───

  private onTouchStart(e: TouchEvent): void {
    e.preventDefault()
    this.keyTouched = true
    this.handlePointerDown(e.touches[0].clientX, e.touches[0].clientY)
  }

  private onTouchMove(e: TouchEvent): void {
    e.preventDefault()
    if (!this.activeKeyEl) return
    this.handlePointerMove(e.touches[0].clientX, e.touches[0].clientY)
  }

  private onTouchEnd(e: TouchEvent): void {
    e.preventDefault()
    this.handlePointerUp()
    setTimeout(() => { this.keyTouched = false }, 200)
  }

  private onMouseDown(e: MouseEvent): void {
    e.preventDefault()
    this.keyTouched = true
    this.handlePointerDown(e.clientX, e.clientY)
  }

  private onMouseMove(e: MouseEvent): void {
    if (!this.activeKeyEl) return
    this.handlePointerMove(e.clientX, e.clientY)
  }

  private onMouseUp(_e: MouseEvent): void {
    this.handlePointerUp()
    setTimeout(() => { this.keyTouched = false }, 200)
  }

  // ─── 容器级触摸标记 ───

  private onContainerTouchStart(_e: TouchEvent): void {
    this.keyTouched = true
    setTimeout(() => { this.keyTouched = false }, 400)
  }

  private onContainerMouseDown(_e: MouseEvent): void {
    this.keyTouched = true
    setTimeout(() => { this.keyTouched = false }, 400)
  }

  // ─── 统一指针处理 ───

  private handlePointerDown(cx: number, cy: number): void {
    const keyEl = this.getKeyAtPoint(cx, cy)
    if (!keyEl) return

    this.activeKeyEl = keyEl
    this.activeKeyDef = this.cb.getKeyDef(keyEl.dataset.key || '')

    // 修饰键不添加按下高亮（由激活状态控制）
    if (this.activeKeyDef && !isModifierAction(this.activeKeyDef.action)) {
      keyEl.classList.add('rime-tkl-key-pressed')
    }

    this.cb.haptic()
    this.startBackspaceRepeat(this.activeKeyDef)
  }

  private handlePointerMove(cx: number, cy: number): void {
    const keyEl = this.getKeyAtPoint(cx, cy)

    if (keyEl !== this.activeKeyEl) {
      if (this.activeKeyEl) this.activeKeyEl.classList.remove('rime-tkl-key-pressed')
      this.clearTimers()

      if (keyEl) {
        this.activeKeyEl = keyEl
        this.activeKeyDef = this.cb.getKeyDef(keyEl.dataset.key || '')
        if (this.activeKeyDef && !isModifierAction(this.activeKeyDef.action)) {
          keyEl.classList.add('rime-tkl-key-pressed')
        }
        this.startBackspaceRepeat(this.activeKeyDef)
      } else {
        this.activeKeyEl = null
        this.activeKeyDef = null
      }
    }
  }

  private handlePointerUp(): void {
    this.clearTimers()

    if (this.activeKeyDef && this.activeKeyEl) {
      this.cb.fireKey(this.activeKeyDef)
    }

    if (this.activeKeyEl) this.activeKeyEl.classList.remove('rime-tkl-key-pressed')
    this.activeKeyEl = null
    this.activeKeyDef = null
  }

  // ─── 按键定位 ───

  private getKeyAtPoint(cx: number, cy: number): HTMLElement | null {
    const pv = this.dom.preview.style.display
    this.dom.preview.style.display = 'none'
    const el = document.elementFromPoint(cx, cy)
    this.dom.preview.style.display = pv
    if (!el) return null
    return (el.closest('.rime-tkl-key') as HTMLElement) || null
  }

  // ─── Backspace 自动重复 ───

  private startBackspaceRepeat(keyDef: TKLKeyDef | null): void {
    if (!keyDef || keyDef.action !== 'backspace') return
    this.backspaceTimer = setTimeout(() => {
      const repeat = () => {
        if (!this.activeKeyEl || this.activeKeyDef?.action !== 'backspace') return
        this.cb.fireKey(this.activeKeyDef)
        this.backspaceTimer = setTimeout(repeat, 80)
      }
      this.backspaceTimer = setTimeout(repeat, 80)
    }, 500)
  }

  private clearTimers(): void {
    if (this.backspaceTimer !== null) {
      clearTimeout(this.backspaceTimer)
      this.backspaceTimer = null
    }
  }
}

/**
 * 虚拟键盘触摸处理
 *
 * 管理触摸/鼠标事件、按键高亮、滑动选键、
 * 长按候选弹出、按键预览气泡、Backspace 自动重复。
 */

import type { KeyDef } from './layouts'
import type { TouchCallbacks } from './types'
import { PUNCT_ALT } from './types'

export class KeyboardTouchHandler {
  private keysEl: HTMLDivElement
  private containerEl: HTMLDivElement
  private previewEl: HTMLDivElement
  private altEl: HTMLDivElement
  private cb: TouchCallbacks

  // 触摸状态
  private activeKeyEl: HTMLElement | null = null
  private activeKeyDef: KeyDef | null = null
  private longPressTimer: ReturnType<typeof setTimeout> | null = null
  private backspaceTimer: ReturnType<typeof setTimeout> | null = null
  private altVisible = false
  private altSelectedIndex = -1
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

  constructor(
    keysEl: HTMLDivElement,
    containerEl: HTMLDivElement,
    previewEl: HTMLDivElement,
    altEl: HTMLDivElement,
    callbacks: TouchCallbacks
  ) {
    this.keysEl = keysEl
    this.containerEl = containerEl
    this.previewEl = previewEl
    this.altEl = altEl
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

  /** 绑定事件 */
  bind(): void {
    this.keysEl.addEventListener('touchstart', this.boundTouchStart, { passive: false })
    this.keysEl.addEventListener('touchmove', this.boundTouchMove, { passive: false })
    this.keysEl.addEventListener('touchend', this.boundTouchEnd, { passive: false })
    this.keysEl.addEventListener('touchcancel', this.boundTouchEnd, { passive: false })
    this.keysEl.addEventListener('mousedown', this.boundMouseDown)
    document.addEventListener('mousemove', this.boundMouseMove)
    document.addEventListener('mouseup', this.boundMouseUp)

    // 阻止键盘区域滚动 + 标记容器级触摸
    this.containerEl.addEventListener('touchmove', (e: TouchEvent) => {
      e.preventDefault()
    }, { passive: false })
    this.containerEl.addEventListener('touchstart', this.boundContainerTouchStart, { passive: true })
    this.containerEl.addEventListener('mousedown', this.boundContainerMouseDown)
  }

  /** 销毁，移除所有事件 */
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

  /** 标记触摸在键盘上（供外部 blur 处理使用） */
  keyTouched = false

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

  // ─── 容器级触摸标记（防止工具栏/候选栏点击导致键盘隐藏）───

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
    keyEl.classList.add('rime-kb-key-active')
    this.showPreview(keyEl)
    this.cb.haptic()
    this.startLongPress(keyEl, this.activeKeyDef)
  }

  private handlePointerMove(cx: number, cy: number): void {
    const keyEl = this.getKeyAtPoint(cx, cy)

    if (keyEl !== this.activeKeyEl) {
      if (this.activeKeyEl) this.activeKeyEl.classList.remove('rime-kb-key-active')
      this.hidePreview()
      this.cancelLongPress()

      if (keyEl) {
        this.activeKeyEl = keyEl
        this.activeKeyDef = this.cb.getKeyDef(keyEl.dataset.key || '')
        keyEl.classList.add('rime-kb-key-active')
        this.showPreview(keyEl)
        this.startLongPress(keyEl, this.activeKeyDef)
      } else {
        this.activeKeyEl = null
        this.activeKeyDef = null
      }
    }

    if (this.altVisible) this.handleAltMove(cx, cy)
  }

  private handlePointerUp(): void {
    this.clearTimers()

    if (this.altVisible && this.altSelectedIndex >= 0) {
      this.selectAltItem(this.altSelectedIndex)
    } else if (this.activeKeyDef && this.activeKeyEl) {
      this.cb.fireKey(this.activeKeyDef)
    }

    if (this.activeKeyEl) this.activeKeyEl.classList.remove('rime-kb-key-active')
    this.hidePreview()
    this.hideAlt()
    this.activeKeyEl = null
    this.activeKeyDef = null
  }

  // ─── 按键定位 ───

  /** 获取坐标下的按键元素 */
  private getKeyAtPoint(cx: number, cy: number): HTMLElement | null {
    const pv = this.previewEl.style.display
    const al = this.altEl.style.display
    this.previewEl.style.display = 'none'
    this.altEl.style.display = 'none'
    const el = document.elementFromPoint(cx, cy)
    this.previewEl.style.display = pv
    this.altEl.style.display = al
    if (!el) return null
    return (el.closest('.rime-kb-key') as HTMLElement) || null
  }

  // ─── 长按 ───

  private startLongPress(keyEl: HTMLElement, keyDef: KeyDef | null): void {
    this.cancelLongPress()
    if (!keyDef) return

    if (keyDef.action === 'backspace') {
      this.backspaceTimer = setTimeout(() => this.startBackspaceRepeat(), 500)
      return
    }

    const altItems = this.getAltItems(keyDef)
    if (altItems.length === 0) return

    this.longPressTimer = setTimeout(() => {
      this.showAlt(keyEl, altItems)
    }, 500)
  }

  private cancelLongPress(): void {
    if (this.longPressTimer !== null) {
      clearTimeout(this.longPressTimer)
      this.longPressTimer = null
    }
  }

  private getAltItems(keyDef: KeyDef): string[] {
    if (keyDef.action === 'punct') return PUNCT_ALT
    return keyDef.alt ?? []
  }

  // ─── 预览气泡 ───

  private showPreview(keyEl: HTMLElement): void {
    if (this.altVisible) return
    const label = keyEl.textContent || ''
    const rect = keyEl.getBoundingClientRect()

    this.previewEl.textContent = label
    this.previewEl.style.display = 'flex'

    const pw = this.previewEl.offsetWidth
    const ph = this.previewEl.offsetHeight
    let x = rect.left + rect.width / 2 - pw / 2
    let y = rect.top - ph - 6

    if (x < 4) x = 4
    if (x + pw > window.innerWidth - 4) x = window.innerWidth - pw - 4
    if (y < 4) y = rect.bottom + 6

    this.previewEl.style.left = x + 'px'
    this.previewEl.style.top = y + 'px'
  }

  private hidePreview(): void {
    this.previewEl.style.display = 'none'
  }

  // ─── 长按候选弹出 ───

  private showAlt(keyEl: HTMLElement, items: string[]): void {
    this.altVisible = true
    this.altSelectedIndex = -1
    this.hidePreview()

    this.altEl.innerHTML = items.map((item, i) =>
      `<span class="rime-kb-alt-item" data-idx="${i}">${esc(item)}</span>`
    ).join('')
    this.altEl.style.display = 'flex'

    const rect = keyEl.getBoundingClientRect()
    const aw = this.altEl.offsetWidth
    let x = rect.left + rect.width / 2 - aw / 2
    let y = rect.top - this.altEl.offsetHeight - 8

    if (x < 4) x = 4
    if (x + aw > window.innerWidth - 4) x = window.innerWidth - aw - 4
    if (y < 4) y = rect.bottom + 8

    this.altEl.style.left = x + 'px'
    this.altEl.style.top = y + 'px'
  }

  private handleAltMove(cx: number, cy: number): void {
    const items = this.altEl.querySelectorAll('.rime-kb-alt-item')
    let found = -1
    items.forEach((item, i) => {
      const rect = item.getBoundingClientRect()
      if (cx >= rect.left && cx <= rect.right && cy >= rect.top && cy <= rect.bottom) found = i
      item.classList.toggle('rime-kb-alt-item-active', i === found)
    })
    this.altSelectedIndex = found
  }

  private selectAltItem(index: number): void {
    const items = this.altEl.querySelectorAll('.rime-kb-alt-item')
    if (index < 0 || index >= items.length) return
    const text = items[index].textContent || ''
    this.cb.insertText(text)
    this.cb.haptic()
  }

  private hideAlt(): void {
    this.altVisible = false
    this.altSelectedIndex = -1
    this.altEl.style.display = 'none'
  }

  // ─── Backspace 自动重复 ───

  private startBackspaceRepeat(): void {
    const repeat = () => {
      if (!this.activeKeyEl || this.activeKeyDef?.action !== 'backspace') return
      this.cb.fireKey(this.activeKeyDef)
      this.backspaceTimer = setTimeout(repeat, 80)
    }
    this.backspaceTimer = setTimeout(repeat, 80)
  }

  // ─── 工具 ───

  private clearTimers(): void {
    this.cancelLongPress()
    if (this.backspaceTimer !== null) {
      clearTimeout(this.backspaceTimer)
      this.backspaceTimer = null
    }
  }
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

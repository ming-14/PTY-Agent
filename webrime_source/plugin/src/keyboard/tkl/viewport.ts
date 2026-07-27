/**
 * TKL 键盘视口与拖拽控制
 *
 * 仅 floating 模式：show/hide、拖拽定位。
 * 无 docked 模式、无拉伸。
 */

export class TKLViewportController {
  private container: HTMLDivElement
  private target: HTMLElement
  private dragHandle: HTMLDivElement
  private getVisible: () => boolean
  private getShowOnFocus: () => boolean
  private onShow: () => void
  private onHide: () => void
  private floatingWidth: number
  private floatingHeight: number

  // 拖拽状态
  private dragging = false
  private dragStartX = 0
  private dragStartY = 0
  private posX = 0
  private posY = 0

  // 绑定引用
  private boundFocus: () => void
  private boundBlur: () => void
  private boundClick: () => void
  private boundPointerDown: () => void
  private boundResize: () => void
  private boundDragTouchStart: (e: TouchEvent) => void
  private boundDragTouchMove: (e: TouchEvent) => void
  private boundDragTouchEnd: () => void
  private boundDragMouseStart: (e: MouseEvent) => void
  private boundDragMouseMove: (e: MouseEvent) => void
  private boundDragMouseEnd: () => void

  // 目标元素属性备份
  private savedInputMode: string | null = null
  private savedAutoComplete: string | null = null

  // 外部读取触摸状态
  private getKeyTouched: () => boolean

  constructor(
    container: HTMLDivElement,
    target: HTMLElement,
    dragHandle: HTMLDivElement,
    getVisible: () => boolean,
    getKeyTouched: () => boolean,
    getShowOnFocus: () => boolean,
    onShow: () => void,
    onHide: () => void,
    floatingWidth: number = 780,
    floatingHeight: number = 280
  ) {
    this.container = container
    this.target = target
    this.dragHandle = dragHandle
    this.getVisible = getVisible
    this.getKeyTouched = getKeyTouched
    this.getShowOnFocus = getShowOnFocus
    this.onShow = onShow
    this.onHide = onHide
    this.floatingWidth = floatingWidth
    this.floatingHeight = floatingHeight

    this.boundFocus = () => this.onTargetFocus()
    this.boundBlur = () => this.onTargetBlur()
    this.boundClick = () => this.onTargetClick()
    this.boundPointerDown = () => this.onTargetClick()
    this.boundResize = () => this.onResize()
    this.boundDragTouchStart = (e) => this.onDragTouchStart(e)
    this.boundDragTouchMove = (e) => this.onDragTouchMove(e)
    this.boundDragTouchEnd = () => { this.onDragEnd() }
    this.boundDragMouseStart = (e) => this.onDragMouseStart(e)
    this.boundDragMouseMove = (e) => this.onDragMouseMove(e)
    this.boundDragMouseEnd = () => { this.onDragEnd() }
  }

  setupTarget(): void {
    if (this.isTextInput(this.target)) {
      this.savedInputMode = this.target.getAttribute('inputmode')
      this.savedAutoComplete = this.target.getAttribute('autocomplete')
      this.target.setAttribute('inputmode', 'none')
      this.target.setAttribute('autocomplete', 'off')
    }
  }

  restoreTarget(): void {
    if (this.isTextInput(this.target)) {
      if (this.savedInputMode !== null) this.target.setAttribute('inputmode', this.savedInputMode)
      else this.target.removeAttribute('inputmode')
      if (this.savedAutoComplete !== null) this.target.setAttribute('autocomplete', this.savedAutoComplete)
      else this.target.removeAttribute('autocomplete')
    }
  }

  bind(): void {
    this.target.addEventListener('focus', this.boundFocus)
    this.target.addEventListener('blur', this.boundBlur)
    this.target.addEventListener('click', this.boundClick)
    this.target.addEventListener('pointerdown', this.boundPointerDown)
    window.addEventListener('resize', this.boundResize)
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', this.boundResize)
      window.visualViewport.addEventListener('scroll', this.boundResize)
    }

    // 拦截原生 compositionstart
    this.target.addEventListener('compositionstart', (e: Event) => {
      e.preventDefault()
      try { (this.target as any).value = (this.target as any).value } catch {}
    })

    // 拖拽事件
    this.dragHandle.addEventListener('touchstart', this.boundDragTouchStart, { passive: false })
    document.addEventListener('touchmove', this.boundDragTouchMove, { passive: false })
    document.addEventListener('touchend', this.boundDragTouchEnd)
    this.dragHandle.addEventListener('mousedown', this.boundDragMouseStart)
    document.addEventListener('mousemove', this.boundDragMouseMove)
    document.addEventListener('mouseup', this.boundDragMouseEnd)
  }

  destroy(): void {
    this.target.removeEventListener('focus', this.boundFocus)
    this.target.removeEventListener('blur', this.boundBlur)
    this.target.removeEventListener('click', this.boundClick)
    this.target.removeEventListener('pointerdown', this.boundPointerDown)
    window.removeEventListener('resize', this.boundResize)
    if (window.visualViewport) {
      window.visualViewport.removeEventListener('resize', this.boundResize)
      window.visualViewport.removeEventListener('scroll', this.boundResize)
    }
    this.dragHandle.removeEventListener('touchstart', this.boundDragTouchStart)
    document.removeEventListener('touchmove', this.boundDragTouchMove)
    document.removeEventListener('touchend', this.boundDragTouchEnd)
    this.dragHandle.removeEventListener('mousedown', this.boundDragMouseStart)
    document.removeEventListener('mousemove', this.boundDragMouseMove)
    document.removeEventListener('mouseup', this.boundDragMouseEnd)
  }

  // ─── Show / Hide ───

  show(): void {
    this.container.classList.remove('rime-tkl-hidden')
    this.onShow()
  }

  hide(): void {
    this.container.classList.add('rime-tkl-hidden')
    this.onHide()
  }

  focusTarget(): void {
    if (this.isTextInput(this.target) && document.activeElement !== this.target) {
      this.target.focus()
    }
  }

  // ─── Focus / Blur ───

  private onTargetFocus(): void {
    if (!this.getShowOnFocus()) return
    this.show()
  }

  private onTargetClick(): void {
    if (!this.getShowOnFocus()) return
    this.show()
  }

  private onTargetBlur(): void {
    if (this.getKeyTouched()) {
      this.focusTarget()
      return
    }
    setTimeout(() => {
      if (document.activeElement !== this.target && !this.getKeyTouched()) {
        const active = document.activeElement as HTMLElement | null
        if (active && active.closest('.rime-toolbar')) {
          this.focusTarget()
          return
        }
        this.hide()
      }
    }, 150)
  }

  // ─── 视口调整 ───

  private onResize(): void {
    this.adjustFloatingSize()
  }

  adjustFloatingSize(): void {
    const vw = window.innerWidth
    const vh = window.innerHeight
    const maxW = Math.min(vw - 16, this.floatingWidth)
    const maxH = Math.min(vh - 16, this.floatingHeight)
    this.container.style.maxWidth = maxW + 'px'
    this.container.style.maxHeight = maxH + 'px'
  }

  // ─── 拖拽 ───

  private onDragTouchStart(e: TouchEvent): void {
    if ((e.target as HTMLElement).closest('.rime-tkl-tb-hide')) return
    e.preventDefault()
    e.stopPropagation()
    this.startDrag(e.touches[0].clientX, e.touches[0].clientY)
  }

  private onDragTouchMove(e: TouchEvent): void {
    if (!this.dragging) return
    e.preventDefault()
    this.moveDrag(e.touches[0].clientX, e.touches[0].clientY)
  }

  private onDragMouseStart(e: MouseEvent): void {
    e.preventDefault()
    e.stopPropagation()
    this.startDrag(e.clientX, e.clientY)
  }

  private onDragMouseMove(e: MouseEvent): void {
    if (!this.dragging) return
    this.moveDrag(e.clientX, e.clientY)
  }

  private onDragEnd(): void {
    this.dragging = false
  }

  private startDrag(cx: number, cy: number): void {
    this.dragging = true
    this.dragStartX = cx
    this.dragStartY = cy
    const rect = this.container.getBoundingClientRect()
    this.posX = rect.left
    this.posY = rect.top
  }

  private moveDrag(cx: number, cy: number): void {
    this.posX += cx - this.dragStartX
    this.posY += cy - this.dragStartY
    this.dragStartX = cx
    this.dragStartY = cy

    const rect = this.container.getBoundingClientRect()
    const vw = window.innerWidth
    const vh = window.innerHeight
    if (this.posX < 0) this.posX = 0
    if (this.posX + rect.width > vw) this.posX = vw - rect.width
    if (this.posY < 0) this.posY = 0
    if (this.posY + rect.height > vh) this.posY = vh - rect.height

    this.container.style.left = this.posX + 'px'
    this.container.style.top = this.posY + 'px'
  }

  // ─── 工具 ───

  private isTextInput(el: HTMLElement): el is HTMLTextAreaElement | HTMLInputElement {
    return el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement
  }
}

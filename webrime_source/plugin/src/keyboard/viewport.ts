/**
 * 虚拟键盘视口与拖拽控制
 *
 * 管理：show/hide、目标元素 focus/blur、视口调整、
 * compositionstart 拦截、Floating 模式拖拽。
 */

import type { KeyboardMode } from './types'

export class KeyboardViewportController {
  private container: HTMLDivElement
  private target: HTMLElement
  private dragHandle: HTMLDivElement
  private getMode: () => KeyboardMode
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

  // docked 拉伸状态
  private resizing = false
  private resizeStartY = 0
  private resizeStartHeight = 0
  private resizeMinHeight = 0
  private dockedHeight = 0

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
    getMode: () => KeyboardMode,
    getVisible: () => boolean,
    getKeyTouched: () => boolean,
    getShowOnFocus: () => boolean,
    onShow: () => void,
    onHide: () => void,
    floatingWidth: number = 320,
    floatingHeight: number = 220
  ) {
    this.container = container
    this.target = target
    this.dragHandle = dragHandle
    this.getMode = getMode
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

  /** 设置目标元素的 inputmode 以阻止系统键盘 */
  setupTarget(): void {
    if (this.isTextInput(this.target)) {
      this.savedInputMode = this.target.getAttribute('inputmode')
      this.savedAutoComplete = this.target.getAttribute('autocomplete')
      this.target.setAttribute('inputmode', 'none')
      this.target.setAttribute('autocomplete', 'off')
    }
  }

  /** 恢复目标元素属性 */
  restoreTarget(): void {
    if (this.isTextInput(this.target)) {
      if (this.savedInputMode !== null) this.target.setAttribute('inputmode', this.savedInputMode)
      else this.target.removeAttribute('inputmode')
      if (this.savedAutoComplete !== null) this.target.setAttribute('autocomplete', this.savedAutoComplete)
      else this.target.removeAttribute('autocomplete')
    }
  }

  /** 绑定所有事件 */
  bind(): void {
    // 目标元素事件
    this.target.addEventListener('focus', this.boundFocus)
    this.target.addEventListener('blur', this.boundBlur)
    this.target.addEventListener('click', this.boundClick)
    // pointerdown 比 click 更可靠：触摸/鼠标下对已聚焦元素也会触发，
    // 用于「隐藏后再次点击已聚焦输入框」重新弹起键盘。
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

  /** 销毁，移除所有事件 */
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
    this.container.classList.remove('rime-kb-hidden')
    if (this.getMode() === 'docked') this.positionDocked()
    this.adjustViewport()
    this.onShow()
  }

  hide(): void {
    this.container.classList.add('rime-kb-hidden')
    this.onHide()
  }

  /** 聚焦目标元素 */
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

  /**
   * 已聚焦的输入框再次被点击（focus 不会二次触发）时，
   * 若键盘处于隐藏状态则重新弹起，对齐 native 移动端行为。
   */
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
    if (this.getVisible()) this.adjustViewport()
    this.adjustFloatingSize()
  }

  adjustFloatingSize(): void {
    if (this.getMode() !== 'floating') return
    const vw = window.innerWidth
    const vh = window.innerHeight
    const isLandscape = vw > vh
    const maxW = isLandscape ? Math.min(vw * 0.6, 600) : Math.min(vw - 16, this.floatingWidth)
    const maxH = isLandscape ? Math.min(vh - 16, this.floatingHeight) : Math.min(vh * 0.5, 320)
    this.container.style.maxWidth = maxW + 'px'
    this.container.style.maxHeight = maxH + 'px'
  }

  private adjustViewport(): void {
    if (!this.isTextInput(this.target)) return
    this.positionDocked()
    requestAnimationFrame(() => {
      const targetRect = this.target.getBoundingClientRect()
      const kbTop = this.container.getBoundingClientRect().top
      if (targetRect.bottom > kbTop - 10) {
        this.target.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      }
    })
  }

  /** docked 模式定位：固定可视视口底部
   *
   * 移动端 position:fixed; bottom:0 相对布局视口（含浏览器 chrome 后方区域），
   * 键盘会被底部导航栏遮挡。用 visualViewport 计算可视视口底边与布局视口底边的
   * 偏移量作为 bottom 值，确保键盘紧贴可视区域底边。
   */
  private positionDocked(): void {
    if (this.getMode() !== 'docked') return
    this.container.style.bottom = ''
    this.container.style.left = '0px'
    this.container.style.right = '0px'
    this.container.style.width = '100%'
    this.container.style.transform = ''

    const vv = window.visualViewport
    const vh = vv ? vv.height : window.innerHeight
    const offset = vv ? Math.max(0, window.innerHeight - vv.offsetTop - vv.height) : 0
    const h = this.container.offsetHeight
    this.container.style.top = (vh - offset - h) + 'px'
  }

  // ─── 拖拽 / 拉伸 ───

  private onDragTouchStart(e: TouchEvent): void {
    // 触摸隐藏按钮时不阻止默认行为，让 click 事件正常触发以执行 hide
    if ((e.target as HTMLElement).closest('.rime-kb-tb-hide')) return
    e.preventDefault()
    e.stopPropagation()
    if (this.getMode() === 'docked') {
      this.startResize(e.touches[0].clientY)
    } else {
      this.startDrag(e.touches[0].clientX, e.touches[0].clientY)
    }
  }

  private onDragTouchMove(e: TouchEvent): void {
    if (!this.dragging && !this.resizing) return
    e.preventDefault()
    if (this.resizing) {
      this.moveResize(e.touches[0].clientY)
    } else {
      this.moveDrag(e.touches[0].clientX, e.touches[0].clientY)
    }
  }

  private onDragMouseStart(e: MouseEvent): void {
    e.preventDefault()
    e.stopPropagation()
    if (this.getMode() === 'docked') {
      this.startResize(e.clientY)
    } else {
      this.startDrag(e.clientX, e.clientY)
    }
  }

  private onDragMouseMove(e: MouseEvent): void {
    if (!this.dragging && !this.resizing) return
    if (this.resizing) {
      this.moveResize(e.clientY)
    } else {
      this.moveDrag(e.clientX, e.clientY)
    }
  }

  private onDragEnd(): void {
    this.dragging = false
    this.resizing = false
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

  // ─── Docked 模式拉伸 ───

  private startResize(cy: number): void {
    this.resizing = true
    this.resizeStartY = cy
    this.resizeStartHeight = this.container.offsetHeight
    const saved = this.container.style.height
    this.container.style.height = ''
    this.resizeMinHeight = this.container.offsetHeight
    this.container.style.height = saved
  }

  private moveResize(cy: number): void {
    const delta = this.resizeStartY - cy
    let newH = this.resizeStartHeight + delta
    const vv = window.visualViewport
    const vh = vv ? vv.height : window.innerHeight
    const minH = this.resizeMinHeight || 160
    const maxH = vh * 0.75
    newH = Math.max(minH, Math.min(maxH, newH))
    this.dockedHeight = newH
    this.container.style.height = newH + 'px'
    this.positionDocked()
  }

  /** 获取当前 docked 高度（供外部恢复） */
  getDockedHeight(): number { return this.dockedHeight }

  // ─── 工具 ───

  private isTextInput(el: HTMLElement): el is HTMLTextAreaElement | HTMLInputElement {
    return el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement
  }
}

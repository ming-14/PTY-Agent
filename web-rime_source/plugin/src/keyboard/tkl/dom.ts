/**
 * TKL 键盘 DOM 创建
 *
 * 纯工厂函数，创建 TKL 键盘所有 DOM 元素并返回引用集合。
 * 无候选词栏、无预编辑栏（由外部 RimePanel 显示）。
 */

export interface TKLKeyboardDOM {
  container: HTMLDivElement
  toolbar: HTMLDivElement
  keys: HTMLDivElement
  preview: HTMLDivElement
  dragHandle: HTMLDivElement
  hideBtn: HTMLButtonElement
}

/** 创建 TKL 键盘所有 DOM 元素 */
export function createTKLKeyboardDOM(): TKLKeyboardDOM {
  const container = div('rime-tkl')
  const toolbar = createToolbar()
  const keys = div('rime-tkl-keys')
  const preview = div('rime-tkl-preview')
  preview.style.display = 'none'

  container.appendChild(toolbar)
  container.appendChild(keys)

  const hideBtn = toolbar.querySelector('.rime-tkl-tb-hide') as HTMLButtonElement

  return {
    container,
    toolbar,
    keys,
    preview,
    dragHandle: toolbar,
    hideBtn,
  }
}

function div(cls: string): HTMLDivElement {
  const el = document.createElement('div')
  el.className = cls
  return el
}

/** 拖拽条（含拖拽手柄 + 隐藏按钮） */
function createToolbar(): HTMLDivElement {
  const el = div('rime-tkl-toolbar')
  el.innerHTML = `
    <div class="rime-tkl-tb-drag">\u2261</div>
    <button class="rime-tkl-tb-hide" type="button" title="隐藏键盘">\u25BC</button>
  `
  return el
}

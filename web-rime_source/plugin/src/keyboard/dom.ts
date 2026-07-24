/**
 * 虚拟键盘 DOM 创建
 *
 * 纯工厂函数，创建所有 DOM 元素并返回引用集合。
 * 不绑定事件，不持有状态。
 */

import type { KeyboardDOM } from './types'

/** 创建键盘所有 DOM 元素 */
export function createKeyboardDOM(): KeyboardDOM {
  const container = div('rime-kb')
  const toolbar = createToolbar()
  const compBar = createCompBar()
  const { candBar, cands, candNav } = createCandBar()
  const keys = div('rime-kb-keys')
  const safe = div('rime-kb-safe')
  const preview = div('rime-kb-preview')
  preview.style.display = 'none'
  const alt = div('rime-kb-alt')
  alt.style.display = 'none'

  container.appendChild(toolbar)
  container.appendChild(compBar)
  container.appendChild(candBar)
  container.appendChild(keys)
  container.appendChild(safe)

  const hideBtn = toolbar.querySelector('.rime-kb-tb-hide') as HTMLButtonElement

  return {
    container, toolbar, compBar, candBar, cands, candNav,
    keys, safe, preview, alt,
    dragHandle: toolbar,
    hideBtn,
  }
}

// ─── 内部工厂 ───

function div(cls: string): HTMLDivElement {
  const el = document.createElement('div')
  el.className = cls
  return el
}

/** 拖拽条（含拖拽手柄 + 隐藏按钮） */
function createToolbar(): HTMLDivElement {
  const el = div('rime-kb-toolbar')
  el.innerHTML = `
    <div class="rime-kb-tb-drag">\u2261</div>
    <button class="rime-kb-tb-hide" type="button" title="隐藏键盘">\u25BC</button>
  `
  return el
}

/** 预编辑显示区（拖拽条下方） */
function createCompBar(): HTMLDivElement {
  const el = div('rime-kb-compbar')
  return el
}

/** 候选栏（仅候选词 + 翻页） */
function createCandBar(): {
  candBar: HTMLDivElement
  cands: HTMLDivElement
  candNav: HTMLDivElement
} {
  const candBar = div('rime-kb-candbar')
  const cands = div('rime-kb-cands')
  const candNav = div('rime-kb-cand-nav')
  candBar.appendChild(cands)
  candBar.appendChild(candNav)
  return { candBar, cands, candNav }
}

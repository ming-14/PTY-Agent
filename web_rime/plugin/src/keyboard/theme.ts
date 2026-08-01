/**
 * 虚拟键盘主题系统
 *
 * 主题预设（dark/light）、尺寸预设（compact/normal/large）、
 * 主题变量解析、CSS 自定义属性应用、全局样式注入。
 */

import type { RimeKeyboardTheme, RimeKeyboardSize, RimeKeyboardThemeVars } from './types'
import { STYLE_ID } from './types'

// ─── 主题预设 ───

export const THEME_PRESETS: Record<RimeKeyboardTheme, RimeKeyboardThemeVars> = {
  dark: {
    kbBg: 'rgba(30,30,34,.98)',
    kbBorder: 'rgba(255,255,255,.06)',
    kbShadow: '0 -2px 12px rgba(0,0,0,.4)',
    keyBg: 'rgba(58,58,64,.92)',
    keyColor: 'rgba(255,255,255,.88)',
    keyActiveBg: 'rgba(112,192,232,.35)',
    keyActiveColor: '#fff',
    fnKeyBg: 'rgba(48,48,54,.92)',
    fnKeyColor: 'rgba(255,255,255,.6)',
    spaceBg: 'rgba(58,58,64,.92)',
    spaceColor: 'rgba(255,255,255,.5)',
    candBarBg: 'rgba(24,24,28,.96)',
    candColor: 'rgba(255,255,255,.82)',
    candActiveBg: 'rgba(112,192,232,.15)',
    compHeadColor: '#63e2b7',
    compBodyColor: '#70c0e8',
    compTailColor: 'rgba(255,255,255,.7)',
    navColor: 'rgba(255,255,255,.4)',
    toolbarBg: 'rgba(24,24,28,.96)',
    toolbarBtnColor: 'rgba(255,255,255,.45)',
    toolbarBtnActiveColor: '#70c0e8',
    previewBg: 'rgba(58,58,64,.96)',
    previewColor: '#fff',
    altBg: 'rgba(40,40,46,.98)',
    altColor: 'rgba(255,255,255,.85)',
    altActiveBg: 'rgba(112,192,232,.3)',
  },
  light: {
    kbBg: 'rgba(245,245,247,.98)',
    kbBorder: 'rgba(0,0,0,.08)',
    kbShadow: '0 -2px 12px rgba(0,0,0,.1)',
    keyBg: 'rgba(255,255,255,.95)',
    keyColor: 'rgba(0,0,0,.85)',
    keyActiveBg: 'rgba(32,128,240,.2)',
    keyActiveColor: '#2080f0',
    fnKeyBg: 'rgba(230,230,233,.95)',
    fnKeyColor: 'rgba(0,0,0,.55)',
    spaceBg: 'rgba(255,255,255,.95)',
    spaceColor: 'rgba(0,0,0,.4)',
    candBarBg: 'rgba(255,255,255,.98)',
    candColor: 'rgba(0,0,0,.82)',
    candActiveBg: 'rgba(32,128,240,.1)',
    compHeadColor: '#18a058',
    compBodyColor: '#2080f0',
    compTailColor: 'rgba(0,0,0,.7)',
    navColor: 'rgba(0,0,0,.4)',
    toolbarBg: 'rgba(255,255,255,.98)',
    toolbarBtnColor: 'rgba(0,0,0,.4)',
    toolbarBtnActiveColor: '#2080f0',
    previewBg: 'rgba(255,255,255,.98)',
    previewColor: 'rgba(0,0,0,.85)',
    altBg: 'rgba(245,245,247,.98)',
    altColor: 'rgba(0,0,0,.85)',
    altActiveBg: 'rgba(32,128,240,.15)',
  },
}

export const SIZE_PRESETS: Record<RimeKeyboardSize, Partial<RimeKeyboardThemeVars>> = {
  compact: {
    keyHeight: '36px', keyFontSize: '15px', keyGap: '3px', keyRadius: '4px',
    candFontSize: '13px', compFontSize: '13px', navFontSize: '10px',
    previewFontSize: '20px', altFontSize: '14px',
    candBarHeight: '32px',
  },
  normal: {
    keyHeight: '44px', keyFontSize: '18px', keyGap: '4px', keyRadius: '5px',
    candFontSize: '15px', compFontSize: '15px', navFontSize: '11px',
    previewFontSize: '26px', altFontSize: '16px',
    candBarHeight: '38px',
  },
  large: {
    keyHeight: '54px', keyFontSize: '22px', keyGap: '5px', keyRadius: '6px',
    candFontSize: '18px', compFontSize: '18px', navFontSize: '13px',
    previewFontSize: '32px', altFontSize: '18px',
    candBarHeight: '44px',
  },
}

const DEFAULT_THEME_VARS: RimeKeyboardThemeVars = {
  kbBg: 'rgba(30,30,34,.98)',
  kbBorder: 'rgba(255,255,255,.06)',
  kbRadius: '0px',
  kbShadow: '0 -2px 12px rgba(0,0,0,.4)',
  kbFontFamily: '-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif',
  kbZIndex: 99999,
  keyBg: 'rgba(58,58,64,.92)',
  keyColor: 'rgba(255,255,255,.88)',
  keyFontSize: '18px',
  keyHeight: '44px',
  keyGap: '4px',
  keyRadius: '5px',
  keyActiveBg: 'rgba(112,192,232,.35)',
  keyActiveColor: '#fff',
  keyActiveScale: '0.96',
  fnKeyBg: 'rgba(48,48,54,.92)',
  fnKeyColor: 'rgba(255,255,255,.6)',
  spaceBg: 'rgba(58,58,64,.92)',
  spaceColor: 'rgba(255,255,255,.5)',
  candBarBg: 'rgba(24,24,28,.96)',
  candBarHeight: '38px',
  candColor: 'rgba(255,255,255,.82)',
  candFontSize: '15px',
  candActiveBg: 'rgba(112,192,232,.15)',
  compHeadColor: '#63e2b7',
  compBodyColor: '#70c0e8',
  compTailColor: 'rgba(255,255,255,.7)',
  compFontSize: '15px',
  navColor: 'rgba(255,255,255,.4)',
  navFontSize: '11px',
  toolbarBg: 'rgba(24,24,28,.96)',
  toolbarBtnColor: 'rgba(255,255,255,.45)',
  toolbarBtnActiveColor: '#70c0e8',
  previewBg: 'rgba(58,58,64,.96)',
  previewColor: '#fff',
  previewFontSize: '26px',
  previewRadius: '6px',
  altBg: 'rgba(40,40,46,.98)',
  altColor: 'rgba(255,255,255,.85)',
  altFontSize: '16px',
  altActiveBg: 'rgba(112,192,232,.3)',
  safeAreaBottom: 'env(safe-area-inset-bottom, 0px)',
}

// ─── 解析与应用 ───

/** 合并主题、尺寸、用户覆盖 */
export function resolveThemeVars(
  theme: RimeKeyboardTheme,
  size: RimeKeyboardSize,
  overrides?: RimeKeyboardThemeVars
): RimeKeyboardThemeVars {
  const base = { ...THEME_PRESETS[theme] }
  const sizeVars = { ...SIZE_PRESETS[size] }
  return { ...DEFAULT_THEME_VARS, ...base, ...sizeVars, ...overrides }
}

/** 将主题变量写入容器 CSS 自定义属性 */
export function applyThemeVars(container: HTMLElement, vars: RimeKeyboardThemeVars): void {
  const s = container.style
  const set = (prop: string, val: string | number | undefined) => {
    if (val !== undefined) s.setProperty(prop, String(val))
  }

  set('--rime-kb-bg', vars.kbBg)
  set('--rime-kb-border', vars.kbBorder)
  set('--rime-kb-radius', vars.kbRadius)
  set('--rime-kb-shadow', vars.kbShadow)
  set('--rime-kb-font-family', vars.kbFontFamily)
  set('--rime-kb-z-index', vars.kbZIndex)
  set('--rime-kb-key-bg', vars.keyBg)
  set('--rime-kb-key-color', vars.keyColor)
  set('--rime-kb-key-font-size', vars.keyFontSize)
  set('--rime-kb-key-height', vars.keyHeight)
  set('--rime-kb-key-gap', vars.keyGap)
  set('--rime-kb-key-radius', vars.keyRadius)
  set('--rime-kb-key-active-bg', vars.keyActiveBg)
  set('--rime-kb-key-active-color', vars.keyActiveColor)
  set('--rime-kb-key-active-scale', vars.keyActiveScale)
  set('--rime-kb-fn-key-bg', vars.fnKeyBg)
  set('--rime-kb-fn-key-color', vars.fnKeyColor)
  set('--rime-kb-space-bg', vars.spaceBg)
  set('--rime-kb-space-color', vars.spaceColor)
  set('--rime-kb-cand-bar-bg', vars.candBarBg)
  set('--rime-kb-cand-bar-height', vars.candBarHeight)
  set('--rime-kb-cand-color', vars.candColor)
  set('--rime-kb-cand-font-size', vars.candFontSize)
  set('--rime-kb-cand-active-bg', vars.candActiveBg)
  set('--rime-kb-comp-head-color', vars.compHeadColor)
  set('--rime-kb-comp-body-color', vars.compBodyColor)
  set('--rime-kb-comp-tail-color', vars.compTailColor)
  set('--rime-kb-comp-font-size', vars.compFontSize)
  set('--rime-kb-nav-color', vars.navColor)
  set('--rime-kb-nav-font-size', vars.navFontSize)
  set('--rime-kb-toolbar-bg', vars.toolbarBg)
  set('--rime-kb-toolbar-btn-color', vars.toolbarBtnColor)
  set('--rime-kb-toolbar-btn-active-color', vars.toolbarBtnActiveColor)
  set('--rime-kb-preview-bg', vars.previewBg)
  set('--rime-kb-preview-color', vars.previewColor)
  set('--rime-kb-preview-font-size', vars.previewFontSize)
  set('--rime-kb-preview-radius', vars.previewRadius)
  set('--rime-kb-alt-bg', vars.altBg)
  set('--rime-kb-alt-color', vars.altColor)
  set('--rime-kb-alt-font-size', vars.altFontSize)
  set('--rime-kb-alt-active-bg', vars.altActiveBg)
  set('--rime-kb-safe-bottom', vars.safeAreaBottom)
}

// ─── 全局样式注入 ───

let styleInstanceCount = 0

/** 注入键盘全局 CSS（单例） */
export function injectKeyboardStyle(): void {
  if (document.getElementById(STYLE_ID)) {
    styleInstanceCount++
    return
  }
  const s = document.createElement('style')
  s.id = STYLE_ID
  s.textContent = CSS
  document.head.appendChild(s)
  styleInstanceCount++
}

/** 移除键盘全局 CSS（引用计数归零时删除） */
export function removeKeyboardStyle(): void {
  styleInstanceCount--
  if (styleInstanceCount <= 0) {
    const st = document.getElementById(STYLE_ID)
    if (st) st.remove()
    styleInstanceCount = 0
  }
}

// ─── CSS ───

const CSS = `
.rime-kb{position:fixed;z-index:var(--rime-kb-z-index,99999);font-family:var(--rime-kb-font-family,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif);user-select:none;-webkit-user-select:none;touch-action:none;overflow:hidden;transition:transform .25s ease,opacity .2s ease;display:flex;flex-direction:column}
.rime-kb-docked{left:0;right:0;width:100%;border-radius:0;border-top:1px solid var(--rime-kb-border,rgba(255,255,255,.06));box-shadow:var(--rime-kb-shadow,0 -2px 12px rgba(0,0,0,.4))}
.rime-kb-floating{border-radius:12px;border:1px solid var(--rime-kb-border,rgba(255,255,255,.06));box-shadow:0 4px 24px rgba(0,0,0,.5)}
.rime-kb-hidden{transform:translateY(100vh);opacity:0;pointer-events:none}
.rime-kb-toolbar{display:flex;align-items:center;justify-content:center;position:relative;padding:2px 6px;height:20px;flex-shrink:0;background:var(--rime-kb-toolbar-bg,rgba(24,24,28,.96));border-bottom:1px solid var(--rime-kb-border,rgba(255,255,255,.06))}
.rime-kb-docked .rime-kb-toolbar{cursor:ns-resize}
.rime-kb-floating .rime-kb-toolbar{cursor:grab}
.rime-kb-floating .rime-kb-toolbar:active{cursor:grabbing}
.rime-kb-tb-drag{color:rgba(255,255,255,.18);font-size:12px;font-weight:700;line-height:1;letter-spacing:3px;pointer-events:none}
.rime-kb-tb-drag:active{cursor:grabbing}
.rime-kb-tb-hide{position:absolute;right:6px;top:50%;transform:translateY(-50%);background:transparent;border:none;color:var(--rime-kb-toolbar-btn-color,rgba(255,255,255,.45));font-size:9px;cursor:pointer;padding:2px 4px;line-height:1;border-radius:3px;-webkit-tap-highlight-color:transparent}
.rime-kb-tb-hide:hover{color:var(--rime-kb-toolbar-btn-active-color,#70c0e8)}
.rime-kb-tb-hide:active{color:var(--rime-kb-toolbar-btn-active-color,#70c0e8)}
.rime-kb-compbar{display:none;align-items:center;padding:0 10px;height:28px;flex-shrink:0;background:var(--rime-kb-cand-bar-bg,rgba(24,24,28,.96));border-bottom:1px solid var(--rime-kb-border,rgba(255,255,255,.06));overflow:hidden;white-space:nowrap}
.rime-kb-compbar-visible{display:flex}
.rime-kb-comp-h{color:var(--rime-kb-comp-head-color,#63e2b7);font-size:var(--rime-kb-comp-font-size,15px)}
.rime-kb-comp-b{color:var(--rime-kb-comp-body-color,#70c0e8);font-size:var(--rime-kb-comp-font-size,15px);text-decoration:underline}
.rime-kb-comp-t{color:var(--rime-kb-comp-tail-color,rgba(255,255,255,.7));font-size:var(--rime-kb-comp-font-size,15px)}
.rime-kb-candbar{display:none;align-items:center;gap:4px;padding:0 8px;height:var(--rime-kb-cand-bar-height,38px);flex-shrink:0;background:var(--rime-kb-cand-bar-bg,rgba(24,24,28,.96));border-bottom:1px solid var(--rime-kb-border,rgba(255,255,255,.06));overflow:hidden}
.rime-kb-candbar-visible{display:flex}
.rime-kb-cands{display:flex;gap:6px;overflow-x:auto;flex:1;scrollbar-width:none;-ms-overflow-style:none}
.rime-kb-cands::-webkit-scrollbar{display:none}
.rime-kb-cand{display:inline-flex;align-items:center;gap:2px;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:var(--rime-kb-cand-font-size,15px);color:var(--rime-kb-cand-color,rgba(255,255,255,.82));white-space:nowrap;transition:background .1s}
.rime-kb-cand:hover,.rime-kb-cand-hl{background:var(--rime-kb-cand-active-bg,rgba(112,192,232,.15))}
.rime-kb-cand-lb{color:var(--rime-kb-comp-body-color,#70c0e8);font-size:0.85em;margin-right:1px}
.rime-kb-cand-nav{display:flex;align-items:center;gap:2px;flex-shrink:0}
.rime-kb-cand-nav-btn{background:transparent;border:none;color:var(--rime-kb-nav-color,rgba(255,255,255,.4));cursor:pointer;font-size:10px;padding:2px 4px;border-radius:2px}
.rime-kb-cand-nav-btn:disabled{opacity:.3;cursor:default}
.rime-kb-cand-nav-btn:hover:not(:disabled){color:var(--rime-kb-comp-body-color,#70c0e8)}
.rime-kb-cand-page{color:var(--rime-kb-nav-color,rgba(255,255,255,.4));font-size:var(--rime-kb-nav-font-size,11px)}
.rime-kb-keys{padding:6px 4px 4px;flex:1;min-height:0;display:flex;flex-direction:column;gap:var(--rime-kb-key-gap,4px);background:var(--rime-kb-bg,rgba(30,30,34,.98))}
.rime-kb-row{display:flex;justify-content:center;gap:var(--rime-kb-key-gap,4px);flex:1 0 auto;align-items:stretch}
.rime-kb-row:last-child{margin-bottom:0}
.rime-kb-key{display:flex;align-items:center;justify-content:center;flex:1;min-height:var(--rime-kb-key-height,44px);background:var(--rime-kb-key-bg,rgba(58,58,64,.92));color:var(--rime-kb-key-color,rgba(255,255,255,.88));border:none;border-radius:var(--rime-kb-key-radius,5px);font-size:var(--rime-kb-key-font-size,18px);font-family:inherit;cursor:pointer;transition:transform .08s,background .08s;-webkit-tap-highlight-color:transparent;outline:none;padding:0}
.rime-kb-key:active,.rime-kb-key-active{background:var(--rime-kb-key-active-bg,rgba(112,192,232,.35));color:var(--rime-kb-key-active-color,#fff);transform:scale(var(--rime-kb-key-active-scale,0.96))}
.rime-kb-key-fn{background:var(--rime-kb-fn-key-bg,rgba(48,48,54,.92));color:var(--rime-kb-fn-key-color,rgba(255,255,255,.6));font-size:calc(var(--rime-kb-key-font-size,18px) * 0.8)}
.rime-kb-key-fn:active,.rime-kb-key-fn.rime-kb-key-active{background:var(--rime-kb-key-active-bg,rgba(112,192,232,.35));color:var(--rime-kb-key-active-color,#fff)}
.rime-kb-key-space{background:var(--rime-kb-space-bg,rgba(58,58,64,.92));color:var(--rime-kb-space-color,rgba(255,255,255,.5));font-size:calc(var(--rime-kb-key-font-size,18px) * 0.75);letter-spacing:2px}
.rime-kb-key-shift.rime-kb-shift-on{background:var(--rime-kb-key-active-bg,rgba(112,192,232,.35));color:var(--rime-kb-key-active-color,#fff)}
.rime-kb-safe{height:var(--rime-kb-safe-bottom,env(safe-area-inset-bottom,0px));flex-shrink:0;background:var(--rime-kb-bg,rgba(30,30,34,.98))}
.rime-kb-preview{position:fixed;z-index:100000;display:flex;align-items:center;justify-content:center;min-width:44px;height:56px;padding:4px 12px;background:var(--rime-kb-preview-bg,rgba(58,58,64,.96));color:var(--rime-kb-preview-color,#fff);font-size:var(--rime-kb-preview-font-size,26px);border-radius:var(--rime-kb-preview-radius,6px);box-shadow:0 2px 8px rgba(0,0,0,.3);pointer-events:none}
.rime-kb-alt{position:fixed;z-index:100001;display:flex;gap:2px;padding:6px 8px;background:var(--rime-kb-alt-bg,rgba(40,40,46,.98));border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.4)}
.rime-kb-alt-item{display:flex;align-items:center;justify-content:center;min-width:36px;height:40px;padding:4px 8px;border-radius:4px;color:var(--rime-kb-alt-color,rgba(255,255,255,.85));font-size:var(--rime-kb-alt-font-size,16px);cursor:pointer;transition:background .1s}
.rime-kb-alt-item-active{background:var(--rime-kb-alt-active-bg,rgba(112,192,232,.3))}
`

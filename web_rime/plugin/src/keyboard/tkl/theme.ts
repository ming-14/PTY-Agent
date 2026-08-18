/**
 * TKL 键盘主题系统
 *
 * 主题预设（dark/light）、主题变量解析、CSS 自定义属性应用、全局样式注入。
 * 按键比手机键盘更小更密，修饰键有专用样式。
 */

export type TKLTheme = 'dark' | 'light'

export interface TKLThemeVars {
  tklBg?: string
  tklBorder?: string
  tklRadius?: string
  tklShadow?: string
  tklFontFamily?: string
  tklZIndex?: number
  keyBg?: string
  keyColor?: string
  keyFontSize?: string
  keyHeight?: string
  keyGap?: string
  keyRadius?: string
  keyActiveBg?: string
  keyActiveColor?: string
  fnKeyBg?: string
  fnKeyColor?: string
  modKeyBg?: string
  modKeyColor?: string
  spaceBg?: string
  spaceColor?: string
  toolbarBg?: string
  toolbarBtnColor?: string
  toolbarBtnActiveColor?: string
  previewBg?: string
  previewColor?: string
  previewFontSize?: string
  previewRadius?: string
}

const THEME_PRESETS: Record<TKLTheme, TKLThemeVars> = {
  dark: {
    tklBg: 'rgba(30,30,34,.98)',
    tklBorder: 'rgba(255,255,255,.06)',
    tklShadow: '0 4px 24px rgba(0,0,0,.5)',
    keyBg: 'rgba(58,58,64,.92)',
    keyColor: 'rgba(255,255,255,.88)',
    keyActiveBg: 'rgba(112,192,232,.35)',
    keyActiveColor: '#fff',
    fnKeyBg: 'rgba(48,48,54,.92)',
    fnKeyColor: 'rgba(255,255,255,.6)',
    modKeyBg: 'rgba(42,42,48,.92)',
    modKeyColor: 'rgba(255,255,255,.6)',
    spaceBg: 'rgba(58,58,64,.92)',
    spaceColor: 'rgba(255,255,255,.5)',
    toolbarBg: 'rgba(24,24,28,.96)',
    toolbarBtnColor: 'rgba(255,255,255,.45)',
    toolbarBtnActiveColor: '#70c0e8',
    previewBg: 'rgba(58,58,64,.96)',
    previewColor: '#fff',
  },
  light: {
    tklBg: 'rgba(245,245,247,.98)',
    tklBorder: 'rgba(0,0,0,.08)',
    tklShadow: '0 4px 24px rgba(0,0,0,.1)',
    keyBg: 'rgba(255,255,255,.95)',
    keyColor: 'rgba(0,0,0,.85)',
    keyActiveBg: 'rgba(32,128,240,.2)',
    keyActiveColor: '#2080f0',
    fnKeyBg: 'rgba(230,230,233,.95)',
    fnKeyColor: 'rgba(0,0,0,.55)',
    modKeyBg: 'rgba(220,220,224,.95)',
    modKeyColor: 'rgba(0,0,0,.55)',
    spaceBg: 'rgba(255,255,255,.95)',
    spaceColor: 'rgba(0,0,0,.4)',
    toolbarBg: 'rgba(255,255,255,.98)',
    toolbarBtnColor: 'rgba(0,0,0,.4)',
    toolbarBtnActiveColor: '#2080f0',
    previewBg: 'rgba(255,255,255,.98)',
    previewColor: 'rgba(0,0,0,.85)',
  },
}

const DEFAULT_THEME_VARS: TKLThemeVars = {
  tklBg: 'rgba(30,30,34,.98)',
  tklBorder: 'rgba(255,255,255,.06)',
  tklRadius: '10px',
  tklShadow: '0 4px 24px rgba(0,0,0,.5)',
  tklFontFamily: '-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif',
  tklZIndex: 99999,
  keyBg: 'rgba(58,58,64,.92)',
  keyColor: 'rgba(255,255,255,.88)',
  keyFontSize: '12px',
  keyHeight: '32px',
  keyGap: '2px',
  keyRadius: '4px',
  keyActiveBg: 'rgba(112,192,232,.35)',
  keyActiveColor: '#fff',
  fnKeyBg: 'rgba(48,48,54,.92)',
  fnKeyColor: 'rgba(255,255,255,.6)',
  modKeyBg: 'rgba(42,42,48,.92)',
  modKeyColor: 'rgba(255,255,255,.6)',
  spaceBg: 'rgba(58,58,64,.92)',
  spaceColor: 'rgba(255,255,255,.5)',
  toolbarBg: 'rgba(24,24,28,.96)',
  toolbarBtnColor: 'rgba(255,255,255,.45)',
  toolbarBtnActiveColor: '#70c0e8',
  previewBg: 'rgba(58,58,64,.96)',
  previewColor: '#fff',
  previewFontSize: '18px',
  previewRadius: '4px',
}

/** 合并主题与用户覆盖 */
export function resolveTKLThemeVars(
  theme: TKLTheme,
  overrides?: TKLThemeVars
): TKLThemeVars {
  const base = { ...THEME_PRESETS[theme] }
  return { ...DEFAULT_THEME_VARS, ...base, ...overrides }
}

/** 将主题变量写入容器 CSS 自定义属性 */
export function applyTKLThemeVars(container: HTMLElement, vars: TKLThemeVars): void {
  const s = container.style
  const set = (prop: string, val: string | number | undefined) => {
    if (val !== undefined) s.setProperty(prop, String(val))
  }

  set('--rime-tkl-bg', vars.tklBg)
  set('--rime-tkl-border', vars.tklBorder)
  set('--rime-tkl-radius', vars.tklRadius)
  set('--rime-tkl-shadow', vars.tklShadow)
  set('--rime-tkl-font-family', vars.tklFontFamily)
  set('--rime-tkl-z-index', vars.tklZIndex)
  set('--rime-tkl-key-bg', vars.keyBg)
  set('--rime-tkl-key-color', vars.keyColor)
  set('--rime-tkl-key-font-size', vars.keyFontSize)
  set('--rime-tkl-key-height', vars.keyHeight)
  set('--rime-tkl-key-gap', vars.keyGap)
  set('--rime-tkl-key-radius', vars.keyRadius)
  set('--rime-tkl-key-active-bg', vars.keyActiveBg)
  set('--rime-tkl-key-active-color', vars.keyActiveColor)
  set('--rime-tkl-fn-key-bg', vars.fnKeyBg)
  set('--rime-tkl-fn-key-color', vars.fnKeyColor)
  set('--rime-tkl-mod-key-bg', vars.modKeyBg)
  set('--rime-tkl-mod-key-color', vars.modKeyColor)
  set('--rime-tkl-space-bg', vars.spaceBg)
  set('--rime-tkl-space-color', vars.spaceColor)
  set('--rime-tkl-toolbar-bg', vars.toolbarBg)
  set('--rime-tkl-toolbar-btn-color', vars.toolbarBtnColor)
  set('--rime-tkl-toolbar-btn-active-color', vars.toolbarBtnActiveColor)
  set('--rime-tkl-preview-bg', vars.previewBg)
  set('--rime-tkl-preview-color', vars.previewColor)
  set('--rime-tkl-preview-font-size', vars.previewFontSize)
  set('--rime-tkl-preview-radius', vars.previewRadius)
}

// ─── 全局样式注入 ───

const TKL_STYLE_ID = 'rime-tkl-style'
let tklStyleInstanceCount = 0

export function injectTKLStyle(): void {
  if (document.getElementById(TKL_STYLE_ID)) {
    tklStyleInstanceCount++
    return
  }
  const s = document.createElement('style')
  s.id = TKL_STYLE_ID
  s.textContent = TKL_CSS
  document.head.appendChild(s)
  tklStyleInstanceCount++
}

export function removeTKLStyle(): void {
  tklStyleInstanceCount--
  if (tklStyleInstanceCount <= 0) {
    const st = document.getElementById(TKL_STYLE_ID)
    if (st) st.remove()
    tklStyleInstanceCount = 0
  }
}

// ─── CSS ───

const TKL_CSS = `
.rime-tkl{position:fixed;z-index:var(--rime-tkl-z-index,99999);font-family:var(--rime-tkl-font-family,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif);user-select:none;-webkit-user-select:none;touch-action:none;overflow:hidden;transition:transform .25s ease,opacity .2s ease;display:flex;flex-direction:column;border-radius:var(--rime-tkl-radius,10px);border:1px solid var(--rime-tkl-border,rgba(255,255,255,.06));box-shadow:var(--rime-tkl-shadow,0 4px 24px rgba(0,0,0,.5))}
.rime-tkl-hidden{transform:translateY(100vh);opacity:0;pointer-events:none}
.rime-tkl-toolbar{display:flex;align-items:center;justify-content:center;position:relative;padding:2px 6px;height:18px;flex-shrink:0;background:var(--rime-tkl-toolbar-bg,rgba(24,24,28,.96));border-bottom:1px solid var(--rime-tkl-border,rgba(255,255,255,.06));cursor:grab;border-radius:var(--rime-tkl-radius,10px) var(--rime-tkl-radius,10px) 0 0}
.rime-tkl-toolbar:active{cursor:grabbing}
.rime-tkl-tb-drag{color:rgba(255,255,255,.18);font-size:10px;font-weight:700;line-height:1;letter-spacing:3px;pointer-events:none}
.rime-tkl-tb-hide{position:absolute;right:6px;top:50%;transform:translateY(-50%);background:transparent;border:none;color:var(--rime-tkl-toolbar-btn-color,rgba(255,255,255,.45));font-size:8px;cursor:pointer;padding:2px 4px;line-height:1;border-radius:3px;-webkit-tap-highlight-color:transparent}
.rime-tkl-tb-hide:hover{color:var(--rime-tkl-toolbar-btn-active-color,#70c0e8)}
.rime-tkl-keys{padding:4px 6px 4px;display:flex;flex-direction:column;gap:var(--rime-tkl-key-gap,2px);background:var(--rime-tkl-bg,rgba(30,30,34,.98));border-radius:0 0 var(--rime-tkl-radius,10px) var(--rime-tkl-radius,10px)}
.rime-tkl-row{display:flex;gap:var(--rime-tkl-key-gap,2px);align-items:stretch}
.rime-tkl-row-main{display:flex;flex:15;min-width:0}
.rime-tkl-row-nav{display:flex;flex:3;min-width:0}
.rime-tkl-key{display:flex;align-items:center;justify-content:center;flex:1;min-height:var(--rime-tkl-key-height,32px);background:var(--rime-tkl-key-bg,rgba(58,58,64,.92));color:var(--rime-tkl-key-color,rgba(255,255,255,.88));border:1px solid transparent;box-sizing:border-box;border-radius:calc(var(--rime-tkl-key-radius,4px) + 1px);font-size:var(--rime-tkl-key-font-size,12px);font-family:inherit;cursor:pointer;transition:transform .08s,background .08s;-webkit-tap-highlight-color:transparent;outline:none;padding:0;white-space:nowrap}
.rime-tkl-spacer{visibility:hidden;pointer-events:none;min-height:var(--rime-tkl-key-height,32px);border:1px solid transparent;box-sizing:border-box}
.rime-tkl-key-dual{position:relative;flex-direction:column;justify-content:flex-end;align-items:center;padding-bottom:3px}
.rime-tkl-sub{position:absolute;top:1px;right:3px;font-size:calc(var(--rime-tkl-key-font-size,12px) * 0.6);opacity:.4;line-height:1;pointer-events:none}
.rime-tkl-main{line-height:1;pointer-events:none}
.rime-tkl-key-dual-shift .rime-tkl-sub{opacity:.4}
.rime-tkl-key-dual-shift .rime-tkl-main{font-weight:600}
.rime-tkl-key:active,.rime-tkl-key-pressed{background:var(--rime-tkl-key-active-bg,rgba(112,192,232,.35));color:var(--rime-tkl-key-active-color,#fff);transform:scale(0.96)}
.rime-tkl-key-fn{background:var(--rime-tkl-fn-key-bg,rgba(48,48,54,.92));color:var(--rime-tkl-fn-key-color,rgba(255,255,255,.6));font-size:calc(var(--rime-tkl-key-font-size,12px) * 0.85)}
.rime-tkl-key-fn:active,.rime-tkl-key-fn.rime-tkl-key-pressed{background:var(--rime-tkl-key-active-bg,rgba(112,192,232,.35));color:var(--rime-tkl-key-active-color,#fff)}
.rime-tkl-key-mod{background:var(--rime-tkl-mod-key-bg,rgba(42,42,48,.92));color:var(--rime-tkl-mod-key-color,rgba(255,255,255,.6));font-size:calc(var(--rime-tkl-key-font-size,12px) * 0.8)}
.rime-tkl-key-mod:active,.rime-tkl-key-mod.rime-tkl-key-pressed{background:var(--rime-tkl-key-active-bg,rgba(112,192,232,.35));color:var(--rime-tkl-key-active-color,#fff)}
.rime-tkl-key-active{background:var(--rime-tkl-key-active-bg,rgba(112,192,232,.35))!important;color:var(--rime-tkl-key-active-color,#fff)!important}
.rime-tkl-key-space{background:var(--rime-tkl-space-bg,rgba(58,58,64,.92));color:var(--rime-tkl-space-color,rgba(255,255,255,.5));font-size:calc(var(--rime-tkl-key-font-size,12px) * 0.75);letter-spacing:2px}
.rime-tkl-preview{position:fixed;z-index:100000;display:flex;align-items:center;justify-content:center;min-width:36px;height:40px;padding:4px 10px;background:var(--rime-tkl-preview-bg,rgba(58,58,64,.96));color:var(--rime-tkl-preview-color,#fff);font-size:var(--rime-tkl-preview-font-size,18px);border-radius:var(--rime-tkl-preview-radius,4px);box-shadow:0 2px 8px rgba(0,0,0,.3);pointer-events:none}
`

/**
 * Shadow DOM 内样式表。
 *
 * 通过 :host 定义主题变量（--lp-*），宿主可通过以下方式覆盖：
 *   1. 构造时 opts.theme = 'light' | 'dark' | 'auto'
 *   2. 宿主 CSS 对 host 元素设 :host { --lp-bg: ... } 等
 *   3. setOption('theme', 'dark')
 *
 * 所有选择器在 Shadow DOM 内隔离，无需担心与宿主冲突。
 */
export const STYLES = `
:host {
  --lp-bg: #ffffff;
  --lp-fg: #333333;
  --lp-border: #d0d0d0;
  --lp-titlebar-bg: #f5f5f5;
  --lp-muted: #888888;
  --lp-accent: #1976d2;
  --lp-btn-bg: #f5f5f5;
  --lp-btn-hover: #e0e0e0;
  --lp-close-hover: #e53935;
  --lp-input-bg: #ffffff;
  --lp-input-border: #cccccc;
  --lp-error: #d32f2f;
  --lp-hover-bg: #eeeeee;
  --lp-font-ui: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --lp-font-mono: "Cascadia Code", "Fira Code", Consolas, "Courier New", monospace;
  --lp-scrollbar: #bbbbbb;
  --lp-log-bg: #fafafa;

  position: fixed;
  z-index: 25000;
  display: block;
  contain: layout style;
}

:host([data-theme="dark"]) {
  --lp-bg: #252525;
  --lp-fg: #e0e0e0;
  --lp-border: #3a3a3a;
  --lp-titlebar-bg: #2a2a2a;
  --lp-muted: #888888;
  --lp-accent: #42a5f5;
  --lp-btn-bg: #333333;
  --lp-btn-hover: #404040;
  --lp-close-hover: #ef5350;
  --lp-input-bg: #2a2a2a;
  --lp-input-border: #444444;
  --lp-error: #ef5350;
  --lp-hover-bg: #383838;
  --lp-scrollbar: #555555;
  --lp-log-bg: #1a1a1a;
}

:host([data-hidden="true"]) {
  display: none;
}

/* ── 视窗容器 ── */
.lp-root {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  background: var(--lp-bg);
  color: var(--lp-fg);
  border: 1px solid var(--lp-border);
  border-radius: 8px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
  overflow: hidden;
  font-family: var(--lp-font-ui);
  font-size: 12px;
}

/* ── 标题栏（拖拽区） ── */
.lp-titlebar {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 30px;
  padding: 0 8px 0 12px;
  background: var(--lp-titlebar-bg);
  border-bottom: 1px solid var(--lp-border);
  cursor: move;
  user-select: none;
  flex-shrink: 0;
}

.lp-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--lp-fg);
  display: flex;
  align-items: center;
  gap: 6px;
}

.lp-title svg {
  color: var(--lp-accent);
}

.lp-status {
  font-size: 11px;
  color: var(--lp-muted);
  font-variant-numeric: tabular-nums;
}

.lp-spacer { flex: 1; }

.lp-iconbtn {
  width: 22px;
  height: 22px;
  border: none;
  background: transparent;
  color: var(--lp-muted);
  cursor: pointer;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.1s, color 0.1s;
}

.lp-iconbtn:hover {
  background: var(--lp-btn-hover);
  color: var(--lp-fg);
}

.lp-iconbtn.lp-close:hover {
  background: var(--lp-close-hover);
  color: #fff;
}

/* ── 工具栏 ── */
.lp-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  background: var(--lp-titlebar-bg);
  border-bottom: 1px solid var(--lp-border);
  flex-wrap: wrap;
  flex-shrink: 0;
}

.lp-level-group {
  display: flex;
  gap: 4px;
}

.lp-chip {
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--lp-input-border);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.3px;
  cursor: pointer;
  background: var(--lp-input-bg);
  color: var(--lp-muted);
  user-select: none;
  transition: opacity 0.1s, border-color 0.1s;
  opacity: 0.45;
}

.lp-chip.active { opacity: 1; }
.lp-chip[data-level="0"].active { border-color: #888; color: #888; }
.lp-chip[data-level="1"].active { border-color: #08f; color: #08f; }
.lp-chip[data-level="2"].active { border-color: #f80; color: #f80; }
.lp-chip[data-level="3"].active { border-color: #f00; color: #f00; }

.lp-divider {
  width: 1px;
  height: 18px;
  background: var(--lp-border);
  margin: 0 2px;
}

/* 搜索框 */
.lp-search {
  flex: 1;
  min-width: 100px;
  display: flex;
  align-items: center;
  gap: 4px;
  background: var(--lp-input-bg);
  border: 1px solid var(--lp-input-border);
  border-radius: 4px;
  padding: 0 6px;
  height: 24px;
}

.lp-search input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: var(--lp-fg);
  font-size: 12px;
  outline: none;
  font-family: var(--lp-font-mono);
}

.lp-search.lp-search-invalid {
  border-color: var(--lp-error);
}

.lp-regex-btn {
  border: none;
  background: transparent;
  color: var(--lp-muted);
  cursor: pointer;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 4px;
  border-radius: 3px;
  font-family: var(--lp-font-mono);
}

.lp-regex-btn.active {
  background: var(--lp-accent);
  color: #fff;
}

/* 操作按钮组 */
.lp-actions {
  display: flex;
  gap: 4px;
}

.lp-btn {
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--lp-input-border);
  border-radius: 4px;
  background: var(--lp-btn-bg);
  color: var(--lp-fg);
  font-size: 11px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
  transition: background 0.1s;
  font-family: inherit;
}

.lp-btn:hover { background: var(--lp-btn-hover); }

.lp-btn svg { flex-shrink: 0; }

/* ── 主体：左栏 + 日志列表 ── */
.lp-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: row;
  background: var(--lp-log-bg);
}

/* 左栏 */
.lp-left {
  width: 150px;
  flex-shrink: 0;
  border-right: 1px solid var(--lp-border);
  padding: 8px 10px;
  overflow-y: auto;
  font-family: var(--lp-font-ui);
  font-size: 11px;
  color: var(--lp-fg);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.lp-left-section {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.lp-left-label {
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  color: var(--lp-muted);
  font-weight: 600;
  margin-bottom: 1px;
}

.lp-left-tag-toggle {
  font-size: 10px;
  color: var(--lp-accent);
  cursor: pointer;
  user-select: none;
  margin-bottom: 4px;
  font-weight: 600;
}

.lp-left-tag-toggle:hover { text-decoration: underline; }

.lp-left-tags {
  display: flex;
  flex-direction: column;
  gap: 1px;
  max-height: 200px;
  overflow-y: auto;
}

.lp-left-tag-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px;
  border-radius: 3px;
  cursor: pointer;
  font-size: 11px;
  font-family: var(--lp-font-mono);
  user-select: none;
  color: var(--lp-muted);
  transition: background 0.1s, color 0.1s;
}

.lp-left-tag-row:hover { background: var(--lp-hover-bg); }

.lp-left-tag-row.active {
  color: var(--lp-fg);
  font-weight: 500;
}

.lp-left-tag-dot {
  font-size: 8px;
  width: 14px;
  flex-shrink: 0;
}

.lp-left-tag-name {
  flex: 1;
  min-width: 0;
}

.lp-left-spacer {
  flex: 1;
  min-height: 8px;
}

/* 等级统计 */
.lp-left-level-row {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  line-height: 1.6;
}

.lp-left-dot {
  font-size: 8px;
  width: 14px;
  flex-shrink: 0;
}

.lp-left-lvl-name {
  color: var(--lp-fg);
  min-width: 40px;
}

.lp-left-lvl-count {
  margin-left: auto;
  font-weight: 600;
  font-family: var(--lp-font-mono);
  color: var(--lp-fg);
}

/* 缓冲进度条 */
.lp-left-buffer {
  font-size: 10px;
  color: var(--lp-muted);
  font-variant-numeric: tabular-nums;
  font-family: var(--lp-font-mono);
  margin-top: 4px;
}

.lp-left-bar {
  height: 4px;
  border-radius: 2px;
  background: var(--lp-border);
  overflow: hidden;
  margin-top: 2px;
}

.lp-left-bar-fill {
  height: 100%;
  border-radius: 2px;
  background: var(--lp-accent);
  transition: width 0.2s;
  width: 0%;
}

/* 右栏：日志列表 */
.lp-right {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  font-family: var(--lp-font-mono);
  font-size: 11.5px;
  line-height: 1.5;
}

.lp-empty {
  padding: 24px;
  text-align: center;
  color: var(--lp-muted);
  font-size: 12px;
  font-family: var(--lp-font-ui);
}

/* 滚动条 */
.lp-right::-webkit-scrollbar,
.lp-left::-webkit-scrollbar,
.lp-left-tags::-webkit-scrollbar { width: 8px; }

.lp-right::-webkit-scrollbar-track,
.lp-left::-webkit-scrollbar-track,
.lp-left-tags::-webkit-scrollbar-track { background: transparent; }

.lp-right::-webkit-scrollbar-thumb,
.lp-left::-webkit-scrollbar-thumb,
.lp-left-tags::-webkit-scrollbar-thumb {
  background: var(--lp-scrollbar);
  border-radius: 4px;
}

.lp-right, .lp-left, .lp-left-tags {
  scrollbar-color: var(--lp-scrollbar) transparent;
  scrollbar-width: thin;
}

/* 单行日志 */
.lp-log-line {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 6px;
  padding: 1px 8px 1px 4px;
  border-left: 3px solid transparent;
  word-break: break-word;
  white-space: pre-wrap;
}

.lp-log-line[data-level="0"] { border-left-color: #888; }
.lp-log-line[data-level="1"] { border-left-color: #08f; }
.lp-log-line[data-level="2"] { border-left-color: #f80; background: rgba(255,136,0,0.06); }
.lp-log-line[data-level="3"] { border-left-color: #f00; background: rgba(255,0,0,0.06); }

.lp-log-line:hover { background: var(--lp-hover-bg); }

.lp-ts {
  color: var(--lp-muted);
  flex-shrink: 0;
  opacity: 0.7;
}

.lp-lvl {
  flex-shrink: 0;
  font-weight: 700;
  min-width: 38px;
}

.lp-log-line[data-level="0"] .lp-lvl { color: #888; }
.lp-log-line[data-level="1"] .lp-lvl { color: #08f; }
.lp-log-line[data-level="2"] .lp-lvl { color: #f80; }
.lp-log-line[data-level="3"] .lp-lvl { color: #f00; }

.lp-tag {
  flex-shrink: 0;
  font-weight: 700;
}

.lp-text {
  flex: 1;
  min-width: 0;
  color: var(--lp-fg);
  white-space: pre-wrap;
}

/* 搜索高亮 */
.lp-text mark {
  background: #ffe066;
  color: inherit;
  border-radius: 2px;
  padding: 0 1px;
}

:host([data-theme="dark"]) .lp-text mark {
  background: #b8860b;
  color: #fff;
}

/* 展开图标 */
.lp-expand-icon {
  flex-shrink: 0;
  width: 14px;
  text-align: center;
  font-size: 8px;
  color: var(--lp-muted);
  user-select: none;
  line-height: inherit;
}

.lp-log-line.has-stack { cursor: pointer; }

/* 展开详情 */
.lp-line-detail {
  display: none;
  width: 100%;
  white-space: pre;
  font-size: 11px;
  line-height: 1.4;
  padding: 6px 8px 6px 20px;
  margin: 2px 0 -2px;
  background: var(--lp-titlebar-bg);
  border-radius: 3px;
  color: var(--lp-fg);
  overflow-x: auto;
  font-family: var(--lp-font-mono);
  max-height: 300px;
  overflow-y: auto;
}

.lp-log-line.expanded .lp-line-detail { display: block; }

/* 缩放手柄 */
.lp-resize {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 14px;
  height: 14px;
  cursor: nwse-resize;
}

.lp-resize::after {
  content: '';
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 6px;
  height: 6px;
  border-right: 2px solid var(--lp-muted);
  border-bottom: 2px solid var(--lp-muted);
}

.lp-root.dragging, .lp-root.resizing { user-select: none; }
.lp-root.dragging .lp-body, .lp-root.resizing .lp-body { pointer-events: none; }

/* ── toast（Shadow DOM 内自带） ── */
.lp-toast-container {
  position: absolute;
  bottom: 8px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  display: flex;
  flex-direction: column;
  gap: 4px;
  pointer-events: none;
}

.lp-toast {
  padding: 6px 14px;
  border-radius: 4px;
  font-size: 12px;
  font-family: var(--lp-font-ui);
  background: var(--lp-fg);
  color: var(--lp-bg);
  opacity: 0;
  transition: opacity 0.2s;
  box-shadow: 0 2px 8px rgba(0,0,0,0.2);
}

.lp-toast.show { opacity: 1; }
.lp-toast.success { background: #4caf50; color: #fff; }
.lp-toast.error { background: var(--lp-error); color: #fff; }

/* ── 右键菜单（Shadow DOM 内自实现） ── */
.lp-context-menu {
  position: fixed;
  z-index: 25001;
  background: var(--lp-bg);
  border: 1px solid var(--lp-border);
  border-radius: 4px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.2);
  padding: 4px 0;
  min-width: 120px;
  font-size: 12px;
}

.lp-context-menu-item {
  padding: 6px 14px;
  cursor: pointer;
  user-select: none;
  color: var(--lp-fg);
}

.lp-context-menu-item:hover { background: var(--lp-hover-bg); }
.lp-context-menu-item.danger { color: var(--lp-error); }

/* ── 触摸端适配 ── */
:host([data-touch="true"]) .lp-titlebar { height: 34px; }
:host([data-touch="true"]) .lp-iconbtn { width: 28px; height: 28px; }
:host([data-touch="true"]) .lp-btn { height: 30px; padding: 0 12px; font-size: 12px; }
:host([data-touch="true"]) .lp-log-line { padding-top: 3px; padding-bottom: 3px; font-size: 12px; }
:host([data-touch="true"]) .lp-resize { width: 20px; height: 20px; }
:host([data-touch="true"]) .lp-left { width: 170px; padding: 10px 12px; font-size: 12px; }
:host([data-touch="true"]) .lp-left-tag-row { padding: 5px 6px; font-size: 12px; }
:host([data-touch="true"]) .lp-left-level-row { font-size: 12px; line-height: 1.8; }
:host([data-touch="true"]) .lp-expand-icon { font-size: 10px; width: 18px; }
:host([data-touch="true"]) .lp-line-detail { font-size: 12px; padding: 8px 10px 8px 24px; max-height: 400px; }

/* 深色主题微调 */
:host([data-theme="dark"]) .lp-log-line[data-level="2"] { background: rgba(255,136,0,0.04); }
:host([data-theme="dark"]) .lp-log-line[data-level="3"] { background: rgba(255,0,0,0.04); }
:host([data-theme="dark"]) .lp-line-detail { background: #2a2a2a; }
`;

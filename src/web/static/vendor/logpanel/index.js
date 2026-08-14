/**
 * LogPanel 通用悬浮日志视窗插件入口。
 *
 * 用法：
 *   import { LogPanel } from './logpanel/index.js';
 *   const panel = new LogPanel({ source, rules, hooks, ... });
 *
 * 导出：
 *   LogPanel       — 视窗主类
 *   DEFAULT_RULES  — 默认解析规则（可参考/扩展后传入 opts.rules）
 *   resolveTagColor— tag 颜色解析工具
 */
export { LogPanel } from './LogPanel.js';
export { DEFAULT_RULES, resolveTagColor } from './defaultRules.js';

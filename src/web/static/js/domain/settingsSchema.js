/**
 * 领域层：设置项 Schema 定义
 *
 * 描述所有设置项的元数据：key / 类型 / 默认值 / 分类 / 是否启用 / 选项。
 * 该文件是纯数据，不依赖任何外层模块；表现层据此渲染表单，应用层据此读写。
 *
 * 默认值优先级：后端 /api/settings 返回值（web_user_choice.json > web.toml）> 此处 default。
 * 此处的 default 仅为前端离线回退值，运行时应以后端返回为准。
 */

import { t } from './i18n.js'

// 设置分类
export const SETTINGS_CATEGORIES = [
  {
    id: 'basic',
    label: t('settings.cat.basic'),
    icon: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6"/><path d="M8 4v4l2.5 2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    hint: t('settings.cat.basicHint'),
  },
  {
    id: 'ime',
    label: t('settings.cat.ime'),
    icon: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="12" height="10" rx="1.5"/><path d="M5 7h6M5 10h4" stroke-linecap="round"/></svg>',
    hint: t('settings.cat.imeHint'),
  },
  {
    id: 'remote',
    label: t('settings.cat.remote'),
    icon: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="12" height="8" rx="1"/><path d="M6 13h4M8 11v2" stroke-linecap="round"/></svg>',
    hint: t('settings.cat.remoteHint'),
  },
  {
    id: 'security',
    label: t('settings.cat.security'),
    icon: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 1.5l5 2v4.5c0 3-2 5.5-5 7-3-1.5-5-4-5-7v-4.5l5-2z"/></svg>',
    hint: t('settings.cat.securityHint'),
    future: true,
  },
  {
    id: 'developer',
    label: t('settings.cat.developer'),
    icon: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 4L2 8l3.5 4M10.5 4L14 8l-3.5 4"/></svg>',
    hint: t('settings.cat.developerHint'),
  },
];

// 设置项类型枚举
export const SETTING_TYPES = {
  PILLS: 'pills',       // 多选一 pill 组（如主题）
  TOGGLE: 'toggle',     // 开关
  SELECT: 'select',     // 下拉
  STEPPER: 'stepper',   // 步进器
  INPUT: 'input',       // 文本输入
  TEXTAREA: 'textarea', // 多行文本
  ACTION: 'action',     // 可点击操作（点击触发回调，如弹出对话框）
};

/**
 * 设置项 Schema 列表。
 * 每项字段：
 * - key:         唯一键（点号路径，如 'basic.theme'）
 * - category:    所属分类 id
 * - section:     分类内分组标题
 * - label:       显示名
 * - desc:        描述
 * - type:        SETTING_TYPES 之一
 * - default:     默认值
 * - enabled:     是否启用（false 时控件 disabled）
 * - options:     SELECT/PILLS 的可选项 [{value, label}]
 * - min/max/step: STEPPER 的范围与步长
 * - unit:        STEPPER 的单位显示
 * - placeholder: INPUT/TEXTAREA 的占位文本
 * - mobileOnly:  仅移动端显示
 */
export const SETTINGS_SCHEMA = [
  // ── 基本设置 ──
  {
    key: 'basic.theme',
    category: 'basic',
    section: t('settings.sec.appearance'),
    label: t('settings.theme'),
    desc: t('settings.desc.theme'),
    type: SETTING_TYPES.PILLS,
    default: 'dark',
    options: [
      { value: 'light', label: t('settings.opt.light') },
      { value: 'dark', label: t('settings.opt.dark') },
      { value: 'system', label: t('settings.opt.system') },
    ],
  },
  {
    key: 'basic.terminalFont',
    category: 'basic',
    section: t('settings.sec.appearance'),
    label: t('settings.terminalFont'),
    desc: t('settings.desc.terminalFont'),
    type: SETTING_TYPES.PILLS,
    default: 'default',
    options: [
      { value: 'default', label: t('common.default') },
      { value: 'maple-mono', label: 'Maple Mono' },
    ],
  },

  {
    key: 'rikka.enabled',
    category: 'basic',
    section: 'rikka',
    label: t('settings.rikkaLabel'),
    desc: '<a href="/vendor/rikkajs/LICENSE" target="_blank" style="color:var(--wt-accent)">LICENSE</a>',
    type: SETTING_TYPES.TOGGLE,
    default: true,
  },

  {
    key: 'basic.serverAddress',
    category: 'basic',
    section: t('settings.sec.connection'),
    label: t('settings.serverAddress'),
    desc: t('settings.desc.serverAddress'),
    type: SETTING_TYPES.ACTION,
    default: '',
    placeholder: t('settings.ph.serverAddr'),
  },

  // ── 输入法设置 ──
  {
    key: 'ime.enabled',
    category: 'ime',
    section: t('settings.sec.basic'),
    label: t('settings.imeEnabled'),
    desc: t('settings.desc.imeEnabled'),
    type: SETTING_TYPES.TOGGLE,
    default: true,
  },
  {
    key: 'ime.keyboardLayout',
    category: 'ime',
    section: t('settings.sec.basic'),
    label: t('settings.keyboardLayout'),
    desc: t('settings.desc.keyboardLayout'),
    type: SETTING_TYPES.SELECT,
    default: 'compact',
    mobileOnly: true,
    options: [
      { value: 'compact', label: t('settings.opt.compactKb') },
      { value: 'full', label: t('settings.opt.fullKb') },
    ],
  },
  {
    key: 'ime.candidateCount',
    category: 'ime',
    section: t('settings.sec.candidate'),
    label: t('settings.candidateCount'),
    desc: t('settings.desc.candidateCount'),
    type: SETTING_TYPES.STEPPER,
    default: 5,
    min: 3,
    max: 9,
    step: 1,
    unit: t('settings.unit.count'),
  },
  {
    key: 'ime.vertical',
    category: 'ime',
    section: t('settings.sec.candidate'),
    label: t('settings.verticalCandidates'),
    desc: t('settings.desc.verticalCandidates'),
    type: SETTING_TYPES.TOGGLE,
    default: false,
  },
  {
    key: 'ime.defaultState',
    category: 'ime',
    section: t('settings.sec.candidate'),
    label: t('settings.defaultState'),
    desc: t('settings.desc.defaultState'),
    type: SETTING_TYPES.SELECT,
    default: 'chinese',
    options: [
      { value: 'chinese', label: t('settings.opt.chinese') },
      { value: 'english', label: t('settings.opt.english') },
      { value: 'last', label: t('settings.opt.last') },
    ],
  },

  // ── 输入法外观 ──
  {
    key: 'ime.toolbarDisplay',
    category: 'ime',
    section: t('settings.sec.appearance'),
    label: t('settings.toolbarDisplay'),
    desc: t('settings.desc.toolbarDisplay'),
    type: SETTING_TYPES.SELECT,
    default: 'always',
    options: [
      { value: 'always', label: t('settings.opt.alwaysShow') },
      { value: 'desktop_only', label: t('settings.opt.desktopOnly') },
      { value: 'never', label: t('settings.opt.neverShow') },
    ],
  },
  {
    key: 'ime.tbOpacity',
    category: 'ime',
    section: t('settings.sec.appearance'),
    label: t('settings.tbOpacity'),
    desc: t('settings.desc.tbOpacity'),
    type: SETTING_TYPES.STEPPER,
    default: 100,
    min: 30,
    max: 100,
    step: 5,
    unit: '%',
  },
  {
    key: 'ime.kbOpacity',
    category: 'ime',
    section: t('settings.sec.appearance'),
    label: t('settings.kbOpacity'),
    desc: t('settings.desc.kbOpacity'),
    type: SETTING_TYPES.STEPPER,
    default: 100,
    min: 30,
    max: 100,
    step: 5,
    unit: '%',
    mobileOnly: true,
  },
  {
    key: 'ime.tbScale',
    category: 'ime',
    section: t('settings.sec.appearance'),
    label: t('settings.tbScale'),
    desc: t('settings.desc.tbScale'),
    type: SETTING_TYPES.SELECT,
    default: 1.0,
    options: [
      { value: 0.8, label: t('settings.opt.small', { val: '0.8x' }) },
      { value: 1.0, label: t('settings.opt.standard', { val: '1.0x' }) },
      { value: 1.2, label: t('settings.opt.large', { val: '1.2x' }) },
      { value: 1.5, label: t('settings.opt.xlarge', { val: '1.5x' }) },
    ],
  },
  {
    key: 'ime.kbScale',
    category: 'ime',
    section: t('settings.sec.appearance'),
    label: t('settings.kbScale'),
    desc: t('settings.desc.kbScale'),
    type: SETTING_TYPES.SELECT,
    default: 1.0,
    mobileOnly: true,
    options: [
      { value: 0.8, label: t('settings.opt.small', { val: '0.8x' }) },
      { value: 1.0, label: t('settings.opt.standard', { val: '1.0x' }) },
      { value: 1.2, label: t('settings.opt.large', { val: '1.2x' }) },
      { value: 1.5, label: t('settings.opt.xlarge', { val: '1.5x' }) },
    ],
  },
  {
    key: 'ime.panelOpacity',
    category: 'ime',
    section: t('settings.sec.appearance'),
    label: t('settings.panelOpacity'),
    desc: t('settings.desc.panelOpacity'),
    type: SETTING_TYPES.STEPPER,
    default: 100,
    min: 30,
    max: 100,
    step: 5,
    unit: '%',
  },
  {
    key: 'ime.panelScale',
    category: 'ime',
    section: t('settings.sec.appearance'),
    label: t('settings.panelScale'),
    desc: t('settings.desc.panelScale'),
    type: SETTING_TYPES.SELECT,
    default: 1.0,
    options: [
      { value: 0.8, label: t('settings.opt.small', { val: '0.8x' }) },
      { value: 1.0, label: t('settings.opt.standard', { val: '1.0x' }) },
      { value: 1.2, label: t('settings.opt.large', { val: '1.2x' }) },
      { value: 1.5, label: t('settings.opt.xlarge', { val: '1.5x' }) },
    ],
  },

  // ── 远程桌面连接 ──
  // VNC / FastScreen 启用状态属部署级配置（web.toml 的 ENABLE_VNC / ENABLE_FASTSCREEN），
  // 由守护进程启动时读取，前端不可修改，故不在此暴露。
  {
    key: 'remote.fsFps',
    category: 'remote',
    section: 'FastScreen',
    label: t('settings.fsFps'),
    desc: t('settings.desc.fsFps'),
    type: SETTING_TYPES.STEPPER,
    default: 30,
    min: 5,
    max: 30,
    step: 5,
    unit: 'fps',
  },
  {
    key: 'remote.fsBitrate',
    category: 'remote',
    section: 'FastScreen',
    label: t('settings.fsBitrate'),
    desc: t('settings.desc.fsBitrate'),
    type: SETTING_TYPES.SELECT,
    default: 2000000,
    options: [
      { value: 500000, label: t('settings.opt.low') },
      { value: 1000000, label: t('settings.opt.medium') },
      { value: 2000000, label: t('settings.opt.high') },
      { value: 4000000, label: t('settings.opt.veryHigh') },
    ],
  },
  {
    key: 'remote.fsStreamFormat',
    category: 'remote',
    section: 'FastScreen',
    label: t('settings.fsStreamFormat'),
    desc: t('settings.desc.fsStreamFormat'),
    type: SETTING_TYPES.SELECT,
    default: 'mse',
    options: [
      { value: 'auto', label: t('settings.opt.auto') },
      { value: 'mjpeg', label: 'MJPEG' },
      { value: 'mse', label: 'MSE' },
      { value: 'webcodecs', label: 'WebCodecs' },
    ],
  },
  {
    key: 'remote.cursorLocator',
    category: 'remote',
    section: t('settings.sec.cursor'),
    label: t('settings.cursorLocator'),
    desc: t('settings.desc.cursorLocator'),
    type: SETTING_TYPES.TOGGLE,
    default: false,
  },
  {
    key: 'remote.cursorLocatorOuterRadius',
    category: 'remote',
    section: t('settings.sec.cursor'),
    label: t('settings.cursorOuterRadius'),
    desc: t('settings.desc.cursorOuterRadius'),
    type: SETTING_TYPES.STEPPER,
    default: 16,
    min: 8,
    max: 48,
    step: 2,
    unit: t('settings.unit.px'),
  },
  {
    key: 'remote.cursorLocatorInnerRadius',
    category: 'remote',
    section: t('settings.sec.cursor'),
    label: t('settings.cursorInnerRadius'),
    desc: t('settings.desc.cursorInnerRadius'),
    type: SETTING_TYPES.STEPPER,
    default: 8,
    min: 4,
    max: 32,
    step: 2,
    unit: t('settings.unit.px'),
  },
  {
    key: 'remote.cursorLocatorAlpha',
    category: 'remote',
    section: t('settings.sec.cursor'),
    label: t('settings.cursorAlpha'),
    desc: t('settings.desc.cursorAlpha'),
    type: SETTING_TYPES.STEPPER,
    default: 90,
    min: 10,
    max: 255,
    step: 10,
  },

  // ── 安全设置（未来） ──
  {
    key: 'security.sandbox',
    category: 'security',
    section: t('settings.sec.execSandbox'),
    label: t('settings.sandbox'),
    desc: t('settings.desc.sandbox'),
    type: SETTING_TYPES.TOGGLE,
    default: false,
    enabled: false,
  },
  {
    key: 'security.approvalMode',
    category: 'security',
    section: t('settings.sec.approval'),
    label: t('settings.approvalMode'),
    desc: t('settings.desc.approvalMode'),
    type: SETTING_TYPES.SELECT,
    default: 'off',
    enabled: false,
    options: [
      { value: 'off', label: t('settings.opt.off') },
      { value: 'important', label: t('settings.opt.importantApproval') },
      { value: 'all', label: t('settings.opt.allApproval') },
    ],
  },
  {
    key: 'security.whitelist',
    category: 'security',
    section: t('settings.sec.approval'),
    label: t('settings.whitelist'),
    desc: t('settings.desc.whitelist'),
    type: SETTING_TYPES.TEXTAREA,
    default: '',
    enabled: false,
    placeholder: t('settings.ph.whitelist'),
  },
  {
    key: 'security.auditRetention',
    category: 'security',
    section: t('settings.sec.audit'),
    label: t('settings.auditRetention'),
    desc: t('settings.desc.auditRetention'),
    type: SETTING_TYPES.STEPPER,
    default: 30,
    min: 1,
    max: 365,
    step: 1,
    unit: t('settings.unit.day'),
    enabled: false,
  },

  // ── 开发人员模式 ──
  // 采集前端 logger 输出（debug/info/warn/error），在悬浮日志视窗中实时展示。
  // 视窗内部始终采集全部等级，不受控制台输出等级影响。
  {
    key: 'developer.logPanelEnabled',
    category: 'developer',
    section: t('settings.sec.logWindow'),
    label: t('settings.logPanelEnabled'),
    desc: t('settings.desc.logPanelEnabled'),
    type: SETTING_TYPES.TOGGLE,
    default: false,
  },
  {
    key: 'developer.logLevel',
    category: 'developer',
    section: t('settings.sec.logWindow'),
    label: t('settings.logLevel'),
    desc: t('settings.desc.logLevel'),
    type: SETTING_TYPES.SELECT,
    default: 'none',
    options: [
      { value: 'debug', label: t('settings.log.debug') },
      { value: 'info', label: t('settings.log.info') },
      { value: 'warn', label: t('settings.log.warn') },
      { value: 'error', label: t('settings.log.error') },
      { value: 'none', label: t('settings.log.none') },
    ],
  },
  {
    key: 'developer.bufferSize',
    category: 'developer',
    section: t('settings.sec.logWindow'),
    label: t('settings.bufferSize'),
    desc: t('settings.desc.bufferSize'),
    type: SETTING_TYPES.STEPPER,
    default: 1000,
    min: 200,
    max: 5000,
    step: 100,
    unit: t('settings.unit.entry'),
  },
  {
    key: 'developer.windowSize',
    category: 'developer',
    section: t('settings.sec.appearance'),
    label: t('settings.windowSize'),
    desc: t('settings.desc.windowSize'),
    type: SETTING_TYPES.SELECT,
    default: 'medium',
    options: [
      { value: 'small', label: t('settings.opt.small', { val: '480×320' }) },
      { value: 'medium', label: t('settings.opt.standard', { val: '560×360' }) },
      { value: 'large', label: t('settings.opt.large', { val: '720×480' }) },
      { value: 'xlarge', label: t('settings.opt.xlarge', { val: '960×600' }) },
    ],
  },
  {
    key: 'developer.windowOpacity',
    category: 'developer',
    section: t('settings.sec.appearance'),
    label: t('settings.windowOpacity'),
    desc: t('settings.desc.windowOpacity'),
    type: SETTING_TYPES.STEPPER,
    default: 100,
    min: 30,
    max: 100,
    step: 5,
    unit: '%',
  },
];

/**
 * 从 schema 生成默认值对象。
 * @returns {object} { key: default, ... }
 */
export function buildDefaults() {
  const out = {};
  for (const item of SETTINGS_SCHEMA) {
    out[item.key] = item.default;
  }
  return out;
}

/**
 * 按分类分组 schema。
 * @returns {object} { categoryId: [{ section, items: [...] }] }
 */
export function groupByCategory() {
  const out = {};
  for (const cat of SETTINGS_CATEGORIES) out[cat.id] = [];
  for (const item of SETTINGS_SCHEMA) {
    if (!out[item.category]) out[item.category] = [];
    out[item.category].push(item);
  }
  // 在每个分类内按 section 二次分组
  const grouped = {};
  for (const catId of Object.keys(out)) {
    const sections = {};
    for (const item of out[catId]) {
      if (!sections[item.section]) sections[item.section] = [];
      sections[item.section].push(item);
    }
    grouped[catId] = Object.keys(sections).map(name => ({ name, items: sections[name] }));
  }
  return grouped;
}

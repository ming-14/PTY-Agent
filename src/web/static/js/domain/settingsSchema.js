/**
 * 领域层：设置项 Schema 定义
 *
 * 描述所有设置项的元数据：key / 类型 / 默认值 / 分类 / 是否启用 / 选项。
 * 该文件是纯数据，不依赖任何外层模块；表现层据此渲染表单，应用层据此读写。
 *
 * 默认值优先级：后端 /api/settings 返回值（web_user_choice.json > web.toml）> 此处 default。
 * 此处的 default 仅为前端离线回退值，运行时应以后端返回为准。
 */

// 设置分类
export const SETTINGS_CATEGORIES = [
  {
    id: 'basic',
    label: '基本设置',
    icon: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6"/><path d="M8 4v4l2.5 2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    hint: '改动会立即生效',
  },
  {
    id: 'ime',
    label: '输入法设置',
    icon: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="12" height="10" rx="1.5"/><path d="M5 7h6M5 10h4" stroke-linecap="round"/></svg>',
    hint: '键盘方案与候选词行为',
  },
  {
    id: 'remote',
    label: '远程桌面连接',
    icon: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="12" height="8" rx="1"/><path d="M6 13h4M8 11v2" stroke-linecap="round"/></svg>',
    hint: 'VNC / FastScreen 连接参数',
  },
  {
    id: 'security',
    label: '安全设置',
    icon: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 1.5l5 2v4.5c0 3-2 5.5-5 7-3-1.5-5-4-5-7v-4.5l5-2z"/></svg>',
    hint: '开发中 · 暂未开放',
    future: true,
  },
  {
    id: 'developer',
    label: '开发人员模式',
    icon: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 4L2 8l3.5 4M10.5 4L14 8l-3.5 4"/></svg>',
    hint: '前端调试工具 · 日志视窗',
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
    section: '外观',
    label: '主题',
    desc: '选择界面配色方案',
    type: SETTING_TYPES.PILLS,
    default: 'dark',
    options: [
      { value: 'light', label: '浅色' },
      { value: 'dark', label: '深色' },
      { value: 'system', label: '跟随系统' },
    ],
  },
  {
    key: 'basic.terminalFont',
    category: 'basic',
    section: '外观',
    label: '终端字体',
    desc: '选择 Maple Mono 后会后台加载字体资源，加载完成后自动应用',
    type: SETTING_TYPES.PILLS,
    default: 'default',
    options: [
      { value: 'default', label: '默认' },
      { value: 'maple-mono', label: 'Maple Mono' },
    ],
  },

  {
    key: 'rikka.enabled',
    category: 'basic',
    section: 'rikka',
    label: '获取一只rikka',
    desc: '<a href="/static/vendor/rikkajs/LICENSE" target="_blank" style="color:var(--wt-accent)">LICENSE</a>',
    type: SETTING_TYPES.TOGGLE,
    default: true,
  },

  // ── 输入法设置 ──
  {
    key: 'ime.enabled',
    category: 'ime',
    section: '基础',
    label: '启用 Web RIME',
    desc: '关闭后回退到系统输入法',
    type: SETTING_TYPES.TOGGLE,
    default: true,
  },
  {
    key: 'ime.keyboardLayout',
    category: 'ime',
    section: '基础',
    label: '键盘方案',
    desc: '移动端键盘布局，仅影响触摸设备',
    type: SETTING_TYPES.SELECT,
    default: 'compact',
    mobileOnly: true,
    options: [
      { value: 'compact', label: '普通键盘' },
      { value: 'full', label: '全键盘' },
    ],
  },
  {
    key: 'ime.candidateCount',
    category: 'ime',
    section: '候选',
    label: '候选词数量',
    desc: '同时显示的候选词条数',
    type: SETTING_TYPES.STEPPER,
    default: 5,
    min: 3,
    max: 9,
    step: 1,
    unit: '个',
  },
  {
    key: 'ime.vertical',
    category: 'ime',
    section: '候选',
    label: '竖排候选',
    desc: '关闭时横排显示',
    type: SETTING_TYPES.TOGGLE,
    default: false,
  },
  {
    key: 'ime.defaultState',
    category: 'ime',
    section: '候选',
    label: '默认中英文状态',
    desc: '打开新会话时的初始状态',
    type: SETTING_TYPES.SELECT,
    default: 'chinese',
    options: [
      { value: 'chinese', label: '中文' },
      { value: 'english', label: '英文' },
      { value: 'last', label: '跟随上次' },
    ],
  },

  // ── 输入法外观 ──
  {
    key: 'ime.toolbarDisplay',
    category: 'ime',
    section: '外观',
    label: '工具栏显示',
    desc: '控制 RIME 工具栏的显示模式',
    type: SETTING_TYPES.SELECT,
    default: 'always',
    options: [
      { value: 'always', label: '始终显示' },
      { value: 'desktop_only', label: '仅电脑端显示' },
      { value: 'never', label: '始终不显示' },
    ],
  },
  {
    key: 'ime.tbOpacity',
    category: 'ime',
    section: '外观',
    label: '工具栏透明度',
    desc: '工具栏的不透明度',
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
    section: '外观',
    label: '键盘透明度',
    desc: '键盘的不透明度（仅移动端）',
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
    section: '外观',
    label: '工具栏大小',
    desc: '工具栏缩放比例',
    type: SETTING_TYPES.SELECT,
    default: 1.0,
    options: [
      { value: 0.8, label: '小（0.8x）' },
      { value: 1.0, label: '标准（1.0x）' },
      { value: 1.2, label: '大（1.2x）' },
      { value: 1.5, label: '超大（1.5x）' },
    ],
  },
  {
    key: 'ime.kbScale',
    category: 'ime',
    section: '外观',
    label: '键盘大小',
    desc: '键盘缩放比例（仅移动端）',
    type: SETTING_TYPES.SELECT,
    default: 1.0,
    mobileOnly: true,
    options: [
      { value: 0.8, label: '小（0.8x）' },
      { value: 1.0, label: '标准（1.0x）' },
      { value: 1.2, label: '大（1.2x）' },
      { value: 1.5, label: '超大（1.5x）' },
    ],
  },
  {
    key: 'ime.panelOpacity',
    category: 'ime',
    section: '外观',
    label: '候选词面板透明度',
    desc: '悬浮候选词面板的不透明度',
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
    section: '外观',
    label: '候选词面板大小',
    desc: '悬浮候选词面板的字号缩放比例',
    type: SETTING_TYPES.SELECT,
    default: 1.0,
    options: [
      { value: 0.8, label: '小（0.8x）' },
      { value: 1.0, label: '标准（1.0x）' },
      { value: 1.2, label: '大（1.2x）' },
      { value: 1.5, label: '超大（1.5x）' },
    ],
  },

  // ── 远程桌面连接 ──
  // VNC / FastScreen 启用状态属部署级配置（web.toml 的 ENABLE_VNC / ENABLE_FASTSCREEN），
  // 由守护进程启动时读取，前端不可修改，故不在此暴露。
  {
    key: 'remote.fsFps',
    category: 'remote',
    section: 'FastScreen',
    label: '默认帧率',
    desc: '捕获帧率，越高越流畅但 CPU 占用越高',
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
    label: '默认码率',
    desc: 'H264 编码码率',
    type: SETTING_TYPES.SELECT,
    default: 2000000,
    options: [
      { value: 500000, label: '低（0.5 Mbps）' },
      { value: 1000000, label: '中（1 Mbps）' },
      { value: 2000000, label: '高（2 Mbps）' },
      { value: 4000000, label: '极高（4 Mbps）' },
    ],
  },
  {
    key: 'remote.fsStreamFormat',
    category: 'remote',
    section: 'FastScreen',
    label: '传输协议',
    desc: '优先使用 MSE，失败时降级 MJPEG',
    type: SETTING_TYPES.SELECT,
    default: 'mse',
    options: [
      { value: 'auto', label: '自动' },
      { value: 'mjpeg', label: 'MJPEG' },
      { value: 'mse', label: 'MSE' },
      { value: 'webcodecs', label: 'WebCodecs' },
    ],
  },
  {
    key: 'remote.cursorLocator',
    category: 'remote',
    section: '光标增强',
    label: '增强鼠标显示',
    desc: '在鼠标位置显示高亮圆环，便于远程查看时定位光标',
    type: SETTING_TYPES.TOGGLE,
    default: false,
  },
  {
    key: 'remote.cursorLocatorOuterRadius',
    category: 'remote',
    section: '光标增强',
    label: '外圈半径',
    desc: '光标圆环外圈半径（像素）',
    type: SETTING_TYPES.STEPPER,
    default: 16,
    min: 8,
    max: 48,
    step: 2,
    unit: 'px',
  },
  {
    key: 'remote.cursorLocatorInnerRadius',
    category: 'remote',
    section: '光标增强',
    label: '内圈半径',
    desc: '光标圆环内圈半径（像素），中间透明区域',
    type: SETTING_TYPES.STEPPER,
    default: 8,
    min: 4,
    max: 32,
    step: 2,
    unit: 'px',
  },
  {
    key: 'remote.cursorLocatorAlpha',
    category: 'remote',
    section: '光标增强',
    label: '透明度',
    desc: '圆环整体透明度，0 为全透明，255 为不透明',
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
    section: '执行沙箱',
    label: '启用执行沙箱',
    desc: '在受限环境中运行子进程命令',
    type: SETTING_TYPES.TOGGLE,
    default: false,
    enabled: false,
  },
  {
    key: 'security.approvalMode',
    category: 'security',
    section: '审批',
    label: '审批模式',
    desc: '重要操作前需要用户审批',
    type: SETTING_TYPES.SELECT,
    default: 'off',
    enabled: false,
    options: [
      { value: 'off', label: '关闭' },
      { value: 'important', label: '重要操作审批' },
      { value: 'all', label: '全部审批' },
    ],
  },
  {
    key: 'security.whitelist',
    category: 'security',
    section: '审批',
    label: '命令白名单',
    desc: '每行一条命令前缀，命中白名单的命令免审批',
    type: SETTING_TYPES.TEXTAREA,
    default: '',
    enabled: false,
    placeholder: '如\ngit status\nls\npwd',
  },
  {
    key: 'security.auditRetention',
    category: 'security',
    section: '审计',
    label: '审计日志保留天数',
    desc: '超过天数的审计日志自动清理',
    type: SETTING_TYPES.STEPPER,
    default: 30,
    min: 1,
    max: 365,
    step: 1,
    unit: '天',
    enabled: false,
  },

  // ── 开发人员模式 ──
  // 采集前端 logger 输出（debug/info/warn/error），在悬浮日志视窗中实时展示。
  // 视窗内部始终采集全部等级，不受控制台输出等级影响。
  {
    key: 'developer.logPanelEnabled',
    category: 'developer',
    section: '日志视窗',
    label: '显示日志视窗',
    desc: '开启后弹出悬浮日志视窗，实时查看前端运行日志',
    type: SETTING_TYPES.TOGGLE,
    default: false,
  },
  {
    key: 'developer.logLevel',
    category: 'developer',
    section: '日志视窗',
    label: '控制台输出等级',
    desc: '打印到浏览器 console 的最低日志等级，关闭后日志仍可在视窗中查看',
    type: SETTING_TYPES.SELECT,
    default: 'none',
    options: [
      { value: 'debug', label: 'DEBUG（详细）' },
      { value: 'info', label: 'INFO（事件）' },
      { value: 'warn', label: 'WARN（警告）' },
      { value: 'error', label: 'ERROR（错误）' },
      { value: 'none', label: 'NONE（关闭）' },
    ],
  },
  {
    key: 'developer.bufferSize',
    category: 'developer',
    section: '日志视窗',
    label: '缓冲区容量',
    desc: '日志视窗保留的最大条数，超出自动丢弃最旧',
    type: SETTING_TYPES.STEPPER,
    default: 1000,
    min: 200,
    max: 5000,
    step: 100,
    unit: '条',
  },
  {
    key: 'developer.windowSize',
    category: 'developer',
    section: '外观',
    label: '视窗大小',
    desc: '悬浮日志视窗的默认尺寸',
    type: SETTING_TYPES.SELECT,
    default: 'medium',
    options: [
      { value: 'small', label: '小（480×320）' },
      { value: 'medium', label: '中（560×360）' },
      { value: 'large', label: '大（720×480）' },
      { value: 'xlarge', label: '超大（960×600）' },
    ],
  },
  {
    key: 'developer.windowOpacity',
    category: 'developer',
    section: '外观',
    label: '视窗透明度',
    desc: '悬浮日志视窗的不透明度',
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

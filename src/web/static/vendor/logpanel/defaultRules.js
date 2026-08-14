/**
 * 默认解析规则与 tag 配色。
 *
 * rules 是一组访问器函数，从任意 entry 提取视窗所需的元数据。
 * 默认访问器假设 entry 形如 { ts, tsStr, level, levelName, tag, text, stack }，
 * 与 PTY-Agent logger.js 输出结构一致。宿主 entry 结构不同时，覆盖对应访问器即可。
 *
 * tagColor 是内置默认配色表，可被 opts.rules.tagColor 覆盖/追加。
 * 未在表中出现的 tag 通过字符串哈希自动生成稳定的 HSL 颜色。
 */

// 内置默认 tag 配色（与常见模块名对应，可被 opts.rules.tagColor 覆盖/追加）
const DEFAULT_TAG_COLORS = {
  terminal: '#4CAF50',
  mouse:    '#FF9800',
  key:      '#2196F3',
  ws:       '#9C27B0',
  ui:       '#00BCD4',
  session:  '#FF5722',
  scroll:   '#795548',
  paste:    '#E91E63',
  cursor:   '#9CCC65',
  touch:    '#BA68C8',
  settings: '#FFD54F',
  app:      '#90A4AE',
  fs:       '#26C6DA',
  vnc:      '#7E57C2',
  console:  '#607D8B',
  default:  '#888888',
};

/**
 * 将字符串哈希为稳定的 HSL 颜色。
 * 同一 tag 每次得到相同颜色，不同 tag 尽量分散。
 */
function hashColor(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = (h * 31 + str.charCodeAt(i)) | 0;
  }
  const hue = Math.abs(h) % 360;
  return `hsl(${hue}, 65%, 55%)`;
}

/**
 * 默认访问器集合。每个访问器接收原始 entry，返回视窗所需字段。
 * 宿主可通过 opts.rules 覆盖任意一个。
 */
export const DEFAULT_RULES = {
  /** 等级 0=DEBUG 1=INFO 2=WARN 3=ERROR */
  level: (e) => e.level,

  /** 等级名 */
  levelName: (e) => e.levelName || ['DEBUG', 'INFO', 'WARN', 'ERROR'][e.level] || '?',

  /** 模块标签 */
  tag: (e) => e.tag || 'default',

  /** 日志正文 */
  text: (e) => e.text || '',

  /** 时间戳（毫秒） */
  ts: (e) => e.ts || 0,

  /** 时间戳展示字符串 */
  tsStr: (e) => e.tsStr || '',

  /** stack 字符串（可展开详情），无则返回 null */
  stack: (e) => (e.hasStack ? (e.stack || null) : (e.stack || null)),

  /** 是否可展开（有 stack 或等级 >= WARN） */
  isExpandable: (e) => !!e.stack || e.hasStack || e.level >= 2,

  /** tag 配色表（可被覆盖/追加） */
  tagColor: { ...DEFAULT_TAG_COLORS },
};

/**
 * 解析 tag 颜色：先查 tagColor 表，未命中再尝试驼峰变换，最后哈希着色。
 * @param {string} tag
 * @param {object} tagColorTable  rules.tagColor
 * @returns {string} CSS 颜色
 */
export function resolveTagColor(tag, tagColorTable) {
  if (tagColorTable[tag]) return tagColorTable[tag];
  // 尝试驼峰变换：some-tag → someTag
  const camel = tag.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  if (tagColorTable[camel]) return tagColorTable[camel];
  return hashColor(tag);
}

/**
 * 虚拟键盘布局定义
 *
 * 每个按键由 KeyDef 描述，布局由 KeyDef[][] (按行) 组成。
 * 三套布局：letters (QWERTY)、numbers (数字+常用符号)、symbols (扩展符号)。
 */

/** 按键动作类型 */
export type KeyAction =
  | 'shift'      // Shift 切换
  | 'backspace'  // 退格
  | 'enter'      // 回车
  | 'space'      // 空格
  | 'page'       // 切换键盘页 (letters/numbers/symbols)
  | 'lang'       // 中/英切换 (ascii_mode)
  | 'punct'      // 智能标点 (根据中/英文状态切换)

/** 单个按键定义 */
export interface KeyDef {
  key: string
  label: string
  width?: number
  shiftKey?: string
  shiftLabel?: string
  alt?: string[]
  action?: KeyAction
  page?: KeyboardPage
  cnLabel?: string
  cnKey?: string
}

/** 键盘页类型 */
export type KeyboardPage = 'letters' | 'numbers' | 'symbols'

/** 一行按键 */
export type KeyRow = KeyDef[]

/** 完整键盘布局 */
export type KeyboardLayout = KeyRow[]

// ─── QWERTY 字母布局 ───────────────────────────────────────────────

const LETTERS_LAYOUT: KeyboardLayout = [
  // 第一行: q w e r t y u i o p
  [
    { key: 'q', label: 'q', shiftKey: 'Q', shiftLabel: 'Q', alt: ['1'] },
    { key: 'w', label: 'w', shiftKey: 'W', shiftLabel: 'W', alt: ['2'] },
    { key: 'e', label: 'e', shiftKey: 'E', shiftLabel: 'E', alt: ['3'] },
    { key: 'r', label: 'r', shiftKey: 'R', shiftLabel: 'R', alt: ['4'] },
    { key: 't', label: 't', shiftKey: 'T', shiftLabel: 'T', alt: ['5'] },
    { key: 'y', label: 'y', shiftKey: 'Y', shiftLabel: 'Y', alt: ['6'] },
    { key: 'u', label: 'u', shiftKey: 'U', shiftLabel: 'U', alt: ['7'] },
    { key: 'i', label: 'i', shiftKey: 'I', shiftLabel: 'I', alt: ['8'] },
    { key: 'o', label: 'o', shiftKey: 'O', shiftLabel: 'O', alt: ['9'] },
    { key: 'p', label: 'p', shiftKey: 'P', shiftLabel: 'P', alt: ['0'] },
  ],
  // 第二行: a s d f g h j k l
  [
    { key: 'a', label: 'a', shiftKey: 'A', shiftLabel: 'A', alt: ['1'] },
    { key: 's', label: 's', shiftKey: 'S', shiftLabel: 'S', alt: ['2'] },
    { key: 'd', label: 'd', shiftKey: 'D', shiftLabel: 'D', alt: ['3'] },
    { key: 'f', label: 'f', shiftKey: 'F', shiftLabel: 'F', alt: ['4'] },
    { key: 'g', label: 'g', shiftKey: 'G', shiftLabel: 'G', alt: ['5'] },
    { key: 'h', label: 'h', shiftKey: 'H', shiftLabel: 'H', alt: ['6'] },
    { key: 'j', label: 'j', shiftKey: 'J', shiftLabel: 'J', alt: ['7'] },
    { key: 'k', label: 'k', shiftKey: 'K', shiftLabel: 'K', alt: ['8'] },
    { key: 'l', label: 'l', shiftKey: 'L', shiftLabel: 'L', alt: ['9'] },
  ],
  // 第三行: Shift z x c v b n m Backspace
  [
    { key: 'Shift', label: '⇧', action: 'shift', width: 1.5 },
    { key: 'z', label: 'z', shiftKey: 'Z', shiftLabel: 'Z' },
    { key: 'x', label: 'x', shiftKey: 'X', shiftLabel: 'X' },
    { key: 'c', label: 'c', shiftKey: 'C', shiftLabel: 'C' },
    { key: 'v', label: 'v', shiftKey: 'V', shiftLabel: 'V' },
    { key: 'b', label: 'b', shiftKey: 'B', shiftLabel: 'B' },
    { key: 'n', label: 'n', shiftKey: 'N', shiftLabel: 'N' },
    { key: 'm', label: 'm', shiftKey: 'M', shiftLabel: 'M' },
    { key: 'BackSpace', label: '⌫', action: 'backspace', width: 1.5 },
  ],
  // 第四行: ?123  中/En  空格  标点  回车
  [
    { key: '?123', label: '?123', action: 'page', page: 'numbers', width: 1.5 },
    { key: 'lang', label: '中', action: 'lang', width: 1.5 },
    { key: 'space', label: '空格', action: 'space', width: 4 },
    { key: 'punct', label: '。', action: 'punct' },
    { key: 'Return', label: '↵', action: 'enter', width: 1.5 },
  ],
]

// ─── 数字+常用符号布局 ─────────────────────────────────────────────

const NUMBERS_LAYOUT: KeyboardLayout = [
  // 第一行: 数字
  [
    { key: '1', label: '1', alt: ['!', '①'] },
    { key: '2', label: '2', alt: ['@', '②'] },
    { key: '3', label: '3', alt: ['#', '③'] },
    { key: '4', label: '4', alt: ['$', '④'] },
    { key: '5', label: '5', alt: ['%', '⑤'] },
    { key: '6', label: '6', alt: ['^', '⑥'] },
    { key: '7', label: '7', alt: ['&', '⑦'] },
    { key: '8', label: '8', alt: ['*', '⑧'] },
    { key: '9', label: '9', alt: ['(', '⑨'] },
    { key: '0', label: '0', alt: [')', '⑩'] },
  ],
  // 第二行: 常用符号
  [
    { key: '-', label: '-', alt: ['_'], cnLabel: '－' },
    { key: '/', label: '/', cnLabel: '／' },
    { key: ':', label: ':', alt: ['；'], cnLabel: '：' },
    { key: ';', label: ';', cnLabel: '；' },
    { key: '(', label: '(', cnLabel: '（' },
    { key: ')', label: ')', cnLabel: '）' },
    { key: '$', label: '$', alt: ['￥'] },
    { key: '&', label: '&' },
    { key: '@', label: '@' },
    { key: '"', label: '"', alt: ['\''], cnLabel: '＂' },
  ],
  // 第三行: 更多符号 + 退格
  [
    { key: '.', label: '.', alt: ['。', '…'], cnLabel: '。' },
    { key: ',', label: ',', alt: ['，'], cnLabel: '，' },
    { key: '?', label: '?', alt: ['？'], cnLabel: '？' },
    { key: '!', label: '!', alt: ['！'], cnLabel: '！' },
    { key: "'", label: "'", cnLabel: '＇' },
    { key: '"', label: '"', alt: ['「', '」'], cnLabel: '＂' },
    { key: '~', label: '~' },
    { key: '_', label: '_', cnLabel: '＿' },
    { key: 'BackSpace', label: '⌫', action: 'backspace', width: 1.5 },
  ],
  // 第四行: 功能行 (与字母页一致)
  [
    { key: 'ABC', label: 'ABC', action: 'page', page: 'letters', width: 1.5 },
    { key: 'lang', label: '中', action: 'lang', width: 1.5 },
    { key: 'space', label: '空格', action: 'space', width: 4 },
    { key: 'punct', label: '。', action: 'punct' },
    { key: 'Return', label: '↵', action: 'enter', width: 1.5 },
  ],
]

// ─── 扩展符号布局 ──────────────────────────────────────────────────

const SYMBOLS_LAYOUT: KeyboardLayout = [
  // 第一行: 方括号类 + 数学符号
  [
    { key: '[', label: '[' },
    { key: ']', label: ']' },
    { key: '{', label: '{' },
    { key: '}', label: '}' },
    { key: '#', label: '#' },
    { key: '%', label: '%' },
    { key: '^', label: '^' },
    { key: '*', label: '*' },
    { key: '+', label: '+' },
    { key: '=', label: '=' },
  ],
  // 第二行: 下划线 + 管道 + 特殊符号
  [
    { key: '_', label: '_' },
    { key: '\\', label: '\\' },
    { key: '|', label: '|' },
    { key: '~', label: '~' },
    { key: '<', label: '<' },
    { key: '>', label: '>' },
    { key: '$', label: '$' },
    { key: '€', label: '€' },
    { key: '£', label: '£' },
    { key: '•', label: '•' },
  ],
  // 第三行: 标点 + 退格
  [
    { key: '`', label: '`' },
    { key: '·', label: '·' },
    { key: '…', label: '…' },
    { key: '—', label: '—' },
    { key: '「', label: '「' },
    { key: '」', label: '」' },
    { key: '《', label: '《' },
    { key: '》', label: '》' },
    { key: 'BackSpace', label: '⌫', action: 'backspace', width: 1.5 },
  ],
  // 第四行: 功能行
  [
    { key: 'ABC', label: 'ABC', action: 'page', page: 'letters', width: 1.5 },
    { key: '#+=', label: '?123', action: 'page', page: 'numbers', width: 1.5 },
    { key: 'space', label: '空格', action: 'space', width: 4 },
    { key: 'punct', label: '。', action: 'punct' },
    { key: 'Return', label: '↵', action: 'enter', width: 1.5 },
  ],
]

// ─── 导出 ──────────────────────────────────────────────────────────

/** 获取指定页的键盘布局 */
export function getLayout(page: KeyboardPage): KeyboardLayout {
  switch (page) {
    case 'letters': return LETTERS_LAYOUT
    case 'numbers': return NUMBERS_LAYOUT
    case 'symbols': return SYMBOLS_LAYOUT
  }
}

/** 获取从当前页切换到其他页的按键 label */
export function getPageSwitchLabel(currentPage: KeyboardPage): string {
  switch (currentPage) {
    case 'letters': return '?123'
    case 'numbers': return '#+='
    case 'symbols': return 'ABC'
  }
}

/** 获取从当前页切换时的目标页 */
export function getPageSwitchTarget(currentPage: KeyboardPage): KeyboardPage {
  switch (currentPage) {
    case 'letters': return 'numbers'
    case 'numbers': return 'symbols'
    case 'symbols': return 'letters'
  }
}

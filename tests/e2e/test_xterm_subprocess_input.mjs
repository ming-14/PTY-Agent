/**
 * 前端 e2e：子进程模式（mode="subprocess"）网页输入路由
 *
 * 验证：子进程会话中按键必须发 {type:'input'}（字符直写 stdin），
 * 不得发 {type:'key'}（后端拒绝无终端编码）。mock 后端对 input 原样
 * 回显、对 key 回显 'KEY:' 前缀，据此断言路由。
 *
 * 前置：先启动 mock_backend.mjs
 * 用法: node tests/e2e/test_xterm_subprocess_input.mjs
 */
import { loadPlaywright, findChromium } from './browser_utils.mjs';

const { chromium } = await loadPlaywright();
const PORT = process.env.E2E_PORT || 8124;

const browser = await chromium.launch({
  headless: true,
  executablePath: findChromium(),
  args: ['--lang=zh-CN'],
});
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on('pageerror', err => console.log('[pageerror]', err.message));

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' | ' + detail : ''}`);
};

await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: 'load' });
await page.waitForFunction(() => {
  const s = document.getElementById('status-text');
  return s && s.textContent.includes('已连接');
}, { timeout: 15000 });

// 创建子进程模式会话（新建会话对话框选择 "子进程" 模式）
await page.evaluate(() => { document.getElementById('btn-new-tab').click(); });
await page.waitForTimeout(300);
await page.evaluate(() => {
  document.getElementById('form-id').value = 'sess-sub';
  document.getElementById('form-command').value = 'python -u -i';
  const modeSel = document.getElementById('form-mode');
  if (modeSel) modeSel.value = 'subprocess';
  document.getElementById('dialog-ok').click();
});
await page.waitForFunction(() => document.querySelectorAll('.term-instance.active').length > 0, { timeout: 10000 });
await page.waitForTimeout(800);

// 终端聚焦后键入字符
const termDiv = await page.evaluate(() => {
  const el = document.querySelector('.term-instance.active');
  if (el) el.focus();
  return !!el;
});
check('子进程会话终端已激活', termDiv);

// 键入 "abc" 三个字符（每键一个 input 帧）
await page.keyboard.press('a');
await page.keyboard.press('b');
await page.keyboard.press('c');
await page.waitForTimeout(800);

// mock 后端对 input 原样回显 → 终端应显示 abc（无 KEY: 前缀）
const screenText = await page.evaluate(() => {
  const el = document.querySelector('.term-instance.active .xterm-screen');
  if (!el) return '';
  // 读屏幕文本：canvas 无 DOM 文本，改读隐藏快照
  const snap = document.getElementById('terminal-snapshot');
  return snap ? snap.textContent : '';
});
const hasKeyPrefix = /KEY:/.test(screenText);
const hasAbc = screenText.includes('abc');
check('键入字符走 input（无 KEY: 前缀）', hasAbc && !hasKeyPrefix, `snapshot=${JSON.stringify(screenText)}`);

// Enter → 应发 \n（input 帧），mock 回显 \n → 终端换行
await page.keyboard.press('Enter');
await page.waitForTimeout(500);
const screenText2 = await page.evaluate(() => {
  const snap = document.getElementById('terminal-snapshot');
  return snap ? snap.textContent : '';
});
check('Enter 后终端有换行内容', screenText2.length > 0, JSON.stringify(screenText2));

const failed = results.filter(r => !r.ok);
console.log(`\n=== 子进程输入路由: ${results.length - failed.length}/${results.length} 通过 ===`);
await browser.close();
process.exit(failed.length ? 1 : 0);

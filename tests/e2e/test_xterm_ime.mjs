/**
 * IME 组合回归测试（xterm 组合视图钳制，防"终端被推左"回归）
 *
 * 背景：vendored xterm 5.x 的组合视图无 maxWidth 钳制，长预编辑把辅助
 * textarea 撑出终端右缘，Chromium 焦点滚动横向滚动 .terminal-container
 * （overflow:hidden），导致终端内容整体左移。6.0.0 已内置钳制
 * （maxWidth + overflow:hidden + direction:rtl + LTR 标记）。
 *
 * 本测试用 CDP Input.imeSetComposition 驱动真实 IME 组合路径，断言：
 * 1. 组合期间容器 scrollLeft 恒为 0（无横向滚动）
 * 2. .xterm-screen 位置不变（无位移）
 * 3. composition-view 右缘不超出终端 frame 右缘（钳制生效）
 *
 * 前置：先启动 mock_backend.mjs（服务真实 index.html + 假 WS 后端）
 * 用法: node tests/e2e/test_xterm_ime.mjs
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
check('app 启动并连接假后端', true);

// 创建会话 → 终端激活
await page.evaluate(() => { document.getElementById('btn-new-tab').click(); });
await page.waitForTimeout(300);
await page.evaluate(() => {
  document.getElementById('form-id').value = 'sess-ime';
  document.getElementById('form-command').value = 'cmd';
  document.getElementById('dialog-ok').click();
});
await page.waitForFunction(() => document.querySelectorAll('.term-instance.active').length > 0, { timeout: 10000 });
await page.waitForTimeout(500);
check('创建会话 → 终端实例激活', true);

const cdp = await page.context().newCDPSession(page);

async function imeSnap() {
  return page.evaluate(() => {
    const c = document.getElementById('terminal-container');
    const s = document.querySelector('.term-instance.active .xterm-screen');
    const cv = document.querySelector('.term-instance.active .composition-view');
    const cvr = cv.getBoundingClientRect();
    const fr = document.getElementById('terminal-frame').getBoundingClientRect();
    return {
      scrollLeft: c.scrollLeft,
      screenX: s.getBoundingClientRect().x,
      cvRight: cvr.right,
      cvMaxW: getComputedStyle(cv).maxWidth,
      frameRight: fr.right,
    };
  });
}

// 光标移到靠右位置 + 长预编辑（最坏情况）
await page.evaluate(() => {
  const ta = document.querySelector('.term-instance.active textarea');
  if (ta) ta.focus();
  window.__term = document.querySelector('.term-instance.active .xterm');
});
await page.waitForTimeout(200);
const before = await imeSnap();

await cdp.send('Input.imeSetComposition', { text: '你'.repeat(50), selectionStart: 50, selectionEnd: 50 });
await page.waitForTimeout(500);
const during = await imeSnap();

await cdp.send('Input.imeSetComposition', { text: '', selectionStart: 0, selectionEnd: 0 });
await page.waitForTimeout(400);
const after = await imeSnap();

check('组合期间无横向滚动（scrollLeft 恒 0）',
  before.scrollLeft === 0 && during.scrollLeft === 0 && after.scrollLeft === 0, JSON.stringify({ before, during, after }));
check('组合期间终端无位移（screenX 不变）',
  before.screenX === during.screenX && after.screenX === before.screenX, JSON.stringify({ before, during, after }));
check('组合视图钳制在终端右缘内（cvRight <= frameRight）',
  during.cvRight <= during.frameRight + 1.5, JSON.stringify(during));

const failed = results.filter(r => !r.ok);
console.log(`\n=== IME 回归: ${results.length - failed.length}/${results.length} 通过 ===`);
await browser.close();
process.exit(failed.length ? 1 : 0);

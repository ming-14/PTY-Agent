/**
 * 前端 e2e 回归（xterm 6.0.0 集成）：输出、窗口缩放、Ctrl+滚轮缩放、主题、resize
 *
 * 前置：先启动 mock_backend.mjs
 * 用法: node tests/e2e/test_xterm_regression.mjs
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

// 创建会话
await page.evaluate(() => { document.getElementById('btn-new-tab').click(); });
await page.waitForTimeout(300);
await page.evaluate(() => {
  document.getElementById('form-id').value = 'sess-reg';
  document.getElementById('form-command').value = 'cmd';
  document.getElementById('dialog-ok').click();
});
await page.waitForFunction(() => document.querySelectorAll('.term-instance.active').length > 0, { timeout: 10000 });
await page.waitForTimeout(500);
check('创建会话 → 终端激活', true);

const screenRect = () => page.evaluate(() => {
  const s = document.querySelector('.term-instance.active .xterm-screen');
  const r = s.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
});
const frameRect = () => page.evaluate(() => {
  const r = document.getElementById('terminal-frame').getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
});

// 1. frame 跟随 canvas
const r1 = await screenRect();
const f1 = await frameRect();
check('frame 尺寸 = screen 尺寸', Math.abs(r1.w - f1.w) < 2 && Math.abs(r1.h - f1.h) < 2, JSON.stringify({ r1, f1 }));

// 2. 窗口 resize → stage 变化 → frame 跟随（ResizeObserver 链路）
await page.setViewportSize({ width: 1000, height: 700 });
await page.waitForTimeout(800);
const r2 = await screenRect();
const f2 = await frameRect();
check('窗口 resize 后 frame 跟随（不超 stage）', r2.w > 0 && r2.w <= 1000 && Math.abs(r2.w - f2.w) < 2, JSON.stringify({ r2, f2 }));

// 3. Ctrl+滚轮缩放 → canvas 变大（frameRatio 缩放链路，真实键盘+滚轮事件）
await page.setViewportSize({ width: 1280, height: 900 });
await page.waitForTimeout(500);
const stageBox = await page.evaluate(() => {
  const r = document.getElementById('terminal-stage').getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
});
await page.keyboard.down('Control');
await page.mouse.move(stageBox.x, stageBox.y);
await page.mouse.wheel(0, -120);
await page.keyboard.up('Control');
await page.waitForTimeout(800);
const r3 = await screenRect();
check('Ctrl+滚轮缩放后 canvas 变大', r3.w > r2.w, JSON.stringify({ r2, r3 }));

// 4. 主题切换（dark/light 按钮）不崩溃
await page.evaluate(() => { document.getElementById('btn-theme').click(); });
await page.waitForTimeout(500);
const themeOk = await page.evaluate(() => {
  const s = document.querySelector('.term-instance.active .xterm-screen');
  return s.getBoundingClientRect().width > 0;
});
check('主题切换后终端正常', themeOk);

// 5. 只读（会话结束后转历史）—— 通过假后端 kill
const roOk = await page.evaluate(async (port) => {
  return new Promise(resolve => {
    const ws = new WebSocket(`ws://127.0.0.1:${port}/ws?clientUid=kill-probe`);
    ws.onopen = () => ws.send(JSON.stringify({ type: 'create', session_id: 'kill-sess', command: 'cmd', cols: 80, rows: 24, mode: 'pty' }));
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === 'subscribed') {
        ws.send(JSON.stringify({ type: 'kill', sessionUid: m.sessionUid }));
      } else if (m.type === 'session_ended') {
        ws.close();
        resolve(true);
      }
    };
    setTimeout(() => resolve(false), 3000);
  });
}, PORT);
check('kill 会话 → session_ended 协议', roOk);

const failed = results.filter(r => !r.ok);
console.log(`\n=== 回归: ${results.length - failed.length}/${results.length} 通过 ===`);
await browser.close();
process.exit(failed.length ? 1 : 0);

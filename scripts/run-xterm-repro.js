// 用 Edge CDP 加载 xterm resize 复现页并读取结果（live 测试运行器）
// 用法: node scripts/run-xterm-repro.js
// 前置: Edge 在标准路径；页面 live-xterm-resize.html 在 scripts/ 下；
//       可选 scripts/live-resize-data.json（live-collect-resize-data.py 产物）
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');
const fs = require('fs');

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const PORT = 9233;
const htmlPath = path.resolve(__dirname, 'live-xterm-resize.html').replace(/\\/g, '/');
const url = 'file:///' + htmlPath + '?d=' + Date.now();

function getJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
      });
    }).on('error', reject);
  });
}

async function waitFor(listUrl, timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const list = await getJson(listUrl);
      if (list.length > 0) return list;
    } catch (e) {}
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error('CDP not ready');
}

async function main() {
  // 注入模式：读取 live-collect-resize-data.py 收集的 daemon 数据
  let injected = null;
  try {
    const dataPath = path.resolve(__dirname, 'live-resize-data.json');
    injected = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
  } catch (e) {
    console.log('[run-xterm-repro] 未找到 live-resize-data.json（先运行 live-collect-resize-data.py），使用自包含模式');
  }

  const edge = spawn(EDGE, [
    `--remote-debugging-port=${PORT}`,
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--user-data-dir=' + path.join(require('os').tmpdir(), 'edge-repro-' + PORT),
    'about:blank',
  ], { stdio: 'ignore' });

  try {
    const list = await waitFor(`http://127.0.0.1:${PORT}/json/list`, 15000);
    const page = list.find((p) => p.type === 'page');
    if (!page) throw new Error('no page target');

    const ws = new WebSocket(page.webSocketDebuggerUrl);
    let msgId = 0;
    const pending = new Map();
    const send = (method, params = {}) =>
      new Promise((resolve, reject) => {
        const id = ++msgId;
        pending.set(id, { resolve, reject });
        ws.send(JSON.stringify({ id, method, params }));
      });
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) {
        const p = pending.get(m.id);
        pending.delete(m.id);
        if (m.error) p.reject(new Error(m.error.message));
        else p.resolve(m.result);
      }
    };
    await new Promise((r) => (ws.onopen = r));

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Page.navigate', { url });
    await new Promise((r) => setTimeout(r, 6000));

    // 注入收集的数据（有则用真实数据渲染 rebuild 流程）
    if (injected) {
      await send('Runtime.evaluate', {
        expression: `window.__DATA = ${JSON.stringify(injected)}; window.start && window.start();`,
        returnByValue: true,
      });
      await new Promise((r) => setTimeout(r, 10000));
    }

    const res = await send('Runtime.evaluate', {
      expression: 'document.getElementById("result") ? document.getElementById("result").textContent : "(no result)"',
      returnByValue: true,
    });
    console.log(res.result ? res.result.value : 'NO BODY CONTENT');
    ws.close();
  } finally {
    edge.kill();
  }
}

main().catch((e) => { console.error('ERROR:', e.message); process.exit(1); });

/**
 * 前端 e2e 测试基础设施：静态资源服务器 + 假 WS 后端
 *
 * 服务 PTY-Agent/src/web/static 全部资源（真实 index.html/app.js），
 * WS /ws 实现前端所需的最小协议（list/history/create/subscribe/resize/input），
 * 供 Playwright 驱动完整前端流程（无需启动真实 daemon）。
 *
 * 用法: node tests/e2e/mock_backend.mjs   （默认端口 8124）
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocketServer } from 'ws';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const STATIC = path.join(PROJECT_ROOT, 'src', 'web', 'static');
const PORT = Number(process.env.E2E_PORT || 8124);

const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.wasm': 'application/wasm', '.bin': 'application/octet-stream', '.yaml': 'text/plain',
  '.ttf': 'font/ttf', '.woff': 'font/woff', '.mp3': 'audio/mpeg', '.oga': 'audio/ogg',
  '.map': 'application/json', '.txt': 'text/plain', '.ico': 'image/x-icon',
};

const server = http.createServer((req, res) => {
  // API 端点（假后端最小实现）
  if (req.url.startsWith('/api/auth/status')) {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ enabled: false, authenticated: true }));
    return;
  }
  if (req.url.startsWith('/api/settings')) {
    if (req.method === 'POST') { res.writeHead(200); res.end('{}'); return; }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({}));
    return;
  }
  const url = decodeURIComponent(req.url.split('?')[0]);
  let rel = url;
  if (rel === '/' || rel.endsWith('/')) rel += 'index.html';
  const file = path.join(STATIC, rel);
  if (fs.existsSync(file) && fs.statSync(file).isFile()) {
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' });
    res.end(fs.readFileSync(file));
    return;
  }
  res.writeHead(404);
  res.end('not found: ' + url);
});

// ── 假 WS 后端：最小协议实现 ──
const sessions = new Map(); // uid -> {sid, cols, rows, running}
let uidCounter = 0;

const wss = new WebSocketServer({ server, path: '/ws' });
wss.on('connection', (ws) => {
  console.log('[ws] connected');
  ws.on('message', (raw) => {
    let msg;
    try { msg = JSON.parse(raw.toString()); } catch { return; }
    const t = msg.type || '';
    switch (t) {
      case 'list':
        ws.send(JSON.stringify({
          type: 'session_list',
          sessions: Array.from(sessions.values()).map(s => ({
            uid: s.uid, id: s.sid, command: s.command, running: s.running,
            startTime: s.startTime, ptyType: s.mode === 'subprocess' ? 'subprocess' : 'conpty',
            mode: s.mode,
          })),
        }));
        break;
      case 'history':
        ws.send(JSON.stringify({ type: 'history_list', sessions: [] }));
        break;
      case 'shells':
        ws.send(JSON.stringify({ type: 'shell_list', shells: { powershell: 'PowerShell', cmd: 'CMD' } }));
        break;
      case 'system_stats':
        ws.send(JSON.stringify({ type: 'system_stats', cpu: 5, memory: 40 }));
        break;
      case 'create': {
        const sid = msg.session_id || 'sess-' + Date.now();
        const uid = 'uid-' + (++uidCounter);
        const cols = msg.cols || 80;
        const rows = msg.rows || 24;
        const mode = msg.mode === 'subprocess' ? 'subprocess' : 'pty';
        sessions.set(uid, { uid, sid, command: msg.command || '', cols, rows, mode, running: true, startTime: Date.now() / 1000 });
        ws.send(JSON.stringify({
          type: 'subscribed',
          sessionId: sid,
          sessionUid: uid,
          replay: mode === 'subprocess' ? '' : 'C:\\> 欢迎使用假后端\r\n',
          scrollback: '',
          ptyType: mode === 'subprocess' ? 'subprocess' : 'conpty',
          mode,
          cols, rows,
          running: true,
          exitCode: null,
          errorMessage: null,
          encoding: 'utf-8',
          startTime: Date.now() / 1000,
          appMouseMode: false,
          adaptiveOwnerActive: false,
        }));
        console.log('[ws] created', sid, uid, cols + 'x' + rows, mode);
        break;
      }
      case 'subscribe': {
        const uid = msg.sessionUid || msg.session_id;
        const s = sessions.get(uid) || Array.from(sessions.values()).find(x => x.sid === uid);
        if (s) {
          ws.send(JSON.stringify({
            type: 'subscribed',
            sessionId: s.sid,
            sessionUid: s.uid,
            replay: s.mode === 'subprocess' ? '' : 'C:\\> ',
            scrollback: '',
            ptyType: s.mode === 'subprocess' ? 'subprocess' : 'conpty',
            mode: s.mode || 'pty',
            cols: s.cols, rows: s.rows,
            running: true,
            exitCode: null,
            errorMessage: null,
            encoding: 'utf-8',
            startTime: s.startTime,
            appMouseMode: false,
            adaptiveOwnerActive: false,
          }));
        } else {
          ws.send(JSON.stringify({ type: 'error', message: `session '${uid}' not found` }));
        }
        break;
      }
      case 'resize': {
        const uid = msg.sessionUid || msg.session_id;
        const s = sessions.get(uid);
        if (s) {
          s.cols = msg.cols; s.rows = msg.rows;
          ws.send(JSON.stringify({
            type: 'resize_complete',
            sessionId: s.sid,
            sessionUid: s.uid,
            cols: msg.cols, rows: msg.rows,
            snapshot: 'C:\\> ',
            scrollback: '',
          }));
          console.log('[ws] resize', s.sid, msg.cols + 'x' + msg.rows);
        }
        break;
      }
      case 'input': {
        const uid = msg.sessionUid || msg.session_id;
        const s = sessions.get(uid);
        if (s && msg.data) {
          // 简单回显（区分 input 与 key：input 原样回显，key 加 KEY: 前缀）
          ws.send(JSON.stringify({ type: 'output', sessionId: s.sid, sessionUid: s.uid, data: msg.data, stream: 'stdout', encoding: 'utf-8' }));
        }
        break;
      }
      case 'key': {
        const uid = msg.sessionUid || msg.session_id;
        const s = sessions.get(uid);
        if (s && msg.key) {
          // 子进程模式前端不应发 key（无终端编码）：回显 KEY: 标记用于断言
          ws.send(JSON.stringify({ type: 'output', sessionId: s.sid, sessionUid: s.uid, data: 'KEY:' + msg.key, stream: 'stdout', encoding: 'utf-8' }));
        }
        break;
      }
      case 'session_detail':
      case 'history_detail': {
        const uid = msg.sessionUid || msg.session_id;
        const s = sessions.get(uid);
        if (s) {
          ws.send(JSON.stringify({
            type: t === 'history_detail' ? 'history_detail' : 'session_detail',
            sessionId: s.sid,
            sessionUid: s.uid,
            command: s.command || '',
            cols: s.cols, rows: s.rows,
            running: false,
            startTime: s.startTime,
            endTime: Date.now() / 1000,
            exitCode: 0,
            errorMessage: null,
            ptyType: 'conpty',
            encoding: 'utf-8',
            replay: 'C:\\> 历史会话回放\r\n',
            snapshot: 'C:\\> 历史会话回放\r\n',
          }));
        }
        break;
      }
      case 'kill':
      case 'remove': {
        const uid = msg.sessionUid || msg.session_id;
        const s = sessions.get(uid);
        if (s) {
          s.running = false;
          ws.send(JSON.stringify({ type: 'session_ended', sessionId: s.sid, sessionUid: s.uid, exitCode: 0, errorMessage: null }));
        }
        break;
      }
      default:
        // 其它消息静默（vnc/fs/cursor_locator 等不模拟）
        break;
    }
  });
  ws.on('close', () => console.log('[ws] closed'));
});

server.listen(PORT, () => console.log(`mock backend on http://127.0.0.1:${PORT} (ws /ws)`));

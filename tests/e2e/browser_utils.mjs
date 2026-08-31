/**
 * e2e 浏览器工具：Playwright 加载（项目内依赖优先，回退全局安装）+ 浏览器探测
 */
import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';
import fs from 'node:fs';

// 优先项目内 playwright，回退全局 npm 安装
export async function loadPlaywright() {
  try {
    return await import('playwright');
  } catch (_) {}
  const require = createRequire(import.meta.url);
  try {
    const root = execSync('npm root -g', { encoding: 'utf8' }).trim();
    const candidates = [
      require.resolve('playwright', { paths: [root] }),
      require.resolve('@playwright/cli/node_modules/playwright', { paths: [root] }),
    ];
    for (const p of candidates) {
      try {
        const mod = await import('file://' + p.replace(/\\/g, '/').replace(/index\.js$/, 'index.mjs'));
        return mod;
      } catch (_) {}
    }
  } catch (_) {}
  throw new Error('playwright 未安装：npm install 或全局安装 @playwright/cli');
}

// 探测本机 Chromium 系浏览器（Edge/Chrome），返回 executablePath
export function findChromium() {
  const env = process.env.E2E_BROWSER;
  if (env && fs.existsSync(env)) return env;
  const candidates = [
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    process.env.LOCALAPPDATA + '/Google/Chrome/Application/chrome.exe',
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return undefined; // 使用 Playwright 自带浏览器
}

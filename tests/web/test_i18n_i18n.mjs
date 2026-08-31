/**
 * 前端 i18n 逻辑验证（无 DOM 环境）
 *
 * 验证：
 * 1. zh/en 字典 key 数量一致且无缺失/重复
 * 2. t() 插值正确（{placeholder} 替换）
 * 3. i18nError() 按 code+params 映射
 * 4. settingsSchema 通过 t() 生成的分类/设置项文案非 key 泄漏
 *
 * 运行：node tests/web/test_i18n_i18n.mjs
 */

import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Node <21 无全局 navigator（浏览器 API）；i18n.js 语言检测依赖它，
// 测试环境补一个最小 shim（Node 21+ 自带 navigator.language="en-US"）
if (typeof globalThis.navigator === 'undefined') {
  globalThis.navigator = { language: 'en-US', userLanguage: undefined };
}

const i18nUrl = pathToFileURL(join(__dirname, '../../src/web/static/js/domain/i18n.js')).href;
const schemaUrl = pathToFileURL(join(__dirname, '../../src/web/static/js/domain/settingsSchema.js')).href;

const failures = [];
function check(cond, msg) {
  if (!cond) failures.push(msg);
}

// 直接读源码校验字典
const i18nSrc = readFileSync(new URL(i18nUrl), 'utf8');
function extractKeys(block) {
  const start = i18nSrc.indexOf('  ' + block + ': {');
  const end = i18nSrc.indexOf('\n  },', start);
  const body = i18nSrc.slice(start, end);
  return new Set([...body.matchAll(/'([^']+)':/g)].map(m => m[1]));
}
const zh = extractKeys('zh');
const en = extractKeys('en');
check(zh.size === en.size, `zh/en key 数量不一致: ${zh.size} vs ${en.size}`);
for (const k of zh) check(en.has(k), `zh 缺 en: ${k}`);
for (const k of en) check(zh.has(k), `en 缺 zh: ${k}`);

// t() 插值验证（zh 环境：Node navigator.language 通常为 en，强制检查中文文案存在即可）
const i18n = await import(i18nUrl);
check(i18n.t('common.cancel') !== 'common.cancel', 't() 未取到字典');
check(i18n.t('detail.title', { sid: 's1' }).includes('s1'), 't() 插值失败');

// i18nError 映射
check(i18n.i18nError({ code: 'vnc.start_failed', params: { error: 'boom' } }) !== '', 'i18nError 未映射 code');
check(i18n.i18nError({ message: 'legacy msg' }).includes('legacy'), 'i18nError message 透传失败');

// settingsSchema：文案非 key 泄漏、中文文案已生成
const schema = await import(schemaUrl);
const catLabels = schema.SETTINGS_CATEGORIES.map(c => c.label).join('|');
check(!catLabels.includes('settings.cat.'), '分类 label 泄漏 key');
const firstItem = schema.SETTINGS_SCHEMA[0];
check(firstItem.label && !firstItem.label.startsWith('settings.'), '设置项 label 泄漏 key');

// 静态 HTML 中 data-i18n* 引用的 key 必须都在字典中（避免渲染时回退 key 泄露）
const projectRoot = join(__dirname, '..', '..');
for (const hf of ['src/web/static/index.html', 'src/web/static/login.html']) {
  const html = readFileSync(join(projectRoot, hf), 'utf8');
  for (const m of html.matchAll(/data-i18n(?:-title|-placeholder)?="([^"]+)"/g)) {
    check(zh.has(m[1]) && en.has(m[1]), `HTML 引用缺失 key: ${m[1]} in ${hf}`);
  }
}

// 汇总
if (failures.length) {
  console.error('FAILURES:');
  failures.forEach(f => console.error('  - ' + f));
  process.exit(1);
}
console.log('PASS: frontend i18n basic checks ok (zh keys=' + zh.size + ', en keys=' + en.size + ')');
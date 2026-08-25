// 审计 v2：所有文件的所有具名 import，验证每个导入名都被使用（排除 side-effect import）
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', 'src', 'web', 'static', 'js');
const EXCLUDE = /vendor|logpanel|novnc|xterm|rime/;

function walk(dir) {
  const out = [];
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, ent.name);
    if (ent.isDirectory()) walk(full).forEach(f => out.push(f));
    else if (ent.name.endsWith('.js') && !EXCLUDE.test(full)) out.push(full);
  }
  return out;
}

let problems = 0;
for (const file of walk(ROOT)) {
  const src = fs.readFileSync(file, 'utf8');
  const rel = path.relative(ROOT, file).replace(/\\/g, '/');
  for (const m of src.matchAll(/import \{([^}]+)\} from '([^']+)'/g)) {
    const imported = m[1].split(',').map(s => s.trim()).filter(Boolean);
    const rest = src.replace(m[0], '');
    for (const name of imported) {
      // 别名导入（as）跳过（形如 A as B）
      const bare = name.split(/\s+as\s+/)[0].trim();
      // 转义正则特殊字符（$ 等），用"前后非单词字符"界定标识符
      //（\b 对 $ 无效——$ 本身非单词字符，$('x') 的 \b 不成立）
      const escaped = bare.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const re = new RegExp('(^|[^\\w])' + escaped + '($|[^\\w])');
      if (!re.test(rest)) {
        console.log(`[未使用导入] ${rel}: ${name} (from ${m[2]})`);
        problems++;
      }
    }
  }
}
console.log(`审计完成，共 ${problems} 个问题`);

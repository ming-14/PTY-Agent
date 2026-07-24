import type { IBackend } from './base'
import type { RimeResult, RimeState, Composition, Candidate } from '../types'

const RIME_STATE_MAP: Record<number, RimeState> = {
  0: 'committed', 1: 'accepted', 2: 'rejected', 3: 'unhandled'
}

function toRimeResult(msg: any): RimeResult {
  return {
    state: RIME_STATE_MAP[msg.state] ?? 'unhandled',
    composition: { head: msg.head ?? '', body: msg.body ?? '', tail: msg.tail ?? '' },
    candidates: msg.candidates ?? [],
    committed: msg.committed ?? '',
    page: msg.page ?? 1,
    isLastPage: msg.isLastPage ?? true,
    highlighted: msg.highlighted ?? 0,
    selectLabels: msg.selectLabels ?? [],
    updatedOptions: msg.updatedOptions ?? {},
    updatedSchema: msg.updatedSchema ?? ''
  }
}

export class WasmBackend implements IBackend {
  private wasmUrl: string
  private worker: Worker | null = null
  private msgId = 0
  private pending = new Map<number, {
    resolve: (result: RimeResult | void) => void
    reject: (error: Error) => void
  }>()
  private destroyed = false

  constructor(wasmUrl: string) { this.wasmUrl = wasmUrl }

  private createWorker(): Worker {
    const workerCode = `
      var Module;
      let ready = false;

      // rime.js 内部通过 emscripten_asm_const_int 调用 _deployStatus(status, message)
      // 必须在 Worker 全局定义，否则 deploy 时会 ReferenceError
      function _deployStatus(status, message) {
        self.postMessage({ type: 'deployStatus', status: status, message: message });
      }

      // ─── 字典加载（镜像 server/src/dict-manager.ts 的逻辑） ───
      // rime.data 只含配置文件（opencc/default.yaml/lua），真正的词库（.table.bin/.prism.bin
      // 等）需要单独下载并写入 /usr/share/rime-data/build/ 后再 set_ime/deploy
      var SCHEMA_TARGETS = { luna_pinyin: 'luna-pinyin', stroke: 'stroke' };
      var SCHEMA_NAMES = { luna_pinyin: '朙月拼音', stroke: '五笔画' };
      var SCHEMA_DEPENDENCIES = { luna_pinyin: ['stroke'], stroke: [] };
      // 每个 schema target 目录下需要加载的文件（与 dict/{target}/ 实际文件一致）
      var SCHEMA_FILES = {
        'luna-pinyin': [
          'luna_pinyin.prism.bin',
          'luna_pinyin.reverse.bin',
          'luna_pinyin.schema.yaml',
          'luna_pinyin.table.bin',
          'luna_pinyin_fluency.schema.yaml',
          'luna_quanpin.prism.bin',
          'luna_quanpin.schema.yaml'
        ],
        'stroke': [
          'stroke.prism.bin',
          'stroke.reverse.bin',
          'stroke.schema.yaml',
          'stroke.table.bin'
        ]
      };
      var loadedSchemas = {};

      function fetchArrayBuffer(url) {
        return new Promise(function(resolve, reject) {
          var xhr = new XMLHttpRequest();
          xhr.open('GET', url, true);
          xhr.responseType = 'arraybuffer';
          xhr.onload = function() {
            if (xhr.status >= 200 && xhr.status < 300) resolve(new Uint8Array(xhr.response));
            else reject(new Error('HTTP ' + xhr.status + ' for ' + url));
          };
          xhr.onerror = function() { reject(new Error('Network error for ' + url)); };
          xhr.send();
        });
      }

      // 加载 schema 及其依赖的字典文件，写入 /usr/share/rime-data/build/
      async function ensureSchema(dictUrl, schemaId) {
        if (loadedSchemas[schemaId]) return;
        var deps = SCHEMA_DEPENDENCIES[schemaId] || [];
        for (var i = 0; i < deps.length; i++) {
          await ensureSchema(dictUrl, deps[i]);
        }
        var target = SCHEMA_TARGETS[schemaId];
        if (!target) return;
        var files = SCHEMA_FILES[target] || [];
        for (var j = 0; j < files.length; j++) {
          var fname = files[j];
          try {
            var buf = await fetchArrayBuffer(dictUrl + target + '/' + fname);
            Module.FS.writeFile('/usr/share/rime-data/build/' + fname, buf);
          } catch (err) {
            // 单个文件缺失不致命，继续
          }
        }
        // 设置 schema 名称（对应 server 端 engine.setSchemaName）
        Module.ccall('set_schema_name', 'null', ['string', 'string'],
          [schemaId, SCHEMA_NAMES[schemaId] || schemaId]);
        loadedSchemas[schemaId] = true;
      }

      function loadWasm(baseUrl) {
        return new Promise((resolve, reject) => {
          let settled = false;
          function ok() { if (!settled) { settled = true; ready = true; resolve(); } }
          function fail(err) { if (!settled) { settled = true; reject(err); } }
          // 在 rime.js 执行前设置 Module 配置
          // rime.js 是旧版 emscripten 输出，直接读取全局 Module，没有 createModule 工厂函数
          Module = {
            locateFile: function(path) { return baseUrl + path; },
            onRuntimeInitialized: function() {
              try {
                Module.FS.mkdir('/rime');
                Module.FS.mount(IDBFS, {}, '/rime');
                Module.FS.syncfs(true, function() {
                  try {
                    Module.ccall('init', 'null', [], []);
                    ok();
                  } catch (err) { fail(err); }
                });
              } catch (err) { fail(err); }
            }
          };
          // 超时保护（200s）
          setTimeout(function() { fail(new Error('WASM init timeout (200s)')); }, 200000);
          try {
            importScripts(baseUrl + 'rime.js');
          } catch (err) { fail(err); }
        });
      }

      function syncFS(direction) {
        return new Promise((resolve) => {
          Module.FS.syncfs(direction === 'write', () => resolve());
        });
      }

      function emptyResult() {
        return { state: 3, head: '', body: '', tail: '', candidates: [], committed: '', page: 1, isLastPage: true, highlighted: 0, selectLabels: [], updatedOptions: {}, updatedSchema: '' };
      }

      self.onmessage = async function(e) {
        const { _msgId, type, ...rest } = e.data;
        try {
          if (type === 'load') {
            await loadWasm(rest.wasmUrl);
            self.postMessage({ _msgId, type: 'loaded' });
            return;
          }
          if (!ready) {
            self.postMessage({ _msgId, type: 'error', error: 'WASM not ready' });
            return;
          }
          let result;
          switch (type) {
            case 'init': {
              // 对齐 server 流程：ensureSchema → setPageSize → setIME（不在 init 中 deploy）
              if (rest.dictUrl) await ensureSchema(rest.dictUrl, rest.schema);
              Module.ccall('set_page_size', 'null', ['number'], [rest.pageSize]);
              Module.ccall('set_ime', 'null', ['string'], [rest.schema]);
              self.postMessage({ _msgId, type: 'result', ...emptyResult(), updatedSchema: rest.schema });
              return;
            }
            case 'process': {
              result = Module.ccall('process', 'string', ['string'], [rest.key]);
              break;
            }
            case 'selectCandidate': {
              result = Module.ccall('select_candidate_on_current_page', 'string', ['number'], [rest.index]);
              break;
            }
            case 'changePage': {
              result = Module.ccall('change_page', 'string', ['boolean'], [rest.backward]);
              break;
            }
            case 'setOption': {
              Module.ccall('set_option', 'null', ['string', 'number'], [rest.option, rest.value ? 1 : 0]);
              self.postMessage({ _msgId, type: 'result', ...emptyResult() });
              return;
            }
            case 'setIME': {
              // 切换 schema 前确保对应字典已加载
              if (rest.dictUrl) await ensureSchema(rest.dictUrl, rest.schema);
              Module.ccall('set_ime', 'null', ['string'], [rest.schema]);
              self.postMessage({ _msgId, type: 'result', ...emptyResult(), updatedSchema: rest.schema });
              return;
            }
            case 'setPageSize': {
              Module.ccall('set_page_size', 'null', ['number'], [rest.pageSize]);
              self.postMessage({ _msgId, type: 'result', ...emptyResult() });
              return;
            }
            case 'deploy': {
              Module.ccall('deploy', 'null', [], []);
              self.postMessage({ _msgId, type: 'result', ...emptyResult() });
              return;
            }
            default:
              self.postMessage({ _msgId, type: 'error', error: 'Unknown type: ' + type });
              return;
          }
          const parsed = JSON.parse(result);
          self.postMessage({ _msgId, type: 'result', ...parsed });
          if (parsed.committed) syncFS('write');
        } catch (err) {
          self.postMessage({ _msgId, type: 'error', error: String(err) });
        }
      };
    `
    const blob = new Blob([workerCode], { type: 'application/javascript' })
    const url = URL.createObjectURL(blob)
    const worker = new Worker(url)
    URL.revokeObjectURL(url)

    worker.onmessage = (ev: MessageEvent) => {
      const msg = ev.data
      const id = msg._msgId
      if (id != null && this.pending.has(id)) {
        const p = this.pending.get(id)!
        this.pending.delete(id)
        if (msg.type === 'error') {
          p.reject(new Error(msg.error))
        } else if (msg.type === 'loaded') {
          p.resolve()
        } else if (msg.type === 'result') {
          p.resolve(toRimeResult(msg))
        }
      }
    }
    return worker
  }

  private send(msg: Record<string, any>, timeout = 15000): Promise<RimeResult | void> {
    return new Promise((resolve, reject) => {
      if (!this.worker) { reject(new Error('Worker not initialized')); return }
      const id = ++this.msgId
      this.pending.set(id, { resolve: resolve as any, reject })
      this.worker.postMessage({ ...msg, _msgId: id })
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); reject(new Error('Worker request timeout')) }
      }, timeout)
    })
  }

  async init(schema: string, pageSize: number): Promise<void> {
    this.worker = this.createWorker()
    // WASM 加载耗时较长（下载+初始化+IDB syncfs），使用 210s 超时
    // （worker 内部 loadWasm 有 200s 超时，这里留 10s 余量让 worker 先 reject）
    await this.send({ type: 'load', wasmUrl: this.wasmUrl }, 210000)
    // 字典目录与 wasm 目录同级：'.../wasm/' → '.../dict/'
    const dictUrl = this.wasmUrl.replace(/\/wasm\/?$/, '/dict/')
    // init 阶段需下载字典文件，同样使用长超时
    await this.send({ type: 'init', schema, pageSize, dictUrl }, 210000)
  }

  async process(key: string): Promise<RimeResult> {
    return (await this.send({ type: 'process', key })) as RimeResult
  }

  async selectCandidate(index: number): Promise<RimeResult> {
    return (await this.send({ type: 'selectCandidate', index })) as RimeResult
  }

  async changePage(backward: boolean): Promise<RimeResult> {
    return (await this.send({ type: 'changePage', backward })) as RimeResult
  }

  async setOption(option: string, value: boolean): Promise<void> {
    await this.send({ type: 'setOption', option, value })
  }

  async setIME(schema: string): Promise<RimeResult> {
    const dictUrl = this.wasmUrl.replace(/\/wasm\/?$/, '/dict/')
    return (await this.send({ type: 'setIME', schema, dictUrl })) as RimeResult
  }

  async setPageSize(size: number): Promise<void> {
    await this.send({ type: 'setPageSize', pageSize: size })
  }

  async deploy(): Promise<void> {
    await this.send({ type: 'deploy' })
  }

  destroy() {
    this.destroyed = true
    if (this.worker) { this.worker.terminate(); this.worker = null }
    for (const [, p] of this.pending) p.reject(new Error('Backend destroyed'))
    this.pending.clear()
  }
}

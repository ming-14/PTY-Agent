var RimePlugin;
/******/ (() => { // webpackBootstrap
/******/ 	"use strict";
/******/ 	// The require scope
/******/ 	const __webpack_require__ = {};
/******/ 	
/************************************************************************/
/******/ 	/* webpack/runtime/define property getters */
/******/ 	(() => {
/******/ 		// define getter/value functions for harmony exports
/******/ 		__webpack_require__.d = (exports, definition) => {
/******/ 			if(Array.isArray(definition)) {
/******/ 				var i = 0;
/******/ 				while(i < definition.length) {
/******/ 					var key = definition[i++];
/******/ 					var binding = definition[i++];
/******/ 					if(!__webpack_require__.o(exports, key)) {
/******/ 						if(binding === 0) {
/******/ 							Object.defineProperty(exports, key, { enumerable: true, value: definition[i++] });
/******/ 						} else {
/******/ 							Object.defineProperty(exports, key, { enumerable: true, get: binding });
/******/ 						}
/******/ 					} else if(binding === 0) { i++; }
/******/ 				}
/******/ 			} else {
/******/ 				for(var key in definition) {
/******/ 					if(__webpack_require__.o(definition, key) && !__webpack_require__.o(exports, key)) {
/******/ 						Object.defineProperty(exports, key, { enumerable: true, get: definition[key] });
/******/ 					}
/******/ 				}
/******/ 			}
/******/ 		};
/******/ 	})();
/******/ 	
/******/ 	/* webpack/runtime/hasOwnProperty shorthand */
/******/ 	(() => {
/******/ 		__webpack_require__.o = (obj, prop) => (Object.prototype.hasOwnProperty.call(obj, prop))
/******/ 	})();
/******/ 	
/******/ 	/* webpack/runtime/make namespace object */
/******/ 	(() => {
/******/ 		// define __esModule on exports
/******/ 		__webpack_require__.r = (exports) => {
/******/ 			if(Symbol.toStringTag) {
/******/ 				Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });
/******/ 			}
/******/ 			Object.defineProperty(exports, '__esModule', { value: true });
/******/ 		};
/******/ 	})();
/******/ 	
/************************************************************************/
let __webpack_exports__ = {};
// ESM COMPAT FLAG
__webpack_require__.r(__webpack_exports__);

// EXPORTS
__webpack_require__.d(__webpack_exports__, {
  RimeIME: () => (/* reexport */ RimeIME),
  RimeKeyboard: () => (/* reexport */ RimeKeyboard),
  RimeManager: () => (/* reexport */ RimeManager),
  RimePanel: () => (/* reexport */ RimePanel),
  RimeTKLKeyboard: () => (/* reexport */ RimeTKLKeyboard),
  RimeToolbar: () => (/* reexport */ RimeToolbar)
});

;// ./src/backends/remote.ts

class RemoteBackend {
  constructor(serverUrl) {
    this.ws = null;
    this.msgId = 0;
    this.pending = /* @__PURE__ */ new Map();
    this.handler = null;
    this.reconnectTimer = null;
    this.destroyed = false;
    this.currentSchema = "";
    this.currentPageSize = 5;
    this.serverUrl = serverUrl;
  }
  connect() {
    return new Promise((resolve, reject) => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        resolve();
        return;
      }
      this.ws = new WebSocket(this.serverUrl);
      const onOpen = () => {
        this.ws.removeEventListener("open", onOpen);
        this.ws.removeEventListener("error", onError);
        resolve();
      };
      const onError = () => {
        this.ws.removeEventListener("open", onOpen);
        this.ws.removeEventListener("error", onError);
        reject(new Error("WebSocket connection failed"));
      };
      this.ws.addEventListener("open", onOpen);
      this.ws.addEventListener("error", onError);
      this.ws.addEventListener("message", (ev) => {
        this.onMessage(ev);
      });
      this.ws.addEventListener("close", () => {
        if (!this.destroyed) this.scheduleReconnect();
      });
    });
  }
  onMessage(ev) {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (msg.type === "result" || msg.type === "deployStatus") {
      const typedMsg = msg;
      if (typedMsg.type === "result" && typeof typedMsg._msgId === "number") {
        const id = typedMsg._msgId;
        const pending = this.pending.get(id);
        if (pending) {
          this.pending.delete(id);
          const result = {
            state: typedMsg.state,
            composition: typedMsg.composition,
            candidates: typedMsg.candidates,
            committed: typedMsg.committed,
            page: typedMsg.page,
            isLastPage: typedMsg.isLastPage,
            highlighted: typedMsg.highlighted,
            selectLabels: typedMsg.selectLabels,
            updatedOptions: typedMsg.updatedOptions,
            updatedSchema: typedMsg.updatedSchema
          };
          pending.resolve(result);
        }
      }
      if (this.handler) this.handler(typedMsg);
    }
  }
  send(msg) {
    return new Promise((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        reject(new Error("WebSocket not connected"));
        return;
      }
      const id = ++this.msgId;
      const payload = { ...msg, _msgId: id };
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify(payload));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error("Request timeout"));
        }
      }, 1e4);
    });
  }
  scheduleReconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(async () => {
      if (this.destroyed) return;
      try {
        await this.connect();
        if (this.currentSchema) await this.init(this.currentSchema, this.currentPageSize);
      } catch {
        this.scheduleReconnect();
      }
    }, 3e3);
  }
  setHandler(handler) {
    this.handler = handler;
  }
  async init(schema, pageSize) {
    this.currentSchema = schema;
    this.currentPageSize = pageSize;
    await this.connect();
    const msg = { type: "init", schema, pageSize };
    await this.send(msg);
  }
  async process(key) {
    const msg = { type: "process", key };
    return this.send(msg);
  }
  async selectCandidate(index) {
    const msg = { type: "selectCandidate", index };
    return this.send(msg);
  }
  async changePage(backward) {
    const msg = { type: "changePage", backward };
    return this.send(msg);
  }
  async setOption(option, value) {
    const msg = { type: "setOption", option, value };
    await this.send(msg);
  }
  async setIME(schema) {
    this.currentSchema = schema;
    const msg = { type: "setIME", schema };
    return this.send(msg);
  }
  async setPageSize(size) {
    this.currentPageSize = size;
    const msg = { type: "setPageSize", pageSize: size };
    await this.send(msg);
  }
  async deploy() {
    const msg = { type: "deploy" };
    await this.send(msg);
  }
  destroy() {
    this.destroyed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    for (const [, p] of this.pending) p.reject(new Error("Backend destroyed"));
    this.pending.clear();
  }
}

;// ./src/backends/wasm.ts

const RIME_STATE_MAP = {
  0: "committed",
  1: "accepted",
  2: "rejected",
  3: "unhandled"
};
function toRimeResult(msg) {
  return {
    state: RIME_STATE_MAP[msg.state] ?? "unhandled",
    composition: { head: msg.head ?? "", body: msg.body ?? "", tail: msg.tail ?? "" },
    candidates: msg.candidates ?? [],
    committed: msg.committed ?? "",
    page: msg.page ?? 1,
    isLastPage: msg.isLastPage ?? true,
    highlighted: msg.highlighted ?? 0,
    selectLabels: msg.selectLabels ?? [],
    updatedOptions: msg.updatedOptions ?? {},
    updatedSchema: msg.updatedSchema ?? ""
  };
}
class WasmBackend {
  constructor(wasmUrl) {
    this.worker = null;
    this.msgId = 0;
    this.pending = /* @__PURE__ */ new Map();
    this.destroyed = false;
    this.wasmUrl = wasmUrl;
  }
  createWorker() {
    const workerCode = `
      var Module;
      let ready = false;

      // rime.js \u5185\u90E8\u901A\u8FC7 emscripten_asm_const_int \u8C03\u7528 _deployStatus(status, message)
      // \u5FC5\u987B\u5728 Worker \u5168\u5C40\u5B9A\u4E49\uFF0C\u5426\u5219 deploy \u65F6\u4F1A ReferenceError
      function _deployStatus(status, message) {
        self.postMessage({ type: 'deployStatus', status: status, message: message });
      }

      // \u2500\u2500\u2500 \u5B57\u5178\u52A0\u8F7D\uFF08\u955C\u50CF server/src/dict-manager.ts \u7684\u903B\u8F91\uFF09 \u2500\u2500\u2500
      // rime.data \u53EA\u542B\u914D\u7F6E\u6587\u4EF6\uFF08opencc/default.yaml/lua\uFF09\uFF0C\u771F\u6B63\u7684\u8BCD\u5E93\uFF08.table.bin/.prism.bin
      // \u7B49\uFF09\u9700\u8981\u5355\u72EC\u4E0B\u8F7D\u5E76\u5199\u5165 /usr/share/rime-data/build/ \u540E\u518D set_ime/deploy
      var SCHEMA_TARGETS = { luna_pinyin: 'luna-pinyin', stroke: 'stroke' };
      var SCHEMA_NAMES = { luna_pinyin: '\u6719\u6708\u62FC\u97F3', stroke: '\u4E94\u7B14\u753B' };
      var SCHEMA_DEPENDENCIES = { luna_pinyin: ['stroke'], stroke: [] };
      // \u6BCF\u4E2A schema target \u76EE\u5F55\u4E0B\u9700\u8981\u52A0\u8F7D\u7684\u6587\u4EF6\uFF08\u4E0E dict/{target}/ \u5B9E\u9645\u6587\u4EF6\u4E00\u81F4\uFF09
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

      // \u52A0\u8F7D schema \u53CA\u5176\u4F9D\u8D56\u7684\u5B57\u5178\u6587\u4EF6\uFF0C\u5199\u5165 /usr/share/rime-data/build/
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
            // \u5355\u4E2A\u6587\u4EF6\u7F3A\u5931\u4E0D\u81F4\u547D\uFF0C\u7EE7\u7EED
          }
        }
        // \u8BBE\u7F6E schema \u540D\u79F0\uFF08\u5BF9\u5E94 server \u7AEF engine.setSchemaName\uFF09
        Module.ccall('set_schema_name', 'null', ['string', 'string'],
          [schemaId, SCHEMA_NAMES[schemaId] || schemaId]);
        loadedSchemas[schemaId] = true;
      }

      function loadWasm(baseUrl) {
        return new Promise((resolve, reject) => {
          let settled = false;
          function ok() { if (!settled) { settled = true; ready = true; resolve(); } }
          function fail(err) { if (!settled) { settled = true; reject(err); } }
          // \u5728 rime.js \u6267\u884C\u524D\u8BBE\u7F6E Module \u914D\u7F6E
          // rime.js \u662F\u65E7\u7248 emscripten \u8F93\u51FA\uFF0C\u76F4\u63A5\u8BFB\u53D6\u5168\u5C40 Module\uFF0C\u6CA1\u6709 createModule \u5DE5\u5382\u51FD\u6570
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
          // \u8D85\u65F6\u4FDD\u62A4\uFF08200s\uFF09
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
              // \u5BF9\u9F50 server \u6D41\u7A0B\uFF1AensureSchema \u2192 setPageSize \u2192 setIME\uFF08\u4E0D\u5728 init \u4E2D deploy\uFF09
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
              // \u5207\u6362 schema \u524D\u786E\u4FDD\u5BF9\u5E94\u5B57\u5178\u5DF2\u52A0\u8F7D
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
    `;
    const blob = new Blob([workerCode], { type: "application/javascript" });
    const url = URL.createObjectURL(blob);
    const worker = new Worker(url);
    URL.revokeObjectURL(url);
    worker.onmessage = (ev) => {
      const msg = ev.data;
      const id = msg._msgId;
      if (id != null && this.pending.has(id)) {
        const p = this.pending.get(id);
        this.pending.delete(id);
        if (msg.type === "error") {
          p.reject(new Error(msg.error));
        } else if (msg.type === "loaded") {
          p.resolve();
        } else if (msg.type === "result") {
          p.resolve(toRimeResult(msg));
        }
      }
    };
    return worker;
  }
  send(msg, timeout = 15e3) {
    return new Promise((resolve, reject) => {
      if (!this.worker) {
        reject(new Error("Worker not initialized"));
        return;
      }
      const id = ++this.msgId;
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ ...msg, _msgId: id });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error("Worker request timeout"));
        }
      }, timeout);
    });
  }
  async init(schema, pageSize) {
    this.worker = this.createWorker();
    await this.send({ type: "load", wasmUrl: this.wasmUrl }, 21e4);
    const dictUrl = this.wasmUrl.replace(/\/wasm\/?$/, "/dict/");
    await this.send({ type: "init", schema, pageSize, dictUrl }, 21e4);
  }
  async process(key) {
    return await this.send({ type: "process", key });
  }
  async selectCandidate(index) {
    return await this.send({ type: "selectCandidate", index });
  }
  async changePage(backward) {
    return await this.send({ type: "changePage", backward });
  }
  async setOption(option, value) {
    await this.send({ type: "setOption", option, value });
  }
  async setIME(schema) {
    const dictUrl = this.wasmUrl.replace(/\/wasm\/?$/, "/dict/");
    return await this.send({ type: "setIME", schema, dictUrl });
  }
  async setPageSize(size) {
    await this.send({ type: "setPageSize", pageSize: size });
  }
  async deploy() {
    await this.send({ type: "deploy" });
  }
  destroy() {
    this.destroyed = true;
    if (this.worker) {
      this.worker.terminate();
      this.worker = null;
    }
    for (const [, p] of this.pending) p.reject(new Error("Backend destroyed"));
    this.pending.clear();
  }
}

;// ./src/ime.ts



const STORAGE_KEY = "rime-ime-state";
class RimeIME {
  constructor(config) {
    this.commitCallbacks = [];
    this.optionCallbacks = [];
    this.schemaCallbacks = [];
    this.errorCallbacks = [];
    this.deployStatusCallbacks = [];
    this.resultCallbacks = [];
    this.initialized = false;
    this._lastResult = null;
    this._state = {
      isEnglish: false,
      isFullWidth: false,
      isEnglishPunct: false,
      isEmoji: false,
      isSimplification: true,
      currentSchema: ""
    };
    /** 标点锁定（UI 层概念，非 RIME 引擎选项）。
     * true 时，中/En 语言切换不会自动改变 ascii_punct。
     * 由 RimeToolbar 双击 。/. 按钮设置，RimeToolbar 和 RimeKeyboard 共同尊重。 */
    this.punctLocked = false;
    this._persist = config.persist ?? true;
    this.config = {
      mode: config.mode,
      persist: this._persist,
      serverUrl: config.serverUrl ?? "",
      wasmUrl: config.wasmUrl ?? "",
      schema: config.schema ?? "luna_pinyin",
      pageSize: config.pageSize ?? 5
    };
    this.loadState();
    if (config.mode === "remote") {
      if (!config.serverUrl) throw new Error("serverUrl is required for remote mode");
      const remote = new RemoteBackend(config.serverUrl);
      remote.setHandler((msg) => {
        if (msg.type === "result") {
          const r = msg;
          if (r.updatedOptions && Object.keys(r.updatedOptions).length > 0) {
            this.syncState(r.updatedOptions);
            this.optionCallbacks.forEach((cb) => cb(r.updatedOptions));
          }
          if (r.updatedSchema) {
            this._state.currentSchema = r.updatedSchema;
            this.schemaCallbacks.forEach((cb) => cb(r.updatedSchema));
          }
        } else if (msg.type === "deployStatus") {
          const d = msg;
          this.deployStatusCallbacks.forEach((cb) => cb(d.status));
        }
      });
      this.backend = remote;
    } else if (config.mode === "wasm") {
      if (!config.wasmUrl) throw new Error("wasmUrl is required for wasm mode");
      this.backend = new WasmBackend(config.wasmUrl);
    } else {
      throw new Error(`Unknown mode: ${config.mode}`);
    }
  }
  async init() {
    if (this.initialized) return;
    try {
      const schema = this._state.currentSchema || this.config.schema;
      await this.backend.init(schema, this.config.pageSize);
      this._state.currentSchema = schema;
      if (this._state.isEnglish) await this.backend.setOption("ascii_mode", true);
      await this.backend.setOption("simplification", this._state.isSimplification);
      if (this._state.isEnglishPunct) await this.backend.setOption("ascii_punct", true);
      if (this._state.isFullWidth) await this.backend.setOption("full_shape", true);
      this.initialized = true;
    } catch (err) {
      this.errorCallbacks.forEach((cb) => cb(err));
      throw err;
    }
  }
  async processKey(key) {
    this.ensureInit();
    try {
      const result = await this.backend.process(key);
      this._lastResult = result;
      this.handleResult(result);
      this.resultCallbacks.forEach((cb) => cb(result));
      return result;
    } catch (err) {
      this.errorCallbacks.forEach((cb) => cb(err));
      throw err;
    }
  }
  async selectCandidate(index) {
    this.ensureInit();
    try {
      const result = await this.backend.selectCandidate(index);
      this._lastResult = result;
      this.handleResult(result);
      this.resultCallbacks.forEach((cb) => cb(result));
      return result;
    } catch (err) {
      this.errorCallbacks.forEach((cb) => cb(err));
      throw err;
    }
  }
  async changePage(backward) {
    this.ensureInit();
    try {
      const result = await this.backend.changePage(backward);
      this._lastResult = result;
      this.handleResult(result);
      this.resultCallbacks.forEach((cb) => cb(result));
      return result;
    } catch (err) {
      this.errorCallbacks.forEach((cb) => cb(err));
      throw err;
    }
  }
  async setOption(option, value) {
    this.ensureInit();
    await this.backend.setOption(option, value);
    const opts = { [option]: value };
    this.syncState(opts);
    this.optionCallbacks.forEach((cb) => cb(opts));
  }
  async setIME(schema) {
    this.ensureInit();
    try {
      const result = await this.backend.setIME(schema);
      this._lastResult = result;
      this._state.currentSchema = schema;
      this._state.isEnglish = false;
      await this.backend.setOption("simplification", this._state.isSimplification);
      this.handleResult(result);
      this.resultCallbacks.forEach((cb) => cb(result));
      return result;
    } catch (err) {
      this.errorCallbacks.forEach((cb) => cb(err));
      throw err;
    }
  }
  async setPageSize(size) {
    this.ensureInit();
    this.config.pageSize = size;
    await this.backend.setPageSize(size);
  }
  async deploy() {
    this.ensureInit();
    await this.backend.deploy();
  }
  getState() {
    return { ...this._state };
  }
  getCandidates() {
    return this._lastResult?.candidates ?? [];
  }
  getComposition() {
    const r = this._lastResult;
    if (!r) return { head: "", body: "", tail: "" };
    const comp = r.composition || {};
    return {
      head: comp.head ?? r.head ?? "",
      body: comp.body ?? r.body ?? "",
      tail: comp.tail ?? r.tail ?? ""
    };
  }
  getLastResult() {
    return this._lastResult;
  }
  getCurrentSchema() {
    return this._state.currentSchema;
  }
  getPageSize() {
    return this.config.pageSize;
  }
  isInitialized() {
    return this.initialized;
  }
  onCommit(callback) {
    this.commitCallbacks.push(callback);
  }
  onOptionChange(callback) {
    this.optionCallbacks.push(callback);
  }
  onSchemaChange(callback) {
    this.schemaCallbacks.push(callback);
  }
  onError(callback) {
    this.errorCallbacks.push(callback);
  }
  onDeployStatus(callback) {
    this.deployStatusCallbacks.push(callback);
  }
  onResultChange(callback) {
    this.resultCallbacks.push(callback);
  }
  offCommit(callback) {
    this.commitCallbacks = this.commitCallbacks.filter((cb) => cb !== callback);
  }
  offOptionChange(callback) {
    this.optionCallbacks = this.optionCallbacks.filter((cb) => cb !== callback);
  }
  offSchemaChange(callback) {
    this.schemaCallbacks = this.schemaCallbacks.filter((cb) => cb !== callback);
  }
  offError(callback) {
    this.errorCallbacks = this.errorCallbacks.filter((cb) => cb !== callback);
  }
  offDeployStatus(callback) {
    this.deployStatusCallbacks = this.deployStatusCallbacks.filter((cb) => cb !== callback);
  }
  offResultChange(callback) {
    this.resultCallbacks = this.resultCallbacks.filter((cb) => cb !== callback);
  }
  destroy() {
    this.initialized = false;
    this._lastResult = null;
    this.backend.destroy();
    this.commitCallbacks = [];
    this.optionCallbacks = [];
    this.schemaCallbacks = [];
    this.errorCallbacks = [];
    this.deployStatusCallbacks = [];
    this.resultCallbacks = [];
  }
  handleResult(result) {
    if (result.committed) {
      this.commitCallbacks.forEach((cb) => cb(result.committed));
    }
    if (result.updatedOptions && Object.keys(result.updatedOptions).length > 0) {
      this.syncState(result.updatedOptions);
      this.optionCallbacks.forEach((cb) => cb(result.updatedOptions));
    }
    if (result.updatedSchema) {
      this._state.currentSchema = result.updatedSchema;
      this.saveState();
      this.schemaCallbacks.forEach((cb) => cb(result.updatedSchema));
    }
  }
  syncState(opts) {
    if ("ascii_mode" in opts) this._state.isEnglish = opts.ascii_mode;
    if ("full_shape" in opts) this._state.isFullWidth = opts.full_shape;
    if ("ascii_punct" in opts) this._state.isEnglishPunct = opts.ascii_punct;
    if ("emoji_suggestion" in opts) this._state.isEmoji = opts.emoji_suggestion;
    if ("simplification" in opts) this._state.isSimplification = opts.simplification;
    this.saveState();
  }
  saveState() {
    if (!this._persist) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this._state));
    } catch {
    }
  }
  loadState() {
    if (!this._persist) return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved.currentSchema) this._state.currentSchema = saved.currentSchema;
      if (typeof saved.isEnglish === "boolean") this._state.isEnglish = saved.isEnglish;
      if (typeof saved.isFullWidth === "boolean") this._state.isFullWidth = saved.isFullWidth;
      if (typeof saved.isEnglishPunct === "boolean") this._state.isEnglishPunct = saved.isEnglishPunct;
      if (typeof saved.isEmoji === "boolean") this._state.isEmoji = saved.isEmoji;
      if (typeof saved.isSimplification === "boolean") this._state.isSimplification = saved.isSimplification;
    } catch {
    }
  }
  ensureInit() {
    if (!this.initialized) throw new Error("RimeIME not initialized. Call init() first.");
  }
}

;// ./src/panel.ts


const THEME_PRESETS = {
  dark: {
    panelBg: "rgba(24,24,28,.96)",
    panelBorder: "rgba(255,255,255,.08)",
    panelRadius: "6px",
    panelShadow: "drop-shadow(0 2px 8px rgba(0,0,0,.45))",
    compHeadColor: "#63e2b7",
    compBodyColor: "#70c0e8",
    compTailColor: "rgba(255,255,255,.82)",
    candColor: "rgba(255,255,255,.82)",
    candHoverBg: "rgba(255,255,255,.08)",
    candHighlightBg: "rgba(255,255,255,.08)",
    labelColor: "#70c0e8",
    commentColor: "rgba(255,255,255,.38)",
    navBtnBg: "rgba(255,255,255,.06)",
    navBtnColor: "#70c0e8",
    navPageColor: "rgba(255,255,255,.38)"
  },
  light: {
    panelBg: "rgba(255,255,255,.98)",
    panelBorder: "rgba(0,0,0,.12)",
    panelRadius: "6px",
    panelShadow: "drop-shadow(0 2px 8px rgba(0,0,0,.15))",
    compHeadColor: "#18a058",
    compBodyColor: "#2080f0",
    compTailColor: "rgba(0,0,0,.82)",
    candColor: "rgba(0,0,0,.82)",
    candHoverBg: "rgba(0,0,0,.06)",
    candHighlightBg: "rgba(0,0,0,.06)",
    labelColor: "#2080f0",
    commentColor: "rgba(0,0,0,.38)",
    navBtnBg: "rgba(0,0,0,.04)",
    navBtnColor: "#2080f0",
    navPageColor: "rgba(0,0,0,.38)"
  }
};
const SIZE_PRESETS = {
  compact: {
    compFontSize: "12px",
    compPadding: "2px 6px",
    candFontSize: "12px",
    candPadding: "2px 6px",
    candGap: "1px",
    labelFontSize: "10px",
    commentFontSize: "10px",
    navBtnSize: "16px",
    navPageFontSize: "10px"
  },
  normal: {
    compFontSize: "16px",
    compPadding: "4px 12px",
    candFontSize: "16px",
    candPadding: "4px 10px",
    candGap: "3px",
    labelFontSize: "13px",
    commentFontSize: "13px",
    navBtnSize: "22px",
    navPageFontSize: "12px"
  },
  large: {
    compFontSize: "20px",
    compPadding: "6px 16px",
    candFontSize: "20px",
    candPadding: "6px 14px",
    candGap: "4px",
    labelFontSize: "16px",
    commentFontSize: "16px",
    navBtnSize: "28px",
    navPageFontSize: "14px"
  }
};
const _RimePanel = class _RimePanel {
  constructor(config) {
    this.lastResult = null;
    this.isEnglish = false;
    this.isFullWidth = false;
    this.isEnglishPunct = false;
    this.isEmoji = false;
    this.destroyed = false;
    this.editing = false;
    this.exclusiveShift = false;
    this.dragging = false;
    this.dragged = false;
    this.dragX = 0;
    this.dragY = 0;
    this.panelX = 0;
    this.panelY = 0;
    this.savedInputMode = null;
    this.savedAutoComplete = null;
    this.androidChromium = false;
    this.acStart = 0;
    this.acEnd = 0;
    // 拖拽事件引用（destroy 时需移除）
    this._dragMoveHandler = null;
    this._dragTouchMoveHandler = null;
    this._dragEndHandler = null;
    this.target = config.target;
    if (config.ime) {
      this.ime = config.ime;
      this._ownsIME = false;
    } else {
      this.ime = new RimeIME(config);
      this._ownsIME = true;
    }
    this._renderOnly = config.renderOnly ?? false;
    this._currentTheme = config.theme ?? "dark";
    this._currentSize = config.size ?? "normal";
    this._showComment = config.showComment ?? true;
    this._showNavigation = config.showNavigation ?? true;
    this._vertical = config.vertical ?? false;
    this._positionOffset = {
      x: config.positionOffset?.x ?? 0,
      y: config.positionOffset?.y ?? 2
    };
    this._className = config.className ?? "";
    this._externalKeyHandling = config.externalKeyHandling ?? false;
    this._currentThemeVars = this.resolveThemeVars(this._currentTheme, this._currentSize, config.themeVars);
    if (this.target instanceof HTMLTextAreaElement || this.target instanceof HTMLInputElement) {
      this.savedInputMode = this.target.getAttribute("inputmode");
      this.savedAutoComplete = this.target.getAttribute("autocomplete");
      this.target.setAttribute("inputmode", "none");
      this.target.setAttribute("autocomplete", "off");
    }
    this.floatEl = document.createElement("div");
    this.floatEl.className = "rime-panel" + (this._className ? " " + this._className : "");
    this.floatEl.style.display = "none";
    if (config.style) Object.assign(this.floatEl.style, config.style);
    this.compEl = document.createElement("div");
    this.compEl.className = "rime-comp";
    this.candsEl = document.createElement("div");
    this.candsEl.className = "rime-cands";
    this.floatEl.appendChild(this.compEl);
    this.floatEl.appendChild(this.candsEl);
    document.body.appendChild(this.floatEl);
    this.injectStyle();
    this.applyThemeVars();
    this.bindIME();
    this.bindTarget();
    this.bindDrag();
  }
  async init() {
    await this.ime.init();
  }
  destroy() {
    this.destroyed = true;
    if (this._ownsIME) this.ime.destroy();
    this.floatEl.remove();
    if (this._dragMoveHandler) document.removeEventListener("mousemove", this._dragMoveHandler);
    if (this._dragTouchMoveHandler) document.removeEventListener("touchmove", this._dragTouchMoveHandler);
    if (this._dragEndHandler) {
      document.removeEventListener("mouseup", this._dragEndHandler);
      document.removeEventListener("touchend", this._dragEndHandler);
    }
    if (this.target instanceof HTMLTextAreaElement || this.target instanceof HTMLInputElement) {
      if (this.savedInputMode !== null) {
        this.target.setAttribute("inputmode", this.savedInputMode);
      } else {
        this.target.removeAttribute("inputmode");
      }
      if (this.savedAutoComplete !== null) {
        this.target.setAttribute("autocomplete", this.savedAutoComplete);
      } else {
        this.target.removeAttribute("autocomplete");
      }
    }
    _RimePanel.instanceCount--;
    if (_RimePanel.instanceCount <= 0) {
      const st = document.getElementById(_RimePanel.STYLE_ID);
      if (st) st.remove();
      _RimePanel.instanceCount = 0;
    }
  }
  getIME() {
    return this.ime;
  }
  getState() {
    return this.ime.getState();
  }
  getCandidates() {
    return this.ime.getCandidates();
  }
  getComposition() {
    return this.ime.getComposition();
  }
  getCurrentSchema() {
    return this.ime.getCurrentSchema();
  }
  getPageSize() {
    return this.ime.getPageSize();
  }
  isInitialized() {
    return this.ime.isInitialized();
  }
  getTheme() {
    return this._currentTheme;
  }
  getSize() {
    return this._currentSize;
  }
  getShowComment() {
    return this._showComment;
  }
  getShowNavigation() {
    return this._showNavigation;
  }
  getVertical() {
    return this._vertical;
  }
  async processKey(key) {
    return this.ime.processKey(key);
  }
  /** 处理一个按键并自动执行 analyze 流程，供外部（如虚拟键盘）调用 */
  async handleKey(rimeKey) {
    try {
      const r = await this.ime.processKey(rimeKey);
      this.analyze(r, rimeKey);
      return r;
    } catch {
      return null;
    }
  }
  async selectCandidate(index) {
    return this.ime.selectCandidate(index);
  }
  async changePage(backward) {
    return this.ime.changePage(backward);
  }
  async setOption(option, value) {
    return this.ime.setOption(option, value);
  }
  async setIME(schema) {
    return this.ime.setIME(schema);
  }
  async setPageSize(size) {
    return this.ime.setPageSize(size);
  }
  async deploy() {
    return this.ime.deploy();
  }
  setTheme(theme, vars) {
    this._currentTheme = theme;
    this._currentThemeVars = this.resolveThemeVars(theme, this._currentSize, vars);
    this.applyThemeVars();
  }
  setSize(size) {
    this._currentSize = size;
    this._currentThemeVars = this.resolveThemeVars(this._currentTheme, size);
    this.applyThemeVars();
  }
  setThemeVar(key, value) {
    if (value === void 0) return;
    this._currentThemeVars[key] = value;
    this.applyThemeVars();
  }
  setShowComment(show) {
    this._showComment = show;
  }
  setShowNavigation(show) {
    this._showNavigation = show;
  }
  setVertical(vertical) {
    this._vertical = vertical;
    this.candsEl.style.flexDirection = vertical ? "column" : "row";
  }
  setPositionOffset(offset) {
    if (offset.x !== void 0) this._positionOffset.x = offset.x;
    if (offset.y !== void 0) this._positionOffset.y = offset.y;
  }
  onCommit(cb) {
    this.ime.onCommit(cb);
  }
  onOptionChange(cb) {
    this.ime.onOptionChange(cb);
  }
  onSchemaChange(cb) {
    this.ime.onSchemaChange(cb);
  }
  onError(cb) {
    this.ime.onError(cb);
  }
  onDeployStatus(cb) {
    this.ime.onDeployStatus(cb);
  }
  onResultChange(cb) {
    this.ime.onResultChange(cb);
  }
  offCommit(cb) {
    this.ime.offCommit(cb);
  }
  offOptionChange(cb) {
    this.ime.offOptionChange(cb);
  }
  offSchemaChange(cb) {
    this.ime.offSchemaChange(cb);
  }
  offError(cb) {
    this.ime.offError(cb);
  }
  offDeployStatus(cb) {
    this.ime.offDeployStatus(cb);
  }
  offResultChange(cb) {
    this.ime.offResultChange(cb);
  }
  show() {
    this.floatEl.style.display = "flex";
    this.position();
  }
  hide() {
    this.floatEl.style.display = "none";
  }
  /** 外部驱动渲染：仅更新候选词显示和 editing 状态，不插入文字。
   * 供 RimeManager 在 renderOnly 模式下使用。 */
  renderResult(r) {
    this.analyze(r, "");
  }
  resolveThemeVars(theme, size, overrides) {
    const base = { ...THEME_PRESETS[theme] };
    const sizeVars = { ...SIZE_PRESETS[size] };
    const defaults = {
      panelBg: "rgba(24,24,28,.96)",
      panelBorder: "rgba(255,255,255,.08)",
      panelRadius: "6px",
      panelShadow: "drop-shadow(0 2px 8px rgba(0,0,0,.45))",
      panelFontFamily: '-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif',
      panelMaxWidth: "480px",
      panelZIndex: 99999,
      compFontSize: "14px",
      compPadding: "4px 10px",
      compHeadColor: "#63e2b7",
      compBodyColor: "#70c0e8",
      compTailColor: "rgba(255,255,255,.82)",
      candFontSize: "14px",
      candPadding: "3px 8px",
      candGap: "2px",
      candColor: "rgba(255,255,255,.82)",
      candHoverBg: "rgba(255,255,255,.08)",
      candHighlightBg: "rgba(255,255,255,.08)",
      labelColor: "#70c0e8",
      labelFontSize: "12px",
      commentColor: "rgba(255,255,255,.38)",
      commentFontSize: "12px",
      navBtnBg: "rgba(255,255,255,.06)",
      navBtnColor: "#70c0e8",
      navBtnSize: "20px",
      navPageColor: "rgba(255,255,255,.38)",
      navPageFontSize: "11px"
    };
    return { ...defaults, ...base, ...sizeVars, ...overrides };
  }
  applyThemeVars() {
    const v = this._currentThemeVars;
    const s = this.floatEl.style;
    s.setProperty("--rime-panel-bg", v.panelBg ?? "");
    s.setProperty("--rime-panel-border", v.panelBorder ?? "");
    s.setProperty("--rime-panel-radius", v.panelRadius ?? "");
    s.setProperty("--rime-panel-shadow", v.panelShadow ?? "");
    s.setProperty("--rime-panel-font-family", v.panelFontFamily ?? "");
    s.setProperty("--rime-panel-max-width", v.panelMaxWidth ?? "");
    s.setProperty("--rime-panel-z-index", String(v.panelZIndex ?? 99999));
    s.setProperty("--rime-comp-font-size", v.compFontSize ?? "");
    s.setProperty("--rime-comp-padding", v.compPadding ?? "");
    s.setProperty("--rime-comp-head-color", v.compHeadColor ?? "");
    s.setProperty("--rime-comp-body-color", v.compBodyColor ?? "");
    s.setProperty("--rime-comp-tail-color", v.compTailColor ?? "");
    s.setProperty("--rime-cand-font-size", v.candFontSize ?? "");
    s.setProperty("--rime-cand-padding", v.candPadding ?? "");
    s.setProperty("--rime-cand-gap", v.candGap ?? "");
    s.setProperty("--rime-cand-color", v.candColor ?? "");
    s.setProperty("--rime-cand-hover-bg", v.candHoverBg ?? "");
    s.setProperty("--rime-cand-highlight-bg", v.candHighlightBg ?? "");
    s.setProperty("--rime-label-color", v.labelColor ?? "");
    s.setProperty("--rime-label-font-size", v.labelFontSize ?? "");
    s.setProperty("--rime-comment-color", v.commentColor ?? "");
    s.setProperty("--rime-comment-font-size", v.commentFontSize ?? "");
    s.setProperty("--rime-nav-btn-bg", v.navBtnBg ?? "");
    s.setProperty("--rime-nav-btn-color", v.navBtnColor ?? "");
    s.setProperty("--rime-nav-btn-size", v.navBtnSize ?? "");
    s.setProperty("--rime-nav-page-color", v.navPageColor ?? "");
    s.setProperty("--rime-nav-page-font-size", v.navPageFontSize ?? "");
  }
  injectStyle() {
    if (document.getElementById(_RimePanel.STYLE_ID)) {
      _RimePanel.instanceCount++;
      return;
    }
    const s = document.createElement("style");
    s.id = _RimePanel.STYLE_ID;
    s.textContent = `
.rime-panel{position:fixed;z-index:var(--rime-panel-z-index,99999);pointer-events:auto;display:flex;flex-direction:column;min-width:40px;max-width:var(--rime-panel-max-width,480px);filter:var(--rime-panel-shadow,drop-shadow(0 2px 8px rgba(0,0,0,.45)));font-family:var(--rime-panel-font-family,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif);opacity:var(--ime-panel-opacity,1);transform:scale(var(--ime-panel-scale,1));transform-origin:top left}
.rime-comp{background:var(--rime-panel-bg,rgba(24,24,28,.96));border-radius:var(--rime-panel-radius,6px) var(--rime-panel-radius,6px) 0 0;padding:var(--rime-comp-padding,4px 10px);font-size:calc(var(--rime-comp-font-size,14px) * var(--ime-panel-scale,1));display:flex;align-items:center;gap:1px;border:1px solid var(--rime-panel-border,rgba(255,255,255,.08));border-bottom:none}
.rime-comp:empty{display:none;border:none;padding:0}
.rime-comp:empty+.rime-cands{border-radius:var(--rime-panel-radius,6px)}
.rime-ch{color:var(--rime-comp-head-color,#63e2b7)}.rime-cb{color:var(--rime-comp-body-color,#70c0e8);text-decoration:underline}.rime-ct{color:var(--rime-comp-tail-color,rgba(255,255,255,.82))}
.rime-cands{background:var(--rime-panel-bg,rgba(24,24,28,.96));border:1px solid var(--rime-panel-border,rgba(255,255,255,.08));border-radius:0 0 var(--rime-panel-radius,6px) var(--rime-panel-radius,6px);padding:4px 6px;display:flex;flex-wrap:wrap;gap:var(--rime-cand-gap,2px)}
.rime-comp:empty+.rime-cands{border-radius:var(--rime-panel-radius,6px)}
.rime-cands:empty{display:none}
.rime-cd{display:inline-flex;align-items:center;gap:4px;padding:var(--rime-cand-padding,3px 8px);border-radius:3px;cursor:pointer;font-size:calc(var(--rime-cand-font-size,14px) * var(--ime-panel-scale,1));transition:background .1s;user-select:none;color:var(--rime-cand-color,rgba(255,255,255,.82))}
.rime-cd:hover{background:var(--rime-cand-hover-bg,rgba(255,255,255,.08))}
.rime-cd.rime-hl{background:var(--rime-cand-highlight-bg,rgba(255,255,255,.08))}
.rime-ci{color:var(--rime-label-color,#70c0e8);font-size:calc(var(--rime-label-font-size,12px) * var(--ime-panel-scale,1));font-weight:500;min-width:14px}
.rime-cm{color:var(--rime-comment-color,rgba(255,255,255,.38));font-size:calc(var(--rime-comment-font-size,12px) * var(--ime-panel-scale,1));margin-left:2px}
.rime-nav{display:flex;align-items:center;gap:4px;margin-left:auto}
.rime-nb{background:var(--rime-nav-btn-bg,rgba(255,255,255,.06));border:none;color:var(--rime-nav-btn-color,#70c0e8);width:var(--rime-nav-btn-size,20px);height:var(--rime-nav-btn-size,20px);border-radius:3px;cursor:pointer;font-size:calc(10px * var(--ime-panel-scale,1));display:flex;align-items:center;justify-content:center}
.rime-nb:hover{background:var(--rime-cand-hover-bg,rgba(255,255,255,.12))}.rime-nb:disabled{opacity:.3;cursor:default}
.rime-np{color:var(--rime-nav-page-color,rgba(255,255,255,.38));font-size:calc(var(--rime-nav-page-font-size,11px) * var(--ime-panel-scale,1))}
`;
    document.head.appendChild(s);
    _RimePanel.instanceCount++;
  }
  insertText(text) {
    if (this.target instanceof HTMLTextAreaElement || this.target instanceof HTMLInputElement) {
      const s = this.target.selectionStart ?? this.target.value.length;
      const e = this.target.selectionEnd ?? s;
      const v = this.target.value;
      this.target.value = v.slice(0, s) + text + v.slice(e);
      this.target.selectionStart = this.target.selectionEnd = s + text.length;
    }
    this.target.dispatchEvent(new Event("input", { bubbles: true }));
  }
  isPrintable(key) {
    return /^[a-z0-9!"#$%&'()*+,./:;<=>?@[\] ^_`{|}~\\-]$/i.test(key);
  }
  analyze(r, rimeKey) {
    if (r.state === "committed") {
      this.editing = false;
      this.dragged = false;
      if (r.committed && !this._renderOnly) this.insertText(r.committed);
      this.hide();
    } else if (r.state === "accepted") {
      if (r.committed && !this._renderOnly) this.insertText(r.committed);
      this.editing = true;
      this.render(r);
      this.show();
    } else {
      this.editing = false;
      this.dragged = false;
      this.hide();
      if (r.state === "rejected" && r.updatedSchema) {
        this.ime.setIME(r.updatedSchema.split("/")[0]).then((nr) => {
          this.analyze(nr, "");
        }).catch(() => {
        });
      }
      if (r.state === "unhandled" && !this._renderOnly && this.isPrintable(rimeKey)) {
        this.insertText(rimeKey);
      }
    }
    this.lastResult = r;
    if (this.target instanceof HTMLTextAreaElement || this.target instanceof HTMLInputElement) {
      this.target.focus();
    }
  }
  bindIME() {
    this.ime.onOptionChange((opts) => {
      if ("ascii_mode" in opts) this.isEnglish = opts.ascii_mode;
      if ("full_shape" in opts) this.isFullWidth = opts.full_shape;
      if ("ascii_punct" in opts) this.isEnglishPunct = opts.ascii_punct;
      if ("emoji_suggestion" in opts) this.isEmoji = opts.emoji_suggestion;
    });
  }
  bindTarget() {
    const el = this.target;
    if (!this._externalKeyHandling) {
      el.addEventListener("compositionstart", (e) => {
        e.preventDefault();
        try {
          el.value = el.value;
        } catch {
        }
      });
      el.addEventListener("keydown", (e) => {
        if (this.destroyed) return;
        const { key, code } = e;
        if (key === "Unidentified") {
          if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
            this.androidChromium = true;
            this.acStart = el.selectionStart ?? 0;
            this.acEnd = el.selectionEnd ?? 0;
          }
          return;
        }
        if (key === "Shift" && !e.ctrlKey && !e.altKey && !e.metaKey) {
          this.exclusiveShift = true;
          return;
        }
        this.exclusiveShift = false;
        const isPrintableKey = this.isPrintable(key);
        const isAlt = key === "Alt";
        const hasControl = e.ctrlKey || e.metaKey || e.altKey;
        const hasShift = e.shiftKey;
        const isShortcut = hasControl || hasShift && !isPrintableKey;
        if (!this.editing) {
          if (document.activeElement !== el) return;
          if (!isPrintableKey && key !== "F4") return;
          if (isShortcut && !hasShift && !(e.ctrlKey && _RimePanel.CONTROL_ALLOWLIST.includes(key))) return;
        }
        let rimeKey;
        const wrap = (s) => `{${s}}`;
        if (isShortcut || !isPrintableKey) {
          rimeKey = /^[0-9a-z]$/i.test(key) ? key : _RimePanel.RIME_KEY_MAP[key];
          if (rimeKey === void 0) return;
          if (isAlt && code === "AltRight") rimeKey = "Alt_R";
          const modifiers = [];
          if (e.ctrlKey) modifiers.push("Control");
          if (e.metaKey) modifiers.push("Meta");
          if (e.altKey && !isAlt) modifiers.push("Alt");
          if (e.shiftKey) modifiers.push("Shift");
          modifiers.push(rimeKey);
          rimeKey = wrap(modifiers.join("+"));
        } else if (code.startsWith("Numpad")) {
          rimeKey = wrap(`KP_${code.substring(6)}`);
        } else {
          rimeKey = key;
        }
        if (!rimeKey) return;
        if (!this.dragged) {
          this.updatePosition();
        }
        e.preventDefault();
        this.ime.processKey(rimeKey).then((r) => {
          this.analyze(r, rimeKey);
        }).catch(() => {
        });
      });
      el.addEventListener("keyup", (e) => {
        if (this.destroyed) return;
        const { key } = e;
        if (key === "Shift" && this.exclusiveShift) {
          this.isEnglish = !this.isEnglish;
          this.ime.setOption("ascii_mode", this.isEnglish).catch(() => {
          });
        }
        this.exclusiveShift = false;
        if (this.editing) {
          const releaseKey = _RimePanel.RIME_KEY_MAP[key] || key;
          this.ime.processKey(`{Release+${releaseKey}}`).catch(() => {
          });
        }
      });
    }
    el.addEventListener("blur", () => {
      this.hide();
    });
    el.addEventListener("focus", () => {
      if (this.editing) this.show();
    });
    el.addEventListener("scroll", () => {
      this.position();
    });
    window.addEventListener("resize", () => {
      this.position();
    });
    if (!this._externalKeyHandling && (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement)) {
      el.addEventListener("input", () => {
        if (!this.androidChromium) return;
        this.androidChromium = false;
        const ta = el;
        const newText = ta.value;
        const oldText = newText.slice(0, this.acStart) + newText.slice(this.acStart + 1);
        if (oldText.length + 1 === newText.length && oldText.substring(0, this.acStart) === newText.substring(0, this.acStart) && oldText.substring(this.acEnd) === newText.substring(this.acEnd + 1)) {
          const ch = newText[this.acStart];
          ta.value = oldText;
          ta.selectionEnd = this.acStart;
          this.editing = true;
          this.ime.processKey(ch).then((r) => {
            this.analyze(r, ch);
          }).catch(() => {
          });
        }
      });
    }
  }
  updatePosition() {
    if (!(this.target instanceof HTMLTextAreaElement) && !(this.target instanceof HTMLInputElement)) return;
    const el = this.target;
    const box = el.getBoundingClientRect();
    const caret = this.getCaretCoords(el);
    this.panelX = box.x + caret.left;
    this.panelY = box.y + caret.top + caret.height - el.scrollTop;
  }
  bindDrag() {
    this.floatEl.addEventListener("mousedown", (e) => {
      this.dragX = e.clientX;
      this.dragY = e.clientY;
      if (this.dragged) {
        this.panelX = this.floatEl.getBoundingClientRect().left;
        this.panelY = this.floatEl.getBoundingClientRect().top;
      }
      this.dragging = true;
      e.preventDefault();
    });
    this.floatEl.addEventListener("touchstart", (e) => {
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      this.dragX = t.clientX;
      this.dragY = t.clientY;
      if (this.dragged) {
        this.panelX = this.floatEl.getBoundingClientRect().left;
        this.panelY = this.floatEl.getBoundingClientRect().top;
      }
      this.dragging = true;
    });
    this._dragMoveHandler = (e) => {
      if (!this.dragging) return;
      this.dragged = true;
      this.panelX += e.clientX - this.dragX;
      this.panelY += e.clientY - this.dragY;
      this.dragX = e.clientX;
      this.dragY = e.clientY;
      this.floatEl.style.left = this.panelX + "px";
      this.floatEl.style.top = this.panelY + "px";
    };
    document.addEventListener("mousemove", this._dragMoveHandler);
    this._dragTouchMoveHandler = (e) => {
      if (!this.dragging || e.touches.length !== 1) return;
      const t = e.touches[0];
      this.dragged = true;
      this.panelX += t.clientX - this.dragX;
      this.panelY += t.clientY - this.dragY;
      this.dragX = t.clientX;
      this.dragY = t.clientY;
      this.floatEl.style.left = this.panelX + "px";
      this.floatEl.style.top = this.panelY + "px";
    };
    document.addEventListener("touchmove", this._dragTouchMoveHandler);
    this._dragEndHandler = () => {
      this.dragging = false;
    };
    document.addEventListener("mouseup", this._dragEndHandler);
    document.addEventListener("touchend", this._dragEndHandler);
  }
  esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  render(r) {
    const comp = r.composition || {};
    const head = comp.head ?? r.head ?? "";
    const body = comp.body ?? r.body ?? "";
    const tail = comp.tail ?? r.tail ?? "";
    if (head || body || tail) {
      this.compEl.innerHTML = `<span class="rime-ch">${this.esc(head || "")}</span><span class="rime-cb">${this.esc(body || "")}</span><span class="rime-ct">${this.esc(tail || "")}</span>`;
    } else {
      this.compEl.innerHTML = "";
    }
    const cands = r.candidates || [];
    if (cands.length) {
      const lbs = r.selectLabels || [];
      const hl = r.highlighted ?? 0;
      let h = "";
      cands.forEach((c, i) => {
        h += `<div class="rime-cd${i === hl ? " rime-hl" : ""}" data-idx="${i}"><span class="rime-ci">${this.esc(lbs[i] || String(i + 1))}</span>` + this.esc(c.text) + (this._showComment && c.comment ? `<span class="rime-cm">${this.esc(c.comment)}</span>` : "") + `</div>`;
      });
      if (this._showNavigation) {
        const pg = r.page || 1, last = r.isLastPage;
        h += `<div class="rime-nav"><button class="rime-nb" data-pg="prev"${pg <= 1 ? " disabled" : ""}>&#9664;</button><span class="rime-np">${pg}</span><button class="rime-nb" data-pg="next"${last ? " disabled" : ""}>&#9654;</button></div>`;
      }
      this.candsEl.innerHTML = h;
      this.candsEl.style.flexDirection = this._vertical ? "column" : "row";
    } else {
      this.candsEl.innerHTML = "";
    }
    const has = head || body || tail || cands.length;
    if (has) {
      this.floatEl.style.display = "flex";
      if (!this.dragged) this.position();
      this.delegateClicks();
      if (!this._vertical) {
        const panelWidth = this.floatEl.getBoundingClientRect().width;
        const targetWidth = this.target instanceof HTMLElement ? this.target.getBoundingClientRect().width : 0;
        if (targetWidth > 0 && panelWidth > targetWidth) {
          this.candsEl.style.flexDirection = "column";
        }
      }
    } else {
      this.floatEl.style.display = "none";
    }
  }
  delegateClicks() {
    this.candsEl.onclick = (e) => {
      const t = e.target;
      const cd = t.closest(".rime-cd");
      if (cd && cd.dataset.idx != null) {
        this.ime.selectCandidate(parseInt(cd.dataset.idx, 10)).then((r) => {
          this.analyze(r, "");
        }).catch(() => {
        });
        return;
      }
      const nb = t.closest(".rime-nb");
      if (nb && nb.dataset.pg) {
        this.ime.changePage(nb.dataset.pg === "prev").then((r) => {
          this.analyze(r, "");
        }).catch(() => {
        });
      }
    };
  }
  position() {
    if (!(this.target instanceof HTMLTextAreaElement) && !(this.target instanceof HTMLInputElement)) return;
    const el = this.target;
    const box = el.getBoundingClientRect();
    const caret = this.getCaretCoords(el);
    const fw = this.floatEl.offsetWidth, fh = this.floatEl.offsetHeight;
    const vw = window.innerWidth, vh = window.innerHeight;
    let x = box.x + caret.left + this._positionOffset.x;
    let y = box.y + caret.top + caret.height - el.scrollTop + this._positionOffset.y;
    if (x + fw > vw - 8) x = vw - fw - 8;
    if (x < 8) x = 8;
    if (y + fh > vh - 8) y = box.y + caret.top - el.scrollTop - fh;
    this.floatEl.style.left = x + "px";
    this.floatEl.style.top = y + "px";
  }
  getCaretCoords(el) {
    const cs = getComputedStyle(el);
    const isInput = el.nodeName === "INPUT";
    const div = document.createElement("div");
    const ps = [
      "direction",
      "boxSizing",
      "width",
      "height",
      "overflowX",
      "overflowY",
      "borderTopWidth",
      "borderRightWidth",
      "borderBottomWidth",
      "borderLeftWidth",
      "borderStyle",
      "paddingTop",
      "paddingRight",
      "paddingBottom",
      "paddingLeft",
      "fontStyle",
      "fontVariant",
      "fontWeight",
      "fontStretch",
      "fontSize",
      "fontSizeAdjust",
      "lineHeight",
      "fontFamily",
      "textAlign",
      "textTransform",
      "textIndent",
      "textDecoration",
      "letterSpacing",
      "wordSpacing",
      "tabSize",
      "MozTabSize"
    ];
    div.style.position = "absolute";
    div.style.visibility = "hidden";
    div.style.whiteSpace = "pre-wrap";
    if (!isInput) div.style.wordWrap = "break-word";
    div.style.overflow = "hidden";
    ps.forEach((p) => {
      div.style[p] = cs[p];
    });
    if (isInput) {
      if (cs.boxSizing === "border-box") {
        const h = parseInt(cs.height);
        const outer = parseInt(cs.paddingTop) + parseInt(cs.paddingBottom) + parseInt(cs.borderTopWidth) + parseInt(cs.borderBottomWidth);
        const target = outer + parseInt(cs.lineHeight);
        if (h > target) div.style.lineHeight = h - outer + "px";
        else if (h === target) div.style.lineHeight = cs.lineHeight;
        else div.style.lineHeight = "0";
      } else {
        div.style.lineHeight = cs.height;
      }
    }
    div.textContent = el.value.substring(0, el.selectionEnd);
    if (isInput) div.textContent = div.textContent.replace(/\s/g, "\xA0");
    const span = document.createElement("span");
    span.textContent = el.value.substring(el.selectionEnd) || ".";
    div.appendChild(span);
    document.body.appendChild(div);
    const coordinates = {
      top: span.offsetTop + parseInt(cs.borderTopWidth),
      left: span.offsetLeft + parseInt(cs.borderLeftWidth),
      height: parseInt(cs.lineHeight)
    };
    document.body.removeChild(div);
    return coordinates;
  }
};
_RimePanel.STYLE_ID = "rime-panel-style";
_RimePanel.instanceCount = 0;
_RimePanel.CONTROL_ALLOWLIST = ["`"];
_RimePanel.RIME_KEY_MAP = {
  Escape: "Escape",
  F4: "F4",
  Backspace: "BackSpace",
  Delete: "Delete",
  Tab: "Tab",
  Enter: "Return",
  Return: "Return",
  Home: "Home",
  End: "End",
  PageUp: "Page_Up",
  PageDown: "Page_Down",
  ArrowUp: "Up",
  ArrowDown: "Down",
  ArrowLeft: "Left",
  ArrowRight: "Right",
  Alt: "Alt_L",
  " ": "space",
  "~": "asciitilde",
  "`": "quoteleft",
  "!": "exclam",
  "@": "at",
  "#": "numbersign",
  $: "dollar",
  "%": "percent",
  "^": "asciicircum",
  "&": "ampersand",
  "*": "asterisk",
  "(": "parenleft",
  ")": "parenright",
  "-": "minus",
  _: "underscore",
  "+": "plus",
  "=": "equal",
  "{": "braceleft",
  "[": "bracketleft",
  "}": "braceright",
  "]": "bracketright",
  ":": "colon",
  ";": "semicolon",
  '"': "quotedbl",
  "'": "apostrophe",
  "|": "bar",
  "\\": "backslash",
  "<": "less",
  ",": "comma",
  ">": "greater",
  ".": "period",
  "?": "question",
  "/": "slash"
};
let RimePanel = _RimePanel;

;// ./src/toolbar.ts

const TOOLBAR_THEME_PRESETS = {
  dark: {
    bg: "rgba(24,24,28,.96)",
    border: "rgba(255,255,255,.08)",
    btnColor: "rgba(255,255,255,.45)",
    btnHoverColor: "rgba(255,255,255,.82)",
    btnHoverBg: "rgba(255,255,255,.06)",
    btnActiveColor: "#70c0e8",
    btnActiveBg: "rgba(112,192,232,.1)",
    btnLockedShadow: "inset 0 0 0 2px #f0a020",
    btnDisabledOpacity: ".3",
    dragColor: "rgba(255,255,255,.25)",
    settingsBg: "rgba(24,24,28,.98)",
    settingsBorder: "rgba(255,255,255,.1)",
    settingsShadow: "0 4px 16px rgba(0,0,0,.5)",
    settingsColor: "rgba(255,255,255,.82)",
    settingsLabelColor: "rgba(255,255,255,.7)",
    toggleBg: "rgba(255,255,255,.06)",
    toggleBorder: "rgba(255,255,255,.12)",
    toggleColor: "rgba(255,255,255,.7)",
    toggleHoverColor: "rgba(255,255,255,.9)",
    toggleHoverBg: "rgba(255,255,255,.1)",
    toggleOnColor: "#70c0e8",
    toggleOnBg: "rgba(112,192,232,.15)",
    toggleOnBorder: "rgba(112,192,232,.4)",
    sliderBg: "rgba(255,255,255,.15)",
    sliderThumbBg: "#70c0e8"
  },
  light: {
    bg: "rgba(255,255,255,.98)",
    border: "rgba(0,0,0,.1)",
    btnColor: "rgba(0,0,0,.5)",
    btnHoverColor: "rgba(0,0,0,.85)",
    btnHoverBg: "rgba(0,0,0,.06)",
    btnActiveColor: "#2080f0",
    btnActiveBg: "rgba(32,128,240,.12)",
    btnLockedShadow: "inset 0 0 0 2px #f0a020",
    btnDisabledOpacity: ".3",
    dragColor: "rgba(0,0,0,.25)",
    settingsBg: "rgba(255,255,255,.99)",
    settingsBorder: "rgba(0,0,0,.12)",
    settingsShadow: "0 4px 16px rgba(0,0,0,.15)",
    settingsColor: "rgba(0,0,0,.85)",
    settingsLabelColor: "rgba(0,0,0,.6)",
    toggleBg: "rgba(0,0,0,.05)",
    toggleBorder: "rgba(0,0,0,.12)",
    toggleColor: "rgba(0,0,0,.6)",
    toggleHoverColor: "rgba(0,0,0,.85)",
    toggleHoverBg: "rgba(0,0,0,.08)",
    toggleOnColor: "#2080f0",
    toggleOnBg: "rgba(32,128,240,.12)",
    toggleOnBorder: "rgba(32,128,240,.4)",
    sliderBg: "rgba(0,0,0,.15)",
    sliderThumbBg: "#2080f0"
  }
};
function resolveToolbarThemeVars(theme, overrides) {
  return { ...TOOLBAR_THEME_PRESETS[theme], ...overrides };
}
function applyToolbarThemeVars(el, vars) {
  const s = el.style;
  const set = (prop, val) => {
    if (val !== void 0) s.setProperty(prop, val);
  };
  set("--rime-tb-bg", vars.bg);
  set("--rime-tb-border", vars.border);
  set("--rime-tb-btn-color", vars.btnColor);
  set("--rime-tb-btn-hover-color", vars.btnHoverColor);
  set("--rime-tb-btn-hover-bg", vars.btnHoverBg);
  set("--rime-tb-btn-active-color", vars.btnActiveColor);
  set("--rime-tb-btn-active-bg", vars.btnActiveBg);
  set("--rime-tb-btn-locked-shadow", vars.btnLockedShadow);
  set("--rime-tb-btn-disabled-opacity", vars.btnDisabledOpacity);
  set("--rime-tb-drag-color", vars.dragColor);
  set("--rime-tb-settings-bg", vars.settingsBg);
  set("--rime-tb-settings-border", vars.settingsBorder);
  set("--rime-tb-settings-shadow", vars.settingsShadow);
  set("--rime-tb-settings-color", vars.settingsColor);
  set("--rime-tb-settings-label-color", vars.settingsLabelColor);
  set("--rime-tb-toggle-bg", vars.toggleBg);
  set("--rime-tb-toggle-border", vars.toggleBorder);
  set("--rime-tb-toggle-color", vars.toggleColor);
  set("--rime-tb-toggle-hover-color", vars.toggleHoverColor);
  set("--rime-tb-toggle-hover-bg", vars.toggleHoverBg);
  set("--rime-tb-toggle-on-color", vars.toggleOnColor);
  set("--rime-tb-toggle-on-bg", vars.toggleOnBg);
  set("--rime-tb-toggle-on-border", vars.toggleOnBorder);
  set("--rime-tb-slider-bg", vars.sliderBg);
  set("--rime-tb-slider-thumb-bg", vars.sliderThumbBg);
}
const _RimeToolbar = class _RimeToolbar {
  constructor(config) {
    this.kbOpacityRow = null;
    this.kbOpacitySlider = null;
    this.destroyed = false;
    this.isEnglish = false;
    this.isFullWidth = false;
    this.isEnglishPunct = false;
    this.isEmoji = false;
    this.isSimplification = true;
    this.isPunctLocked = false;
    this.tbOpacity = 1;
    this.kbOpacity = 1;
    this.settingsOpen = false;
    this._outsideClickHandler = null;
    this.punctClickTimer = null;
    this.dragging = false;
    this.dragX = 0;
    this.dragY = 0;
    this.posX = 0;
    this.posY = 0;
    this._dragMoveHandler = null;
    this._dragTouchMoveHandler = null;
    this._dragEndHandler = null;
    this.provider = config.provider;
    this.target = config.target ?? null;
    this.keyboardEl = config.keyboardEl ?? null;
    this._currentTheme = config.theme ?? "dark";
    this.el = document.createElement("div");
    this.el.className = "rime-toolbar";
    applyToolbarThemeVars(this.el, resolveToolbarThemeVars(this._currentTheme));
    this.dragHandle = document.createElement("div");
    this.dragHandle.className = "rime-tb-drag";
    this.dragHandle.textContent = "\u2261";
    this.btnLang = this.createBtn("\u4E2D", "rime-tb-lang");
    this.btnVariant = this.createBtn("\u7B80", "rime-tb-variant");
    this.btnWidth = this.createBtn("\u534A\u6708", "rime-tb-width");
    this.btnPunct = this.createBtn("\u3002", "rime-tb-punct");
    this.btnSettings = this.createBtn("\u2699", "rime-tb-settings");
    const panelResult = this.createSettingsPanel();
    this.settingsPanel = panelResult.panel;
    this.emojiToggle = panelResult.emojiToggle;
    this.tbOpacitySlider = panelResult.tbOpacitySlider;
    this.kbOpacityRow = panelResult.kbOpacityRow;
    this.kbOpacitySlider = panelResult.kbOpacitySlider;
    this.el.appendChild(this.dragHandle);
    this.el.appendChild(this.btnLang);
    this.el.appendChild(this.btnVariant);
    this.el.appendChild(this.btnWidth);
    this.el.appendChild(this.btnPunct);
    this.el.appendChild(this.btnSettings);
    this.el.appendChild(this.settingsPanel);
    this.loadOpacity();
    this.applyOpacity();
    this.injectStyle();
    this.bindEvents();
    this.bindIME();
    this.bindDrag();
    this.updateState();
    this.el.addEventListener("mousedown", (e) => {
      const t = e.target;
      if (t.tagName === "INPUT" && t.type === "range") return;
      e.preventDefault();
    });
    const target = config.target;
    if (target && config.position !== "float") {
      if (config.position === "top") {
        target.parentElement?.insertBefore(this.el, target);
      } else {
        target.parentElement?.insertBefore(this.el, target.nextSibling);
      }
    } else {
      document.body.appendChild(this.el);
      this.el.style.position = "fixed";
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const tbW = this.el.offsetWidth || 200;
      const tbH = this.el.offsetHeight || 32;
      const savedPos = this.loadPosition();
      if (savedPos) {
        this.posX = savedPos.rx * vw;
        this.posY = savedPos.ry * vh;
      } else {
        const statusBar = document.getElementById("status-bar");
        const sbH = statusBar ? statusBar.offsetHeight : 28;
        this.posX = vw - tbW - 8;
        this.posY = vh - sbH - tbH - 8;
      }
      this.clampPosition();
      this.el.style.left = this.posX + "px";
      this.el.style.top = this.posY + "px";
    }
  }
  destroy() {
    this.destroyed = true;
    this.el.remove();
    if (this._dragMoveHandler) document.removeEventListener("mousemove", this._dragMoveHandler);
    if (this._dragTouchMoveHandler) document.removeEventListener("touchmove", this._dragTouchMoveHandler);
    if (this._dragEndHandler) {
      document.removeEventListener("mouseup", this._dragEndHandler);
      document.removeEventListener("touchend", this._dragEndHandler);
    }
    if (this._outsideClickHandler) {
      document.removeEventListener("mousedown", this._outsideClickHandler, true);
      this._outsideClickHandler = null;
    }
    _RimeToolbar.instanceCount--;
    if (_RimeToolbar.instanceCount <= 0) {
      const st = document.getElementById(_RimeToolbar.STYLE_ID);
      if (st) st.remove();
      _RimeToolbar.instanceCount = 0;
    }
  }
  getElement() {
    return this.el;
  }
  /** 切换主题（由外部 onThemeChange 调用） */
  setTheme(theme) {
    this._currentTheme = theme;
    applyToolbarThemeVars(this.el, resolveToolbarThemeVars(theme));
  }
  updateState() {
    const ime = this.provider.getIME();
    if (!ime || !this.provider.isInitialized()) return;
    const state = ime.getState();
    this.isEnglish = state.isEnglish;
    this.isFullWidth = state.isFullWidth;
    this.isEnglishPunct = state.isEnglishPunct;
    this.isEmoji = state.isEmoji;
    this.isSimplification = state.isSimplification;
    this.isPunctLocked = !!ime.punctLocked;
    this.refreshUI();
  }
  createBtn(text, cls) {
    const btn = document.createElement("button");
    btn.className = "rime-tb-btn" + (cls ? " " + cls : "");
    btn.textContent = text;
    return btn;
  }
  refreshUI() {
    this.btnLang.textContent = this.isEnglish ? "En" : "\u4E2D";
    this.btnLang.classList.toggle("rime-tb-active", !this.isEnglish);
    this.btnVariant.textContent = this.isSimplification ? "\u7B80" : "\u7E41";
    this.btnVariant.classList.toggle("rime-tb-active", this.isSimplification);
    this.btnVariant.disabled = this.isEnglish;
    this.btnWidth.textContent = this.isFullWidth ? "\u5168\u89D2" : "\u534A\u6708";
    this.btnWidth.classList.toggle("rime-tb-active", this.isFullWidth);
    this.btnPunct.textContent = this.isEnglishPunct ? "." : "\u3002";
    this.btnPunct.classList.toggle("rime-tb-active", !this.isEnglishPunct);
    this.btnPunct.classList.toggle("rime-tb-locked", this.isPunctLocked);
    if (this.emojiToggle) {
      this.emojiToggle.classList.toggle("rime-tb-toggle-on", this.isEmoji);
      this.emojiToggle.textContent = this.isEmoji ? "Emoji: \u5F00" : "Emoji: \u5173";
    }
  }
  bindEvents() {
    this.btnLang.addEventListener("click", () => {
      this.isEnglish = !this.isEnglish;
      this.provider.getIME().setOption("ascii_mode", this.isEnglish);
      if (!this.isPunctLocked) {
        this.provider.getIME().setOption("ascii_punct", this.isEnglish);
      }
      // 切换到英文模式时，取消当前组词（清除候选词列表）
      // 当 panel.editing=true 时有活跃组词，切换英文后候选词面板不会自动消失，
      // 且由于 ascii_mode=true 后续按键不再走 RIME 流程，导致候选词列表悬空无法控制。
      // 此处发送 Escape 取消组词，与 rimeManager.js setupShiftToggle 的处理一致。
      if (this.isEnglish) {
        const panel = this.provider.getPanel();
        if (panel && panel.editing) {
          panel.handleKey('{Escape}');
        }
      }
      this.refreshUI();
    });
    this.btnVariant.addEventListener("click", () => {
      if (this.isEnglish) return;
      this.isSimplification = !this.isSimplification;
      this.provider.getIME().setOption("simplification", this.isSimplification);
      this.refreshUI();
    });
    this.btnWidth.addEventListener("click", () => {
      this.isFullWidth = !this.isFullWidth;
      this.provider.getIME().setOption("full_shape", this.isFullWidth);
      this.refreshUI();
    });
    this.btnPunct.addEventListener("click", () => {
      if (this.punctClickTimer !== null) {
        clearTimeout(this.punctClickTimer);
        this.punctClickTimer = null;
        this.isPunctLocked = !this.isPunctLocked;
        this.provider.getIME().punctLocked = this.isPunctLocked;
        this.refreshUI();
        return;
      }
      this.punctClickTimer = window.setTimeout(() => {
        this.punctClickTimer = null;
        this.isEnglishPunct = !this.isEnglishPunct;
        this.provider.getIME().setOption("ascii_punct", this.isEnglishPunct);
        this.refreshUI();
      }, 250);
    });
    this.btnSettings.addEventListener("click", (e) => {
      e.stopPropagation();
      this.toggleSettings();
    });
    this.emojiToggle.addEventListener("click", () => {
      this.isEmoji = !this.isEmoji;
      this.provider.getIME().setOption("emoji_suggestion", this.isEmoji);
      this.refreshUI();
    });
    this.tbOpacitySlider.addEventListener("input", () => {
      this.tbOpacity = parseFloat(this.tbOpacitySlider.value);
      this.el.style.opacity = String(this.tbOpacity);
      this.saveOpacity();
    });
    if (this.kbOpacitySlider && this.kbOpacityRow) {
      const kbSlider = this.kbOpacitySlider;
      kbSlider.addEventListener("input", () => {
        this.kbOpacity = parseFloat(kbSlider.value);
        this.saveOpacity();
      });
    }
    this._outsideClickHandler = (e) => {
      if (!this.settingsOpen) return;
      const target = e.target;
      if (this.settingsPanel.contains(target) || this.btnSettings.contains(target)) return;
      this.closeSettings();
    };
    document.addEventListener("mousedown", this._outsideClickHandler, true);
  }
  /** 创建设置面板：emoji 开关 + 工具栏透明度 + 键盘透明度（移动端） */
  createSettingsPanel() {
    const panel = document.createElement("div");
    panel.className = "rime-tb-settings-panel";
    panel.style.display = "none";
    const emojiRow = document.createElement("div");
    emojiRow.className = "rime-tb-set-row";
    const emojiLabel = document.createElement("span");
    emojiLabel.className = "rime-tb-set-label";
    emojiLabel.textContent = "Emoji";
    const emojiToggle = document.createElement("button");
    emojiToggle.className = "rime-tb-toggle";
    emojiToggle.type = "button";
    emojiToggle.textContent = "Emoji: \u5173";
    emojiRow.appendChild(emojiLabel);
    emojiRow.appendChild(emojiToggle);
    panel.appendChild(emojiRow);
    const tbRow = document.createElement("div");
    tbRow.className = "rime-tb-set-row";
    const tbLabel = document.createElement("span");
    tbLabel.className = "rime-tb-set-label";
    tbLabel.textContent = "\u5DE5\u5177\u680F";
    const tbSlider = document.createElement("input");
    tbSlider.className = "rime-tb-slider";
    tbSlider.type = "range";
    tbSlider.min = "0.3";
    tbSlider.max = "1";
    tbSlider.step = "0.05";
    tbSlider.value = "1";
    tbRow.appendChild(tbLabel);
    tbRow.appendChild(tbSlider);
    panel.appendChild(tbRow);
    let kbRow = null;
    let kbSlider = null;
    if (this.keyboardEl) {
      kbRow = document.createElement("div");
      kbRow.className = "rime-tb-set-row";
      const kbLabel = document.createElement("span");
      kbLabel.className = "rime-tb-set-label";
      kbLabel.textContent = "\u952E\u76D8";
      kbSlider = document.createElement("input");
      kbSlider.className = "rime-tb-slider";
      kbSlider.type = "range";
      kbSlider.min = "0.3";
      kbSlider.max = "1";
      kbSlider.step = "0.05";
      kbSlider.value = "1";
      kbRow.appendChild(kbLabel);
      kbRow.appendChild(kbSlider);
      panel.appendChild(kbRow);
    }
    return { panel, emojiToggle, tbOpacitySlider: tbSlider, kbOpacityRow: kbRow, kbOpacitySlider: kbSlider };
  }
  loadOpacity() {
    try {
      const tb = localStorage.getItem("pty_rime_tb_opacity");
      const kb = localStorage.getItem("pty_rime_kb_opacity");
      if (tb) this.tbOpacity = Math.max(0.3, Math.min(1, parseFloat(tb) || 1));
      if (kb) this.kbOpacity = Math.max(0.3, Math.min(1, parseFloat(kb) || 1));
    } catch (_) {
    }
  }
  applyOpacity() {
    this.el.style.opacity = String(this.tbOpacity);
    this.tbOpacitySlider.value = String(this.tbOpacity);
    if (this.kbOpacitySlider) this.kbOpacitySlider.value = String(this.kbOpacity);
  }
  saveOpacity() {
    try {
      localStorage.setItem("pty_rime_tb_opacity", String(this.tbOpacity));
      localStorage.setItem("pty_rime_kb_opacity", String(this.kbOpacity));
    } catch (_) {
    }
  }
  loadPosition() {
    try {
      const raw = localStorage.getItem("pty_rime_tb_pos");
      if (!raw) return null;
      const pos = JSON.parse(raw);
      if (typeof pos.rx === "number" && typeof pos.ry === "number") return pos;
      return null;
    } catch (_) {
      return null;
    }
  }
  savePosition() {
    try {
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      if (vw <= 0 || vh <= 0) return;
      const rect = this.el.getBoundingClientRect();
      const rx = rect.left / vw;
      const ry = rect.top / vh;
      localStorage.setItem("pty_rime_tb_pos", JSON.stringify({ rx, ry }));
    } catch (_) {
    }
  }
  toggleSettings() {
    this.settingsOpen ? this.closeSettings() : this.openSettings();
  }
  openSettings() {
    if (this.destroyed) return;
    this.settingsOpen = true;
    this.settingsPanel.style.display = "";
    this.btnSettings.classList.add("rime-tb-active");
  }
  closeSettings() {
    this.settingsOpen = false;
    this.settingsPanel.style.display = "none";
    this.btnSettings.classList.remove("rime-tb-active");
  }
  bindIME() {
    const ime = this.provider.getIME();
    ime.onOptionChange((opts) => {
      if ("ascii_mode" in opts) this.isEnglish = opts.ascii_mode;
      if ("full_shape" in opts) this.isFullWidth = opts.full_shape;
      if ("ascii_punct" in opts) this.isEnglishPunct = opts.ascii_punct;
      if ("emoji_suggestion" in opts) this.isEmoji = opts.emoji_suggestion;
      if ("simplification" in opts) this.isSimplification = opts.simplification;
      this.refreshUI();
    });
    ime.onSchemaChange(() => {
      this.updateState();
    });
  }
  bindDrag() {
    this.dragHandle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      this.startDrag(e.clientX, e.clientY);
    });
    this.dragHandle.addEventListener("touchstart", (e) => {
      if (e.touches.length !== 1) return;
      e.preventDefault();
      const t = e.touches[0];
      this.startDrag(t.clientX, t.clientY);
    });
    this._dragMoveHandler = (e) => {
      if (!this.dragging) return;
      this.moveDrag(e.clientX, e.clientY);
    };
    document.addEventListener("mousemove", this._dragMoveHandler);
    this._dragTouchMoveHandler = (e) => {
      if (!this.dragging || e.touches.length !== 1) return;
      const t = e.touches[0];
      this.moveDrag(t.clientX, t.clientY);
    };
    document.addEventListener("touchmove", this._dragTouchMoveHandler);
    this._dragEndHandler = () => {
      this.dragging = false;
      this.savePosition();
    };
    document.addEventListener("mouseup", this._dragEndHandler);
    document.addEventListener("touchend", this._dragEndHandler);
  }
  startDrag(cx, cy) {
    this.dragging = true;
    this.dragX = cx;
    this.dragY = cy;
    const rect = this.el.getBoundingClientRect();
    this.posX = rect.left;
    this.posY = rect.top;
    this.el.style.position = "fixed";
  }
  moveDrag(cx, cy) {
    this.posX += cx - this.dragX;
    this.posY += cy - this.dragY;
    this.dragX = cx;
    this.dragY = cy;
    this.clampPosition();
    this.el.style.left = this.posX + "px";
    this.el.style.top = this.posY + "px";
  }
  clampPosition() {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const rect = this.el.getBoundingClientRect();
    const w = rect.width || 200;
    const h = rect.height || 40;
    if (this.posX < 0) this.posX = 0;
    if (this.posY < 0) this.posY = 0;
    if (this.posX + w > vw) this.posX = vw - w;
    if (this.posY + h > vh) this.posY = vh - h;
  }
  injectStyle() {
    if (document.getElementById(_RimeToolbar.STYLE_ID)) {
      _RimeToolbar.instanceCount++;
      return;
    }
    const s = document.createElement("style");
    s.id = _RimeToolbar.STYLE_ID;
    s.textContent = `
.rime-toolbar{display:inline-flex;align-items:center;gap:3px;padding:4px 8px;background:var(--rime-tb-bg,rgba(24,24,28,.96));border:1px solid var(--rime-tb-border,rgba(255,255,255,.08));border-radius:6px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;user-select:none;filter:drop-shadow(0 2px 8px rgba(0,0,0,.45));z-index:100002;touch-action:none}
.rime-tb-drag{cursor:grab;padding:2px 4px;color:var(--rime-tb-drag-color,rgba(255,255,255,.25));font-size:16px;line-height:1;letter-spacing:1px;font-weight:700}
.rime-tb-drag:active{cursor:grabbing}
.rime-tb-btn{background:transparent;border:none;color:var(--rime-tb-btn-color,rgba(255,255,255,.45));padding:3px 10px;border-radius:3px;cursor:pointer;font-size:13px;line-height:1.6;transition:all .1s;white-space:nowrap;min-width:34px;text-align:center;-webkit-tap-highlight-color:transparent}
.rime-tb-btn:hover{color:var(--rime-tb-btn-hover-color,rgba(255,255,255,.82));background:var(--rime-tb-btn-hover-bg,rgba(255,255,255,.06))}
.rime-tb-btn.rime-tb-active{color:var(--rime-tb-btn-active-color,#70c0e8);background:var(--rime-tb-btn-active-bg,rgba(112,192,232,.1))}
.rime-tb-btn.rime-tb-locked{box-shadow:var(--rime-tb-btn-locked-shadow,inset 0 0 0 2px #f0a020)}
.rime-tb-btn:disabled{opacity:var(--rime-tb-btn-disabled-opacity,.3);cursor:default;background:transparent;pointer-events:none}
.rime-tb-settings-panel{position:absolute;top:calc(100% + 4px);right:0;min-width:180px;padding:8px 10px;background:var(--rime-tb-settings-bg,rgba(24,24,28,.98));border:1px solid var(--rime-tb-settings-border,rgba(255,255,255,.1));border-radius:6px;box-shadow:var(--rime-tb-settings-shadow,0 4px 16px rgba(0,0,0,.5));z-index:100003;display:flex;flex-direction:column;gap:8px;font-size:12px;color:var(--rime-tb-settings-color,rgba(255,255,255,.82))}
.rime-tb-set-row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.rime-tb-set-label{color:var(--rime-tb-settings-label-color,rgba(255,255,255,.7));font-size:12px;white-space:nowrap}
.rime-tb-toggle{background:var(--rime-tb-toggle-bg,rgba(255,255,255,.06));border:1px solid var(--rime-tb-toggle-border,rgba(255,255,255,.12));color:var(--rime-tb-toggle-color,rgba(255,255,255,.7));padding:3px 8px;border-radius:3px;cursor:pointer;font-size:11px;line-height:1.4;-webkit-tap-highlight-color:transparent;transition:all .1s}
.rime-tb-toggle:hover{color:var(--rime-tb-toggle-hover-color,rgba(255,255,255,.9));background:var(--rime-tb-toggle-hover-bg,rgba(255,255,255,.1))}
.rime-tb-toggle-on{color:var(--rime-tb-toggle-on-color,#70c0e8);background:var(--rime-tb-toggle-on-bg,rgba(112,192,232,.15));border-color:var(--rime-tb-toggle-on-border,rgba(112,192,232,.4))}
.rime-tb-slider{flex:1;min-width:80px;cursor:pointer;-webkit-appearance:none;appearance:none;height:4px;background:var(--rime-tb-slider-bg,rgba(255,255,255,.15));border-radius:2px;outline:none}
.rime-tb-slider::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:14px;height:14px;border-radius:50%;background:var(--rime-tb-slider-thumb-bg,#70c0e8);cursor:pointer;border:none}
.rime-tb-slider::-moz-range-thumb{width:14px;height:14px;border-radius:50%;background:var(--rime-tb-slider-thumb-bg,#70c0e8);cursor:pointer;border:none}
`;
    document.head.appendChild(s);
    _RimeToolbar.instanceCount++;
  }
};
_RimeToolbar.STYLE_ID = "rime-toolbar-style";
_RimeToolbar.instanceCount = 0;
let RimeToolbar = _RimeToolbar;

;// ./src/keyboard/layouts.ts

const LETTERS_LAYOUT = [
  // 第一行: q w e r t y u i o p
  [
    { key: "q", label: "q", shiftKey: "Q", shiftLabel: "Q", alt: ["1"] },
    { key: "w", label: "w", shiftKey: "W", shiftLabel: "W", alt: ["2"] },
    { key: "e", label: "e", shiftKey: "E", shiftLabel: "E", alt: ["3"] },
    { key: "r", label: "r", shiftKey: "R", shiftLabel: "R", alt: ["4"] },
    { key: "t", label: "t", shiftKey: "T", shiftLabel: "T", alt: ["5"] },
    { key: "y", label: "y", shiftKey: "Y", shiftLabel: "Y", alt: ["6"] },
    { key: "u", label: "u", shiftKey: "U", shiftLabel: "U", alt: ["7"] },
    { key: "i", label: "i", shiftKey: "I", shiftLabel: "I", alt: ["8"] },
    { key: "o", label: "o", shiftKey: "O", shiftLabel: "O", alt: ["9"] },
    { key: "p", label: "p", shiftKey: "P", shiftLabel: "P", alt: ["0"] }
  ],
  // 第二行: a s d f g h j k l
  [
    { key: "a", label: "a", shiftKey: "A", shiftLabel: "A", alt: ["1"] },
    { key: "s", label: "s", shiftKey: "S", shiftLabel: "S", alt: ["2"] },
    { key: "d", label: "d", shiftKey: "D", shiftLabel: "D", alt: ["3"] },
    { key: "f", label: "f", shiftKey: "F", shiftLabel: "F", alt: ["4"] },
    { key: "g", label: "g", shiftKey: "G", shiftLabel: "G", alt: ["5"] },
    { key: "h", label: "h", shiftKey: "H", shiftLabel: "H", alt: ["6"] },
    { key: "j", label: "j", shiftKey: "J", shiftLabel: "J", alt: ["7"] },
    { key: "k", label: "k", shiftKey: "K", shiftLabel: "K", alt: ["8"] },
    { key: "l", label: "l", shiftKey: "L", shiftLabel: "L", alt: ["9"] }
  ],
  // 第三行: Shift z x c v b n m Backspace
  [
    { key: "Shift", label: "\u21E7", action: "shift", width: 1.5 },
    { key: "z", label: "z", shiftKey: "Z", shiftLabel: "Z" },
    { key: "x", label: "x", shiftKey: "X", shiftLabel: "X" },
    { key: "c", label: "c", shiftKey: "C", shiftLabel: "C" },
    { key: "v", label: "v", shiftKey: "V", shiftLabel: "V" },
    { key: "b", label: "b", shiftKey: "B", shiftLabel: "B" },
    { key: "n", label: "n", shiftKey: "N", shiftLabel: "N" },
    { key: "m", label: "m", shiftKey: "M", shiftLabel: "M" },
    { key: "BackSpace", label: "\u232B", action: "backspace", width: 1.5 }
  ],
  // 第四行: ?123  中/En  空格  标点  回车
  [
    { key: "?123", label: "?123", action: "page", page: "numbers", width: 1.5 },
    { key: "lang", label: "\u4E2D", action: "lang", width: 1.5 },
    { key: "space", label: "\u7A7A\u683C", action: "space", width: 4 },
    { key: "punct", label: "\u3002", action: "punct" },
    { key: "Return", label: "\u21B5", action: "enter", width: 1.5 }
  ]
];
const NUMBERS_LAYOUT = [
  // 第一行: 数字
  [
    { key: "1", label: "1", alt: ["!", "\u2460"] },
    { key: "2", label: "2", alt: ["@", "\u2461"] },
    { key: "3", label: "3", alt: ["#", "\u2462"] },
    { key: "4", label: "4", alt: ["$", "\u2463"] },
    { key: "5", label: "5", alt: ["%", "\u2464"] },
    { key: "6", label: "6", alt: ["^", "\u2465"] },
    { key: "7", label: "7", alt: ["&", "\u2466"] },
    { key: "8", label: "8", alt: ["*", "\u2467"] },
    { key: "9", label: "9", alt: ["(", "\u2468"] },
    { key: "0", label: "0", alt: [")", "\u2469"] }
  ],
  // 第二行: 常用符号
  [
    { key: "-", label: "-", alt: ["_"], cnLabel: "\uFF0D" },
    { key: "/", label: "/", cnLabel: "\uFF0F" },
    { key: ":", label: ":", alt: ["\uFF1B"], cnLabel: "\uFF1A" },
    { key: ";", label: ";", cnLabel: "\uFF1B" },
    { key: "(", label: "(", cnLabel: "\uFF08" },
    { key: ")", label: ")", cnLabel: "\uFF09" },
    { key: "$", label: "$", alt: ["\uFFE5"] },
    { key: "&", label: "&" },
    { key: "@", label: "@" },
    { key: '"', label: '"', alt: ["'"], cnLabel: "\uFF02" }
  ],
  // 第三行: 更多符号 + 退格
  [
    { key: ".", label: ".", alt: ["\u3002", "\u2026"], cnLabel: "\u3002" },
    { key: ",", label: ",", alt: ["\uFF0C"], cnLabel: "\uFF0C" },
    { key: "?", label: "?", alt: ["\uFF1F"], cnLabel: "\uFF1F" },
    { key: "!", label: "!", alt: ["\uFF01"], cnLabel: "\uFF01" },
    { key: "'", label: "'", cnLabel: "\uFF07" },
    { key: '"', label: '"', alt: ["\u300C", "\u300D"], cnLabel: "\uFF02" },
    { key: "~", label: "~" },
    { key: "_", label: "_", cnLabel: "\uFF3F" },
    { key: "BackSpace", label: "\u232B", action: "backspace", width: 1.5 }
  ],
  // 第四行: 功能行 (与字母页一致)
  [
    { key: "ABC", label: "ABC", action: "page", page: "letters", width: 1.5 },
    { key: "lang", label: "\u4E2D", action: "lang", width: 1.5 },
    { key: "space", label: "\u7A7A\u683C", action: "space", width: 4 },
    { key: "punct", label: "\u3002", action: "punct" },
    { key: "Return", label: "\u21B5", action: "enter", width: 1.5 }
  ]
];
const SYMBOLS_LAYOUT = [
  // 第一行: 方括号类 + 数学符号
  [
    { key: "[", label: "[" },
    { key: "]", label: "]" },
    { key: "{", label: "{" },
    { key: "}", label: "}" },
    { key: "#", label: "#" },
    { key: "%", label: "%" },
    { key: "^", label: "^" },
    { key: "*", label: "*" },
    { key: "+", label: "+" },
    { key: "=", label: "=" }
  ],
  // 第二行: 下划线 + 管道 + 特殊符号
  [
    { key: "_", label: "_" },
    { key: "\\", label: "\\" },
    { key: "|", label: "|" },
    { key: "~", label: "~" },
    { key: "<", label: "<" },
    { key: ">", label: ">" },
    { key: "$", label: "$" },
    { key: "\u20AC", label: "\u20AC" },
    { key: "\xA3", label: "\xA3" },
    { key: "\u2022", label: "\u2022" }
  ],
  // 第三行: 标点 + 退格
  [
    { key: "`", label: "`" },
    { key: "\xB7", label: "\xB7" },
    { key: "\u2026", label: "\u2026" },
    { key: "\u2014", label: "\u2014" },
    { key: "\u300C", label: "\u300C" },
    { key: "\u300D", label: "\u300D" },
    { key: "\u300A", label: "\u300A" },
    { key: "\u300B", label: "\u300B" },
    { key: "BackSpace", label: "\u232B", action: "backspace", width: 1.5 }
  ],
  // 第四行: 功能行
  [
    { key: "ABC", label: "ABC", action: "page", page: "letters", width: 1.5 },
    { key: "#+=", label: "?123", action: "page", page: "numbers", width: 1.5 },
    { key: "space", label: "\u7A7A\u683C", action: "space", width: 4 },
    { key: "punct", label: "\u3002", action: "punct" },
    { key: "Return", label: "\u21B5", action: "enter", width: 1.5 }
  ]
];
function getLayout(page) {
  switch (page) {
    case "letters":
      return LETTERS_LAYOUT;
    case "numbers":
      return NUMBERS_LAYOUT;
    case "symbols":
      return SYMBOLS_LAYOUT;
  }
}
function getPageSwitchLabel(currentPage) {
  switch (currentPage) {
    case "letters":
      return "?123";
    case "numbers":
      return "#+=";
    case "symbols":
      return "ABC";
  }
}
function getPageSwitchTarget(currentPage) {
  switch (currentPage) {
    case "letters":
      return "numbers";
    case "numbers":
      return "symbols";
    case "symbols":
      return "letters";
  }
}

;// ./src/keyboard/dom.ts

function createKeyboardDOM() {
  const container = div("rime-kb");
  const toolbar = createToolbar();
  const compBar = createCompBar();
  const { candBar, cands, candNav } = createCandBar();
  const keys = div("rime-kb-keys");
  const safe = div("rime-kb-safe");
  const preview = div("rime-kb-preview");
  preview.style.display = "none";
  const alt = div("rime-kb-alt");
  alt.style.display = "none";
  container.appendChild(toolbar);
  container.appendChild(compBar);
  container.appendChild(candBar);
  container.appendChild(keys);
  container.appendChild(safe);
  const hideBtn = toolbar.querySelector(".rime-kb-tb-hide");
  return {
    container,
    toolbar,
    compBar,
    candBar,
    cands,
    candNav,
    keys,
    safe,
    preview,
    alt,
    dragHandle: toolbar,
    hideBtn
  };
}
function div(cls) {
  const el = document.createElement("div");
  el.className = cls;
  return el;
}
function createToolbar() {
  const el = div("rime-kb-toolbar");
  el.innerHTML = `
    <div class="rime-kb-tb-drag">\u2261</div>
    <button class="rime-kb-tb-hide" type="button" title="\u9690\u85CF\u952E\u76D8">\u25BC</button>
  `;
  return el;
}
function createCompBar() {
  const el = div("rime-kb-compbar");
  return el;
}
function createCandBar() {
  const candBar = div("rime-kb-candbar");
  const cands = div("rime-kb-cands");
  const candNav = div("rime-kb-cand-nav");
  candBar.appendChild(cands);
  candBar.appendChild(candNav);
  return { candBar, cands, candNav };
}

;// ./src/keyboard/render.ts


function toFullWidth(s) {
  let out = "";
  for (const ch of s) {
    const code = ch.codePointAt(0);
    if (code === 32) out += "\u3000";
    else if (code >= 33 && code <= 126) out += String.fromCharCode(code + 65248);
    else out += ch;
  }
  return out;
}
const FULLWIDTH_PUNCT_MAP = {
  ".": "\u3002",
  ",": "\uFF0C",
  "?": "\uFF1F",
  "!": "\uFF01",
  ":": "\uFF1A",
  ";": "\uFF1B",
  "(": "\uFF08",
  ")": "\uFF09",
  "[": "\u3010",
  "]": "\u3011",
  "-": "\u2014",
  "$": "\uFFE5",
  "\\": "\u3001",
  "~": "\xB7"
};
function renderKeys(keysEl, page, shiftState, isEnglish, isEnglishPunct, isFullWidth) {
  const layout = getLayout(page);
  keysEl.innerHTML = "";
  for (const row of layout) {
    const rowEl = document.createElement("div");
    rowEl.className = "rime-kb-row";
    for (const keyDef of row) {
      rowEl.appendChild(createKeyEl(keyDef, shiftState, isEnglish, isEnglishPunct, isFullWidth));
    }
    keysEl.appendChild(rowEl);
  }
}
function createKeyEl(keyDef, shiftState, isEnglish, isEnglishPunct, isFullWidth) {
  const el = document.createElement("button");
  el.className = keyClass(keyDef);
  el.dataset.key = keyDef.key;
  if (keyDef.action) el.dataset.action = keyDef.action;
  if (keyDef.width) el.style.flex = String(keyDef.width);
  el.textContent = keyLabel(keyDef, shiftState, isEnglish, isEnglishPunct, isFullWidth);
  return el;
}
function keyClass(keyDef) {
  let cls = "rime-kb-key";
  if (keyDef.action) {
    cls += " rime-kb-key-fn";
    if (keyDef.action === "space") cls += " rime-kb-key-space";
    if (keyDef.action === "shift") cls += " rime-kb-key-shift";
  }
  return cls;
}
function keyLabel(keyDef, shiftState, isEnglish, isEnglishPunct, isFullWidth) {
  if (keyDef.action === "shift") return shiftState === "locked" ? "\u21EA" : "\u21E7";
  if (keyDef.action === "lang") return isEnglish ? "En" : "\u4E2D";
  if (keyDef.action === "punct") {
    const useCnPunct2 = isFullWidth || !isEnglishPunct;
    return useCnPunct2 ? "\u3002" : ".";
  }
  if (keyDef.action) return keyDef.label;
  let s;
  if (shiftState !== "off" && keyDef.shiftLabel) s = keyDef.shiftLabel;
  else s = keyDef.label;
  const useCnPunct = isFullWidth || !isEnglishPunct;
  if (useCnPunct) {
    const mapped = FULLWIDTH_PUNCT_MAP[s];
    if (mapped) return mapped;
    if (s === "'") return "\u2018";
    if (s === '"') return "\u201C";
  }
  if (isFullWidth) return toFullWidth(s);
  return s;
}
function renderCompBar(compBar, r) {
  if (!r || !r.composition) {
    compBar.innerHTML = "";
    compBar.classList.remove("rime-kb-compbar-visible");
    return;
  }
  const comp = r.composition;
  const head = comp.head ?? "";
  const body = comp.body ?? "";
  const tail = comp.tail ?? "";
  if (!head && !body && !tail) {
    compBar.innerHTML = "";
    compBar.classList.remove("rime-kb-compbar-visible");
    return;
  }
  compBar.classList.add("rime-kb-compbar-visible");
  compBar.innerHTML = `<span class="rime-kb-comp-h">${esc(head)}</span><span class="rime-kb-comp-b">${esc(body)}</span><span class="rime-kb-comp-t">${esc(tail)}</span>`;
}
function renderCandBar(dom, r) {
  if (!r || !r.candidates?.length) {
    dom.cands.innerHTML = "";
    dom.candNav.innerHTML = "";
    dom.candBar.classList.remove("rime-kb-candbar-visible");
    return;
  }
  dom.candBar.classList.add("rime-kb-candbar-visible");
  const cands = r.candidates || [];
  const labels = r.selectLabels || [];
  const hl = r.highlighted ?? 0;
  dom.cands.innerHTML = cands.map(
    (c, i) => `<span class="rime-kb-cand${i === hl ? " rime-kb-cand-hl" : ""}" data-idx="${i}"><span class="rime-kb-cand-lb">${esc(labels[i] || String(i + 1))}</span>` + esc(c.text) + "</span>"
  ).join("");
  const page = r.page || 1;
  const isLast = r.isLastPage;
  dom.candNav.innerHTML = `<button class="rime-kb-cand-nav-btn" data-dir="prev"${page <= 1 ? " disabled" : ""}>\u25C0</button><span class="rime-kb-cand-page">${page}</span><button class="rime-kb-cand-nav-btn" data-dir="next"${isLast ? " disabled" : ""}>\u25B6</button>`;
}
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

;// ./src/keyboard/types.ts

const PUNCT_ALT = ["\uFF0C", "\uFF01", "\uFF1F", "\u3001", "\uFF1A", "\uFF1B", "\u2026", "\u2014", "\xB7"];
const STYLE_ID = "rime-keyboard-style";

;// ./src/keyboard/touch.ts


class KeyboardTouchHandler {
  constructor(keysEl, containerEl, previewEl, altEl, callbacks) {
    // 触摸状态
    this.activeKeyEl = null;
    this.activeKeyDef = null;
    this.longPressTimer = null;
    this.backspaceTimer = null;
    this.altVisible = false;
    this.altSelectedIndex = -1;
    this.destroyed = false;
    /** 标记触摸在键盘上（供外部 blur 处理使用） */
    this.keyTouched = false;
    this.keysEl = keysEl;
    this.containerEl = containerEl;
    this.previewEl = previewEl;
    this.altEl = altEl;
    this.cb = callbacks;
    this.boundTouchStart = (e) => this.onTouchStart(e);
    this.boundTouchMove = (e) => this.onTouchMove(e);
    this.boundTouchEnd = (e) => this.onTouchEnd(e);
    this.boundMouseDown = (e) => this.onMouseDown(e);
    this.boundMouseMove = (e) => this.onMouseMove(e);
    this.boundMouseUp = (e) => this.onMouseUp(e);
    this.boundContainerTouchStart = (e) => this.onContainerTouchStart(e);
    this.boundContainerMouseDown = (e) => this.onContainerMouseDown(e);
  }
  /** 绑定事件 */
  bind() {
    this.keysEl.addEventListener("touchstart", this.boundTouchStart, { passive: false });
    this.keysEl.addEventListener("touchmove", this.boundTouchMove, { passive: false });
    this.keysEl.addEventListener("touchend", this.boundTouchEnd, { passive: false });
    this.keysEl.addEventListener("touchcancel", this.boundTouchEnd, { passive: false });
    this.keysEl.addEventListener("mousedown", this.boundMouseDown);
    document.addEventListener("mousemove", this.boundMouseMove);
    document.addEventListener("mouseup", this.boundMouseUp);
    this.containerEl.addEventListener("touchmove", (e) => {
      e.preventDefault();
    }, { passive: false });
    this.containerEl.addEventListener("touchstart", this.boundContainerTouchStart, { passive: true });
    this.containerEl.addEventListener("mousedown", this.boundContainerMouseDown);
  }
  /** 销毁，移除所有事件 */
  destroy() {
    this.destroyed = true;
    this.clearTimers();
    this.keysEl.removeEventListener("touchstart", this.boundTouchStart);
    this.keysEl.removeEventListener("touchmove", this.boundTouchMove);
    this.keysEl.removeEventListener("touchend", this.boundTouchEnd);
    this.keysEl.removeEventListener("touchcancel", this.boundTouchEnd);
    this.keysEl.removeEventListener("mousedown", this.boundMouseDown);
    document.removeEventListener("mousemove", this.boundMouseMove);
    document.removeEventListener("mouseup", this.boundMouseUp);
    this.containerEl.removeEventListener("touchstart", this.boundContainerTouchStart);
    this.containerEl.removeEventListener("mousedown", this.boundContainerMouseDown);
  }
  // ─── 触摸事件 ───
  onTouchStart(e) {
    e.preventDefault();
    this.keyTouched = true;
    this.handlePointerDown(e.touches[0].clientX, e.touches[0].clientY);
  }
  onTouchMove(e) {
    e.preventDefault();
    if (!this.activeKeyEl) return;
    this.handlePointerMove(e.touches[0].clientX, e.touches[0].clientY);
  }
  onTouchEnd(e) {
    e.preventDefault();
    this.handlePointerUp();
    setTimeout(() => {
      this.keyTouched = false;
    }, 200);
  }
  onMouseDown(e) {
    e.preventDefault();
    this.keyTouched = true;
    this.handlePointerDown(e.clientX, e.clientY);
  }
  onMouseMove(e) {
    if (!this.activeKeyEl) return;
    this.handlePointerMove(e.clientX, e.clientY);
  }
  onMouseUp(_e) {
    this.handlePointerUp();
    setTimeout(() => {
      this.keyTouched = false;
    }, 200);
  }
  // ─── 容器级触摸标记（防止工具栏/候选栏点击导致键盘隐藏）───
  onContainerTouchStart(_e) {
    this.keyTouched = true;
    setTimeout(() => {
      this.keyTouched = false;
    }, 400);
  }
  onContainerMouseDown(_e) {
    this.keyTouched = true;
    setTimeout(() => {
      this.keyTouched = false;
    }, 400);
  }
  // ─── 统一指针处理 ───
  handlePointerDown(cx, cy) {
    const keyEl = this.getKeyAtPoint(cx, cy);
    if (!keyEl) return;
    this.activeKeyEl = keyEl;
    this.activeKeyDef = this.cb.getKeyDef(keyEl.dataset.key || "");
    keyEl.classList.add("rime-kb-key-active");
    this.showPreview(keyEl);
    this.cb.haptic();
    this.startLongPress(keyEl, this.activeKeyDef);
  }
  handlePointerMove(cx, cy) {
    const keyEl = this.getKeyAtPoint(cx, cy);
    if (keyEl !== this.activeKeyEl) {
      if (this.activeKeyEl) this.activeKeyEl.classList.remove("rime-kb-key-active");
      this.hidePreview();
      this.cancelLongPress();
      if (keyEl) {
        this.activeKeyEl = keyEl;
        this.activeKeyDef = this.cb.getKeyDef(keyEl.dataset.key || "");
        keyEl.classList.add("rime-kb-key-active");
        this.showPreview(keyEl);
        this.startLongPress(keyEl, this.activeKeyDef);
      } else {
        this.activeKeyEl = null;
        this.activeKeyDef = null;
      }
    }
    if (this.altVisible) this.handleAltMove(cx, cy);
  }
  handlePointerUp() {
    this.clearTimers();
    if (this.altVisible && this.altSelectedIndex >= 0) {
      this.selectAltItem(this.altSelectedIndex);
    } else if (this.activeKeyDef && this.activeKeyEl) {
      this.cb.fireKey(this.activeKeyDef);
    }
    if (this.activeKeyEl) this.activeKeyEl.classList.remove("rime-kb-key-active");
    this.hidePreview();
    this.hideAlt();
    this.activeKeyEl = null;
    this.activeKeyDef = null;
  }
  // ─── 按键定位 ───
  /** 获取坐标下的按键元素 */
  getKeyAtPoint(cx, cy) {
    const pv = this.previewEl.style.display;
    const al = this.altEl.style.display;
    this.previewEl.style.display = "none";
    this.altEl.style.display = "none";
    const el = document.elementFromPoint(cx, cy);
    this.previewEl.style.display = pv;
    this.altEl.style.display = al;
    if (!el) return null;
    return el.closest(".rime-kb-key") || null;
  }
  // ─── 长按 ───
  startLongPress(keyEl, keyDef) {
    this.cancelLongPress();
    if (!keyDef) return;
    if (keyDef.action === "backspace") {
      this.backspaceTimer = setTimeout(() => this.startBackspaceRepeat(), 500);
      return;
    }
    const altItems = this.getAltItems(keyDef);
    if (altItems.length === 0) return;
    this.longPressTimer = setTimeout(() => {
      this.showAlt(keyEl, altItems);
    }, 500);
  }
  cancelLongPress() {
    if (this.longPressTimer !== null) {
      clearTimeout(this.longPressTimer);
      this.longPressTimer = null;
    }
  }
  getAltItems(keyDef) {
    if (keyDef.action === "punct") return PUNCT_ALT;
    return keyDef.alt ?? [];
  }
  // ─── 预览气泡 ───
  showPreview(keyEl) {
    if (this.altVisible) return;
    const label = keyEl.textContent || "";
    const rect = keyEl.getBoundingClientRect();
    this.previewEl.textContent = label;
    this.previewEl.style.display = "flex";
    const pw = this.previewEl.offsetWidth;
    const ph = this.previewEl.offsetHeight;
    let x = rect.left + rect.width / 2 - pw / 2;
    let y = rect.top - ph - 6;
    if (x < 4) x = 4;
    if (x + pw > window.innerWidth - 4) x = window.innerWidth - pw - 4;
    if (y < 4) y = rect.bottom + 6;
    this.previewEl.style.left = x + "px";
    this.previewEl.style.top = y + "px";
  }
  hidePreview() {
    this.previewEl.style.display = "none";
  }
  // ─── 长按候选弹出 ───
  showAlt(keyEl, items) {
    this.altVisible = true;
    this.altSelectedIndex = -1;
    this.hidePreview();
    this.altEl.innerHTML = items.map(
      (item, i) => `<span class="rime-kb-alt-item" data-idx="${i}">${touch_esc(item)}</span>`
    ).join("");
    this.altEl.style.display = "flex";
    const rect = keyEl.getBoundingClientRect();
    const aw = this.altEl.offsetWidth;
    let x = rect.left + rect.width / 2 - aw / 2;
    let y = rect.top - this.altEl.offsetHeight - 8;
    if (x < 4) x = 4;
    if (x + aw > window.innerWidth - 4) x = window.innerWidth - aw - 4;
    if (y < 4) y = rect.bottom + 8;
    this.altEl.style.left = x + "px";
    this.altEl.style.top = y + "px";
  }
  handleAltMove(cx, cy) {
    const items = this.altEl.querySelectorAll(".rime-kb-alt-item");
    let found = -1;
    items.forEach((item, i) => {
      const rect = item.getBoundingClientRect();
      if (cx >= rect.left && cx <= rect.right && cy >= rect.top && cy <= rect.bottom) found = i;
      item.classList.toggle("rime-kb-alt-item-active", i === found);
    });
    this.altSelectedIndex = found;
  }
  selectAltItem(index) {
    const items = this.altEl.querySelectorAll(".rime-kb-alt-item");
    if (index < 0 || index >= items.length) return;
    const text = items[index].textContent || "";
    this.cb.insertText(text);
    this.cb.haptic();
  }
  hideAlt() {
    this.altVisible = false;
    this.altSelectedIndex = -1;
    this.altEl.style.display = "none";
  }
  // ─── Backspace 自动重复 ───
  startBackspaceRepeat() {
    const repeat = () => {
      if (!this.activeKeyEl || this.activeKeyDef?.action !== "backspace") return;
      this.cb.fireKey(this.activeKeyDef);
      this.backspaceTimer = setTimeout(repeat, 80);
    };
    this.backspaceTimer = setTimeout(repeat, 80);
  }
  // ─── 工具 ───
  clearTimers() {
    this.cancelLongPress();
    if (this.backspaceTimer !== null) {
      clearTimeout(this.backspaceTimer);
      this.backspaceTimer = null;
    }
  }
}
function touch_esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

;// ./src/keyboard/viewport.ts

class KeyboardViewportController {
  constructor(container, target, dragHandle, getMode, getVisible, getKeyTouched, getShowOnFocus, onShow, onHide, floatingWidth = 320, floatingHeight = 220) {
    // 拖拽状态
    this.dragging = false;
    this.dragStartX = 0;
    this.dragStartY = 0;
    this.posX = 0;
    this.posY = 0;
    // docked 拉伸状态
    this.resizing = false;
    this.resizeStartY = 0;
    this.resizeStartHeight = 0;
    this.resizeMinHeight = 0;
    this.dockedHeight = 0;
    // 目标元素属性备份
    this.savedInputMode = null;
    this.savedAutoComplete = null;
    this.container = container;
    this.target = target;
    this.dragHandle = dragHandle;
    this.getMode = getMode;
    this.getVisible = getVisible;
    this.getKeyTouched = getKeyTouched;
    this.getShowOnFocus = getShowOnFocus;
    this.onShow = onShow;
    this.onHide = onHide;
    this.floatingWidth = floatingWidth;
    this.floatingHeight = floatingHeight;
    this.boundFocus = () => this.onTargetFocus();
    this.boundBlur = () => this.onTargetBlur();
    this.boundClick = () => this.onTargetClick();
    this.boundPointerDown = () => this.onTargetClick();
    this.boundResize = () => this.onResize();
    this.boundDragTouchStart = (e) => this.onDragTouchStart(e);
    this.boundDragTouchMove = (e) => this.onDragTouchMove(e);
    this.boundDragTouchEnd = () => {
      this.onDragEnd();
    };
    this.boundDragMouseStart = (e) => this.onDragMouseStart(e);
    this.boundDragMouseMove = (e) => this.onDragMouseMove(e);
    this.boundDragMouseEnd = () => {
      this.onDragEnd();
    };
  }
  /** 设置目标元素的 inputmode 以阻止系统键盘 */
  setupTarget() {
    if (this.isTextInput(this.target)) {
      this.savedInputMode = this.target.getAttribute("inputmode");
      this.savedAutoComplete = this.target.getAttribute("autocomplete");
      this.target.setAttribute("inputmode", "none");
      this.target.setAttribute("autocomplete", "off");
    }
  }
  /** 恢复目标元素属性 */
  restoreTarget() {
    if (this.isTextInput(this.target)) {
      if (this.savedInputMode !== null) this.target.setAttribute("inputmode", this.savedInputMode);
      else this.target.removeAttribute("inputmode");
      if (this.savedAutoComplete !== null) this.target.setAttribute("autocomplete", this.savedAutoComplete);
      else this.target.removeAttribute("autocomplete");
    }
  }
  /** 绑定所有事件 */
  bind() {
    this.target.addEventListener("focus", this.boundFocus);
    this.target.addEventListener("blur", this.boundBlur);
    this.target.addEventListener("click", this.boundClick);
    this.target.addEventListener("pointerdown", this.boundPointerDown);
    window.addEventListener("resize", this.boundResize);
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", this.boundResize);
      window.visualViewport.addEventListener("scroll", this.boundResize);
    }
    this.target.addEventListener("compositionstart", (e) => {
      e.preventDefault();
      try {
        this.target.value = this.target.value;
      } catch {
      }
    });
    this.dragHandle.addEventListener("touchstart", this.boundDragTouchStart, { passive: false });
    document.addEventListener("touchmove", this.boundDragTouchMove, { passive: false });
    document.addEventListener("touchend", this.boundDragTouchEnd);
    this.dragHandle.addEventListener("mousedown", this.boundDragMouseStart);
    document.addEventListener("mousemove", this.boundDragMouseMove);
    document.addEventListener("mouseup", this.boundDragMouseEnd);
  }
  /** 销毁，移除所有事件 */
  destroy() {
    this.target.removeEventListener("focus", this.boundFocus);
    this.target.removeEventListener("blur", this.boundBlur);
    this.target.removeEventListener("click", this.boundClick);
    this.target.removeEventListener("pointerdown", this.boundPointerDown);
    window.removeEventListener("resize", this.boundResize);
    if (window.visualViewport) {
      window.visualViewport.removeEventListener("resize", this.boundResize);
      window.visualViewport.removeEventListener("scroll", this.boundResize);
    }
    this.dragHandle.removeEventListener("touchstart", this.boundDragTouchStart);
    document.removeEventListener("touchmove", this.boundDragTouchMove);
    document.removeEventListener("touchend", this.boundDragTouchEnd);
    this.dragHandle.removeEventListener("mousedown", this.boundDragMouseStart);
    document.removeEventListener("mousemove", this.boundDragMouseMove);
    document.removeEventListener("mouseup", this.boundDragMouseEnd);
  }
  // ─── Show / Hide ───
  show() {
    this.container.classList.remove("rime-kb-hidden");
    if (this.getMode() === "docked") this.positionDocked();
    this.adjustViewport();
    this.onShow();
  }
  hide() {
    this.container.classList.add("rime-kb-hidden");
    this.onHide();
  }
  /** 聚焦目标元素 */
  focusTarget() {
    if (this.isTextInput(this.target) && document.activeElement !== this.target) {
      this.target.focus();
    }
  }
  // ─── Focus / Blur ───
  onTargetFocus() {
    if (!this.getShowOnFocus()) return;
    this.show();
  }
  /**
   * 已聚焦的输入框再次被点击（focus 不会二次触发）时，
   * 若键盘处于隐藏状态则重新弹起，对齐 native 移动端行为。
   */
  onTargetClick() {
    if (!this.getShowOnFocus()) return;
    this.show();
  }
  onTargetBlur() {
    if (this.getKeyTouched()) {
      this.focusTarget();
      return;
    }
    setTimeout(() => {
      if (document.activeElement !== this.target && !this.getKeyTouched()) {
        const active = document.activeElement;
        if (active && active.closest(".rime-toolbar")) {
          this.focusTarget();
          return;
        }
        this.hide();
      }
    }, 150);
  }
  // ─── 视口调整 ───
  onResize() {
    if (this.getVisible()) this.adjustViewport();
    this.adjustFloatingSize();
  }
  adjustFloatingSize() {
    if (this.getMode() !== "floating") return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const isLandscape = vw > vh;
    const maxW = isLandscape ? Math.min(vw * 0.6, 600) : Math.min(vw - 16, this.floatingWidth);
    const maxH = isLandscape ? Math.min(vh - 16, this.floatingHeight) : Math.min(vh * 0.5, 320);
    this.container.style.maxWidth = maxW + "px";
    this.container.style.maxHeight = maxH + "px";
  }
  adjustViewport() {
    if (!this.isTextInput(this.target)) return;
    this.positionDocked();
    requestAnimationFrame(() => {
      const targetRect = this.target.getBoundingClientRect();
      const kbTop = this.container.getBoundingClientRect().top;
      if (targetRect.bottom > kbTop - 10) {
        this.target.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    });
  }
  /** docked 模式定位：固定可视视口底部
   *
   * 移动端 position:fixed; bottom:0 相对布局视口（含浏览器 chrome 后方区域），
   * 键盘会被底部导航栏遮挡。用 visualViewport 计算可视视口底边与布局视口底边的
   * 偏移量作为 bottom 值，确保键盘紧贴可视区域底边。
   */
  positionDocked() {
    if (this.getMode() !== "docked") return;
    this.container.style.bottom = "";
    this.container.style.left = "0px";
    this.container.style.right = "0px";
    this.container.style.width = "100%";
    this.container.style.transform = "";
    const vv = window.visualViewport;
    const vh = vv ? vv.height : window.innerHeight;
    const offset = vv ? Math.max(0, window.innerHeight - vv.offsetTop - vv.height) : 0;
    const h = this.container.offsetHeight;
    this.container.style.top = vh - offset - h + "px";
  }
  // ─── 拖拽 / 拉伸 ───
  onDragTouchStart(e) {
    if (e.target.closest(".rime-kb-tb-hide")) return;
    e.preventDefault();
    e.stopPropagation();
    if (this.getMode() === "docked") {
      this.startResize(e.touches[0].clientY);
    } else {
      this.startDrag(e.touches[0].clientX, e.touches[0].clientY);
    }
  }
  onDragTouchMove(e) {
    if (!this.dragging && !this.resizing) return;
    e.preventDefault();
    if (this.resizing) {
      this.moveResize(e.touches[0].clientY);
    } else {
      this.moveDrag(e.touches[0].clientX, e.touches[0].clientY);
    }
  }
  onDragMouseStart(e) {
    e.preventDefault();
    e.stopPropagation();
    if (this.getMode() === "docked") {
      this.startResize(e.clientY);
    } else {
      this.startDrag(e.clientX, e.clientY);
    }
  }
  onDragMouseMove(e) {
    if (!this.dragging && !this.resizing) return;
    if (this.resizing) {
      this.moveResize(e.clientY);
    } else {
      this.moveDrag(e.clientX, e.clientY);
    }
  }
  onDragEnd() {
    this.dragging = false;
    this.resizing = false;
  }
  startDrag(cx, cy) {
    this.dragging = true;
    this.dragStartX = cx;
    this.dragStartY = cy;
    const cs = getComputedStyle(this.container);
    this.posX = parseFloat(cs.left);
    this.posY = parseFloat(cs.top);
  }
  moveDrag(cx, cy) {
    this.posX += cx - this.dragStartX;
    this.posY += cy - this.dragStartY;
    this.dragStartX = cx;
    this.dragStartY = cy;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const w = this.container.offsetWidth;
    const h = this.container.offsetHeight;
    if (this.posX < 0) this.posX = 0;
    if (this.posX + w > vw) this.posX = vw - w;
    if (this.posY < 0) this.posY = 0;
    if (this.posY + h > vh) this.posY = vh - h;
    this.container.style.left = this.posX + "px";
    this.container.style.top = this.posY + "px";
  }
  // ─── Docked 模式拉伸 ───
  startResize(cy) {
    this.resizing = true;
    this.resizeStartY = cy;
    this.resizeStartHeight = this.container.offsetHeight;
    const saved = this.container.style.height;
    this.container.style.height = "";
    this.resizeMinHeight = this.container.offsetHeight;
    this.container.style.height = saved;
  }
  moveResize(cy) {
    const delta = this.resizeStartY - cy;
    let newH = this.resizeStartHeight + delta;
    const vv = window.visualViewport;
    const vh = vv ? vv.height : window.innerHeight;
    const minH = this.resizeMinHeight || 160;
    const maxH = vh * 0.75;
    newH = Math.max(minH, Math.min(maxH, newH));
    this.dockedHeight = newH;
    this.container.style.height = newH + "px";
    this.positionDocked();
  }
  /** 获取当前 docked 高度（供外部恢复） */
  getDockedHeight() {
    return this.dockedHeight;
  }
  // ─── 工具 ───
  isTextInput(el) {
    return el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement;
  }
}

;// ./src/keyboard/theme.ts


const theme_THEME_PRESETS = {
  dark: {
    kbBg: "rgba(30,30,34,.98)",
    kbBorder: "rgba(255,255,255,.06)",
    kbShadow: "0 -2px 12px rgba(0,0,0,.4)",
    keyBg: "rgba(58,58,64,.92)",
    keyColor: "rgba(255,255,255,.88)",
    keyActiveBg: "rgba(112,192,232,.35)",
    keyActiveColor: "#fff",
    fnKeyBg: "rgba(48,48,54,.92)",
    fnKeyColor: "rgba(255,255,255,.6)",
    spaceBg: "rgba(58,58,64,.92)",
    spaceColor: "rgba(255,255,255,.5)",
    candBarBg: "rgba(24,24,28,.96)",
    candColor: "rgba(255,255,255,.82)",
    candActiveBg: "rgba(112,192,232,.15)",
    compHeadColor: "#63e2b7",
    compBodyColor: "#70c0e8",
    compTailColor: "rgba(255,255,255,.7)",
    navColor: "rgba(255,255,255,.4)",
    toolbarBg: "rgba(24,24,28,.96)",
    toolbarBtnColor: "rgba(255,255,255,.45)",
    toolbarBtnActiveColor: "#70c0e8",
    previewBg: "rgba(58,58,64,.96)",
    previewColor: "#fff",
    altBg: "rgba(40,40,46,.98)",
    altColor: "rgba(255,255,255,.85)",
    altActiveBg: "rgba(112,192,232,.3)"
  },
  light: {
    kbBg: "rgba(245,245,247,.98)",
    kbBorder: "rgba(0,0,0,.08)",
    kbShadow: "0 -2px 12px rgba(0,0,0,.1)",
    keyBg: "rgba(255,255,255,.95)",
    keyColor: "rgba(0,0,0,.85)",
    keyActiveBg: "rgba(32,128,240,.2)",
    keyActiveColor: "#2080f0",
    fnKeyBg: "rgba(230,230,233,.95)",
    fnKeyColor: "rgba(0,0,0,.55)",
    spaceBg: "rgba(255,255,255,.95)",
    spaceColor: "rgba(0,0,0,.4)",
    candBarBg: "rgba(255,255,255,.98)",
    candColor: "rgba(0,0,0,.82)",
    candActiveBg: "rgba(32,128,240,.1)",
    compHeadColor: "#18a058",
    compBodyColor: "#2080f0",
    compTailColor: "rgba(0,0,0,.7)",
    navColor: "rgba(0,0,0,.4)",
    toolbarBg: "rgba(255,255,255,.98)",
    toolbarBtnColor: "rgba(0,0,0,.4)",
    toolbarBtnActiveColor: "#2080f0",
    previewBg: "rgba(255,255,255,.98)",
    previewColor: "rgba(0,0,0,.85)",
    altBg: "rgba(245,245,247,.98)",
    altColor: "rgba(0,0,0,.85)",
    altActiveBg: "rgba(32,128,240,.15)"
  }
};
const theme_SIZE_PRESETS = {
  compact: {
    keyHeight: "36px",
    keyFontSize: "15px",
    keyGap: "3px",
    keyRadius: "4px",
    candFontSize: "13px",
    compFontSize: "13px",
    navFontSize: "10px",
    previewFontSize: "20px",
    altFontSize: "14px",
    candBarHeight: "32px"
  },
  normal: {
    keyHeight: "44px",
    keyFontSize: "18px",
    keyGap: "4px",
    keyRadius: "5px",
    candFontSize: "15px",
    compFontSize: "15px",
    navFontSize: "11px",
    previewFontSize: "26px",
    altFontSize: "16px",
    candBarHeight: "38px"
  },
  large: {
    keyHeight: "54px",
    keyFontSize: "22px",
    keyGap: "5px",
    keyRadius: "6px",
    candFontSize: "18px",
    compFontSize: "18px",
    navFontSize: "13px",
    previewFontSize: "32px",
    altFontSize: "18px",
    candBarHeight: "44px"
  }
};
const DEFAULT_THEME_VARS = {
  kbBg: "rgba(30,30,34,.98)",
  kbBorder: "rgba(255,255,255,.06)",
  kbRadius: "0px",
  kbShadow: "0 -2px 12px rgba(0,0,0,.4)",
  kbFontFamily: '-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif',
  kbZIndex: 99999,
  keyBg: "rgba(58,58,64,.92)",
  keyColor: "rgba(255,255,255,.88)",
  keyFontSize: "18px",
  keyHeight: "44px",
  keyGap: "4px",
  keyRadius: "5px",
  keyActiveBg: "rgba(112,192,232,.35)",
  keyActiveColor: "#fff",
  keyActiveScale: "0.96",
  fnKeyBg: "rgba(48,48,54,.92)",
  fnKeyColor: "rgba(255,255,255,.6)",
  spaceBg: "rgba(58,58,64,.92)",
  spaceColor: "rgba(255,255,255,.5)",
  candBarBg: "rgba(24,24,28,.96)",
  candBarHeight: "38px",
  candColor: "rgba(255,255,255,.82)",
  candFontSize: "15px",
  candActiveBg: "rgba(112,192,232,.15)",
  compHeadColor: "#63e2b7",
  compBodyColor: "#70c0e8",
  compTailColor: "rgba(255,255,255,.7)",
  compFontSize: "15px",
  navColor: "rgba(255,255,255,.4)",
  navFontSize: "11px",
  toolbarBg: "rgba(24,24,28,.96)",
  toolbarBtnColor: "rgba(255,255,255,.45)",
  toolbarBtnActiveColor: "#70c0e8",
  previewBg: "rgba(58,58,64,.96)",
  previewColor: "#fff",
  previewFontSize: "26px",
  previewRadius: "6px",
  altBg: "rgba(40,40,46,.98)",
  altColor: "rgba(255,255,255,.85)",
  altFontSize: "16px",
  altActiveBg: "rgba(112,192,232,.3)",
  safeAreaBottom: "env(safe-area-inset-bottom, 0px)"
};
function resolveThemeVars(theme, size, overrides) {
  const base = { ...theme_THEME_PRESETS[theme] };
  const sizeVars = { ...theme_SIZE_PRESETS[size] };
  return { ...DEFAULT_THEME_VARS, ...base, ...sizeVars, ...overrides };
}
function applyThemeVars(container, vars) {
  const s = container.style;
  const set = (prop, val) => {
    if (val !== void 0) s.setProperty(prop, String(val));
  };
  set("--rime-kb-bg", vars.kbBg);
  set("--rime-kb-border", vars.kbBorder);
  set("--rime-kb-radius", vars.kbRadius);
  set("--rime-kb-shadow", vars.kbShadow);
  set("--rime-kb-font-family", vars.kbFontFamily);
  set("--rime-kb-z-index", vars.kbZIndex);
  set("--rime-kb-key-bg", vars.keyBg);
  set("--rime-kb-key-color", vars.keyColor);
  set("--rime-kb-key-font-size", vars.keyFontSize);
  set("--rime-kb-key-height", vars.keyHeight);
  set("--rime-kb-key-gap", vars.keyGap);
  set("--rime-kb-key-radius", vars.keyRadius);
  set("--rime-kb-key-active-bg", vars.keyActiveBg);
  set("--rime-kb-key-active-color", vars.keyActiveColor);
  set("--rime-kb-key-active-scale", vars.keyActiveScale);
  set("--rime-kb-fn-key-bg", vars.fnKeyBg);
  set("--rime-kb-fn-key-color", vars.fnKeyColor);
  set("--rime-kb-space-bg", vars.spaceBg);
  set("--rime-kb-space-color", vars.spaceColor);
  set("--rime-kb-cand-bar-bg", vars.candBarBg);
  set("--rime-kb-cand-bar-height", vars.candBarHeight);
  set("--rime-kb-cand-color", vars.candColor);
  set("--rime-kb-cand-font-size", vars.candFontSize);
  set("--rime-kb-cand-active-bg", vars.candActiveBg);
  set("--rime-kb-comp-head-color", vars.compHeadColor);
  set("--rime-kb-comp-body-color", vars.compBodyColor);
  set("--rime-kb-comp-tail-color", vars.compTailColor);
  set("--rime-kb-comp-font-size", vars.compFontSize);
  set("--rime-kb-nav-color", vars.navColor);
  set("--rime-kb-nav-font-size", vars.navFontSize);
  set("--rime-kb-toolbar-bg", vars.toolbarBg);
  set("--rime-kb-toolbar-btn-color", vars.toolbarBtnColor);
  set("--rime-kb-toolbar-btn-active-color", vars.toolbarBtnActiveColor);
  set("--rime-kb-preview-bg", vars.previewBg);
  set("--rime-kb-preview-color", vars.previewColor);
  set("--rime-kb-preview-font-size", vars.previewFontSize);
  set("--rime-kb-preview-radius", vars.previewRadius);
  set("--rime-kb-alt-bg", vars.altBg);
  set("--rime-kb-alt-color", vars.altColor);
  set("--rime-kb-alt-font-size", vars.altFontSize);
  set("--rime-kb-alt-active-bg", vars.altActiveBg);
  set("--rime-kb-safe-bottom", vars.safeAreaBottom);
}
let styleInstanceCount = 0;
function injectKeyboardStyle() {
  if (document.getElementById(STYLE_ID)) {
    styleInstanceCount++;
    return;
  }
  const s = document.createElement("style");
  s.id = STYLE_ID;
  s.textContent = CSS;
  document.head.appendChild(s);
  styleInstanceCount++;
}
function removeKeyboardStyle() {
  styleInstanceCount--;
  if (styleInstanceCount <= 0) {
    const st = document.getElementById(STYLE_ID);
    if (st) st.remove();
    styleInstanceCount = 0;
  }
}
const CSS = `
.rime-kb{position:fixed;z-index:var(--rime-kb-z-index,99999);font-family:var(--rime-kb-font-family,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif);user-select:none;-webkit-user-select:none;touch-action:none;overflow:hidden;transition:transform .25s ease,opacity .2s ease;display:flex;flex-direction:column}
.rime-kb-docked{left:0;right:0;width:100%;border-radius:0;border-top:1px solid var(--rime-kb-border,rgba(255,255,255,.06));box-shadow:var(--rime-kb-shadow,0 -2px 12px rgba(0,0,0,.4))}
.rime-kb-floating{border-radius:12px;border:1px solid var(--rime-kb-border,rgba(255,255,255,.06));box-shadow:0 4px 24px rgba(0,0,0,.5)}
.rime-kb-hidden{transform:translateY(100vh);opacity:0;pointer-events:none}
.rime-kb-toolbar{display:flex;align-items:center;justify-content:center;position:relative;padding:2px 6px;height:20px;flex-shrink:0;background:var(--rime-kb-toolbar-bg,rgba(24,24,28,.96));border-bottom:1px solid var(--rime-kb-border,rgba(255,255,255,.06))}
.rime-kb-docked .rime-kb-toolbar{cursor:ns-resize}
.rime-kb-floating .rime-kb-toolbar{cursor:grab}
.rime-kb-floating .rime-kb-toolbar:active{cursor:grabbing}
.rime-kb-tb-drag{color:rgba(255,255,255,.18);font-size:12px;font-weight:700;line-height:1;letter-spacing:3px;pointer-events:none}
.rime-kb-tb-drag:active{cursor:grabbing}
.rime-kb-tb-hide{position:absolute;right:6px;top:50%;transform:translateY(-50%);background:transparent;border:none;color:var(--rime-kb-toolbar-btn-color,rgba(255,255,255,.45));font-size:9px;cursor:pointer;padding:2px 4px;line-height:1;border-radius:3px;-webkit-tap-highlight-color:transparent}
.rime-kb-tb-hide:hover{color:var(--rime-kb-toolbar-btn-active-color,#70c0e8)}
.rime-kb-tb-hide:active{color:var(--rime-kb-toolbar-btn-active-color,#70c0e8)}
.rime-kb-compbar{display:none;align-items:center;padding:0 10px;height:28px;flex-shrink:0;background:var(--rime-kb-cand-bar-bg,rgba(24,24,28,.96));border-bottom:1px solid var(--rime-kb-border,rgba(255,255,255,.06));overflow:hidden;white-space:nowrap}
.rime-kb-compbar-visible{display:flex}
.rime-kb-comp-h{color:var(--rime-kb-comp-head-color,#63e2b7);font-size:var(--rime-kb-comp-font-size,15px)}
.rime-kb-comp-b{color:var(--rime-kb-comp-body-color,#70c0e8);font-size:var(--rime-kb-comp-font-size,15px);text-decoration:underline}
.rime-kb-comp-t{color:var(--rime-kb-comp-tail-color,rgba(255,255,255,.7));font-size:var(--rime-kb-comp-font-size,15px)}
.rime-kb-candbar{display:none;align-items:center;gap:4px;padding:0 8px;height:var(--rime-kb-cand-bar-height,38px);flex-shrink:0;background:var(--rime-kb-cand-bar-bg,rgba(24,24,28,.96));border-bottom:1px solid var(--rime-kb-border,rgba(255,255,255,.06));overflow:hidden}
.rime-kb-candbar-visible{display:flex}
.rime-kb-cands{display:flex;gap:6px;overflow-x:auto;flex:1;scrollbar-width:none;-ms-overflow-style:none}
.rime-kb-cands::-webkit-scrollbar{display:none}
.rime-kb-cand{display:inline-flex;align-items:center;gap:2px;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:var(--rime-kb-cand-font-size,15px);color:var(--rime-kb-cand-color,rgba(255,255,255,.82));white-space:nowrap;transition:background .1s}
.rime-kb-cand:hover,.rime-kb-cand-hl{background:var(--rime-kb-cand-active-bg,rgba(112,192,232,.15))}
.rime-kb-cand-lb{color:var(--rime-kb-comp-body-color,#70c0e8);font-size:0.85em;margin-right:1px}
.rime-kb-cand-nav{display:flex;align-items:center;gap:2px;flex-shrink:0}
.rime-kb-cand-nav-btn{background:transparent;border:none;color:var(--rime-kb-nav-color,rgba(255,255,255,.4));cursor:pointer;font-size:10px;padding:2px 4px;border-radius:2px}
.rime-kb-cand-nav-btn:disabled{opacity:.3;cursor:default}
.rime-kb-cand-nav-btn:hover:not(:disabled){color:var(--rime-kb-comp-body-color,#70c0e8)}
.rime-kb-cand-page{color:var(--rime-kb-nav-color,rgba(255,255,255,.4));font-size:var(--rime-kb-nav-font-size,11px)}
.rime-kb-keys{padding:6px 4px 4px;flex:1;min-height:0;display:flex;flex-direction:column;gap:var(--rime-kb-key-gap,4px);background:var(--rime-kb-bg,rgba(30,30,34,.98))}
.rime-kb-row{display:flex;justify-content:center;gap:var(--rime-kb-key-gap,4px);flex:1 0 auto;align-items:stretch}
.rime-kb-row:last-child{margin-bottom:0}
.rime-kb-key{display:flex;align-items:center;justify-content:center;flex:1;min-height:var(--rime-kb-key-height,44px);background:var(--rime-kb-key-bg,rgba(58,58,64,.92));color:var(--rime-kb-key-color,rgba(255,255,255,.88));border:none;border-radius:var(--rime-kb-key-radius,5px);font-size:var(--rime-kb-key-font-size,18px);font-family:inherit;cursor:pointer;transition:transform .08s,background .08s;-webkit-tap-highlight-color:transparent;outline:none;padding:0}
.rime-kb-key:active,.rime-kb-key-active{background:var(--rime-kb-key-active-bg,rgba(112,192,232,.35));color:var(--rime-kb-key-active-color,#fff);transform:scale(var(--rime-kb-key-active-scale,0.96))}
.rime-kb-key-fn{background:var(--rime-kb-fn-key-bg,rgba(48,48,54,.92));color:var(--rime-kb-fn-key-color,rgba(255,255,255,.6));font-size:calc(var(--rime-kb-key-font-size,18px) * 0.8)}
.rime-kb-key-fn:active,.rime-kb-key-fn.rime-kb-key-active{background:var(--rime-kb-key-active-bg,rgba(112,192,232,.35));color:var(--rime-kb-key-active-color,#fff)}
.rime-kb-key-space{background:var(--rime-kb-space-bg,rgba(58,58,64,.92));color:var(--rime-kb-space-color,rgba(255,255,255,.5));font-size:calc(var(--rime-kb-key-font-size,18px) * 0.75);letter-spacing:2px}
.rime-kb-key-shift.rime-kb-shift-on{background:var(--rime-kb-key-active-bg,rgba(112,192,232,.35));color:var(--rime-kb-key-active-color,#fff)}
.rime-kb-safe{height:var(--rime-kb-safe-bottom,env(safe-area-inset-bottom,0px));flex-shrink:0;background:var(--rime-kb-bg,rgba(30,30,34,.98))}
.rime-kb-preview{position:fixed;z-index:100000;display:flex;align-items:center;justify-content:center;min-width:44px;height:56px;padding:4px 12px;background:var(--rime-kb-preview-bg,rgba(58,58,64,.96));color:var(--rime-kb-preview-color,#fff);font-size:var(--rime-kb-preview-font-size,26px);border-radius:var(--rime-kb-preview-radius,6px);box-shadow:0 2px 8px rgba(0,0,0,.3);pointer-events:none}
.rime-kb-alt{position:fixed;z-index:100001;display:flex;gap:2px;padding:6px 8px;background:var(--rime-kb-alt-bg,rgba(40,40,46,.98));border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.4)}
.rime-kb-alt-item{display:flex;align-items:center;justify-content:center;min-width:36px;height:40px;padding:4px 8px;border-radius:4px;color:var(--rime-kb-alt-color,rgba(255,255,255,.85));font-size:var(--rime-kb-alt-font-size,16px);cursor:pointer;transition:background .1s}
.rime-kb-alt-item-active{background:var(--rime-kb-alt-active-bg,rgba(112,192,232,.3))}
`;

;// ./src/keyboard/index.ts








const KB_STORAGE_KEY = "rime-kb-state";
class RimeKeyboard {
  // ─── 构造 ───
  constructor(config) {
    // IME 状态
    this.currentPage = "letters";
    this.shiftState = "off";
    this.isEnglish = false;
    this.isFullWidth = false;
    this.isEnglishPunct = false;
    this.isEmoji = false;
    this.isSimplification = true;
    this.editing = false;
    this._visible = false;
    this.destroyed = false;
    this.lastResult = null;
    // 智能引号 toggle 状态（独立跟踪单/双引号）
    this._singleQuoteLeft = true;
    this._doubleQuoteLeft = true;
    // 回调
    this.showCallbacks = [];
    this.hideCallbacks = [];
    this.keyPressCallbacks = [];
    this.commitCallbacks = [];
    // 非 text input 目标（如终端 div）的文字插入/删除回调
    this.textInsertCallbacks = [];
    this.textDeleteCallbacks = [];
    this.target = config.target;
    this._showOnFocus = config.showOnFocus ?? true;
    this._floatingWidth = config.floatingWidth ?? 320;
    this._floatingHeight = config.floatingHeight ?? 220;
    this._hapticEnabled = config.haptic ?? true;
    this._currentTheme = config.theme ?? "dark";
    this._currentSize = config.size ?? "normal";
    this._currentMode = config.kbMode ?? "docked";
    this._eol = config.eol ?? "\n";
    this._hideCandidateBar = config.hideCandidateBar ?? false;
    if (config.ime) {
      this.ime = config.ime;
      this._ownsIME = false;
    } else {
      this.ime = new RimeIME(config);
      this._ownsIME = true;
    }
    this._currentThemeVars = resolveThemeVars(
      this._currentTheme,
      this._currentSize,
      void 0
    );
    this.loadKbState();
    this.dom = createKeyboardDOM();
    if (this._hideCandidateBar) {
      this.dom.compBar.style.display = "none";
      this.dom.candBar.style.display = "none";
    }
    this.applyModeToContainer();
    document.body.appendChild(this.dom.container);
    document.body.appendChild(this.dom.preview);
    document.body.appendChild(this.dom.alt);
    injectKeyboardStyle();
    applyThemeVars(this.dom.container, this._currentThemeVars);
    this.renderKeys();
    this.touch = new KeyboardTouchHandler(
      this.dom.keys,
      this.dom.container,
      this.dom.preview,
      this.dom.alt,
      {
        fireKey: (kd) => this.fireKey(kd),
        insertText: (t) => this.insertText(t),
        haptic: () => this.haptic(),
        getKeyDef: (k) => this.getKeyDef(k)
      }
    );
    this.viewport = new KeyboardViewportController(
      this.dom.container,
      this.target,
      this.dom.dragHandle,
      () => this._currentMode,
      () => this._visible,
      () => this.touch.keyTouched,
      () => this._showOnFocus,
      () => {
        this._visible = true;
        this.showCallbacks.forEach((cb) => cb());
      },
      () => {
        this._visible = false;
        this.hideCallbacks.forEach((cb) => cb());
      },
      this._floatingWidth,
      this._floatingHeight
    );
    this.viewport.setupTarget();
    this.touch.bind();
    this.viewport.bind();
    this.bindCandBar();
    this.bindIME();
    this.bindHideBtn();
    this.dom.container.classList.add("rime-kb-hidden");
  }
  // ─── 公开 API ───
  async init() {
    await this.ime.init();
  }
  destroy() {
    this.destroyed = true;
    if (this._ownsIME) this.ime.destroy();
    this.touch.destroy();
    this.viewport.destroy();
    this.viewport.restoreTarget();
    this.dom.container.remove();
    this.dom.preview.remove();
    this.dom.alt.remove();
    removeKeyboardStyle();
  }
  getIME() {
    return this.ime;
  }
  isInitialized() {
    return this.ime.isInitialized();
  }
  /** 获取键盘容器元素（供外部设置透明度等样式） */
  getElement() {
    return this.dom.container;
  }
  show() {
    if (this._visible) return;
    this.viewport.show();
  }
  hide() {
    if (!this._visible) return;
    this.viewport.hide();
  }
  toggle() {
    this._visible ? this.hide() : this.show();
  }
  isVisible() {
    return this._visible;
  }
  setPage(page) {
    this.currentPage = page;
    this.renderKeys();
  }
  getPage() {
    return this.currentPage;
  }
  setMode(mode) {
    this._currentMode = mode;
    this.applyModeToContainer();
  }
  getMode() {
    return this._currentMode;
  }
  setTheme(theme, vars) {
    this._currentTheme = theme;
    this._currentThemeVars = resolveThemeVars(theme, this._currentSize, vars);
    applyThemeVars(this.dom.container, this._currentThemeVars);
  }
  setSize(size) {
    this._currentSize = size;
    this._currentThemeVars = resolveThemeVars(this._currentTheme, size);
    applyThemeVars(this.dom.container, this._currentThemeVars);
  }
  onShow(cb) {
    this.showCallbacks.push(cb);
  }
  onHide(cb) {
    this.hideCallbacks.push(cb);
  }
  onKeyPress(cb) {
    this.keyPressCallbacks.push(cb);
  }
  onCommit(cb) {
    this.commitCallbacks.push(cb);
  }
  /** 当目标非 text input（如终端 div）时，文字插入会通过此回调通知外部 */
  onTextInsert(cb) {
    this.textInsertCallbacks.push(cb);
  }
  /** 当目标非 text input（如终端 div）时，退格删除会通过此回调通知外部 */
  onTextDelete(cb) {
    this.textDeleteCallbacks.push(cb);
  }
  offShow(cb) {
    this.showCallbacks = this.showCallbacks.filter((c) => c !== cb);
  }
  offHide(cb) {
    this.hideCallbacks = this.hideCallbacks.filter((c) => c !== cb);
  }
  offKeyPress(cb) {
    this.keyPressCallbacks = this.keyPressCallbacks.filter((c) => c !== cb);
  }
  offCommit(cb) {
    this.commitCallbacks = this.commitCallbacks.filter((c) => c !== cb);
  }
  offTextInsert(cb) {
    this.textInsertCallbacks = this.textInsertCallbacks.filter((c) => c !== cb);
  }
  offTextDelete(cb) {
    this.textDeleteCallbacks = this.textDeleteCallbacks.filter((c) => c !== cb);
  }
  // ─── 渲染 ───
  renderKeys() {
    renderKeys(this.dom.keys, this.currentPage, this.shiftState, this.isEnglish, this.isEnglishPunct, this.isFullWidth);
  }
  renderCand(r) {
    if (this._hideCandidateBar) return;
    const savedH = this.dom.container.style.height;
    const savedMaxH = this.dom.container.style.maxHeight;
    const savedMaxVal = parseFloat(savedMaxH) || 0;
    this.dom.container.style.height = "";
    this.dom.container.style.maxHeight = "";
    const prevNaturalH = this.dom.container.offsetHeight;
    const prevVisibleH = savedMaxVal > 0 ? Math.min(prevNaturalH, savedMaxVal) : prevNaturalH;
    renderCompBar(this.dom.compBar, r);
    renderCandBar(this.dom, r);
    const newNaturalH = this.dom.container.offsetHeight;
    if (savedMaxVal > 0 && newNaturalH > savedMaxVal) {
      this.dom.container.style.maxHeight = newNaturalH + "px";
    } else {
      this.dom.container.style.maxHeight = savedMaxH;
    }
    const newVisibleH = newNaturalH;
    const delta = newVisibleH - prevVisibleH;
    if (savedH) {
      this.dom.container.style.height = parseFloat(savedH) + delta + "px";
    }
    if (delta !== 0) {
      const cur = parseFloat(this.dom.container.style.top) || 0;
      this.dom.container.style.top = cur - delta + "px";
    }
    this.clampToViewport();
  }
  refreshUI() {
    this.renderKeys();
  }
  // ─── 隐藏按钮 ───
  bindHideBtn() {
    this.dom.hideBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.hide();
    });
  }
  // ─── 候选栏事件 ───
  bindCandBar() {
    this.dom.cands.addEventListener("click", (e) => {
      const t = e.target;
      const cand = t.closest(".rime-kb-cand");
      if (cand && cand.dataset.idx != null) {
        this.ime.selectCandidate(parseInt(cand.dataset.idx, 10)).then((r) => this.analyze(r, "")).catch(() => {
        });
      }
    });
    this.dom.candNav.addEventListener("click", (e) => {
      const t = e.target;
      const btn = t.closest(".rime-kb-cand-nav-btn");
      if (btn && btn.dataset.dir) {
        this.ime.changePage(btn.dataset.dir === "prev").then((r) => this.analyze(r, "")).catch(() => {
        });
      }
    });
  }
  // ─── IME 事件 ───
  bindIME() {
    this.ime.onOptionChange((opts) => {
      console.log("[RimeKB] onOptionChange", opts);
      if ("ascii_mode" in opts) {
        this.isEnglish = opts.ascii_mode;
        if (this.isEnglish) this.shiftState = "off";
      }
      if ("full_shape" in opts) this.isFullWidth = opts.full_shape;
      if ("ascii_punct" in opts) this.isEnglishPunct = opts.ascii_punct;
      if ("emoji_suggestion" in opts) this.isEmoji = opts.emoji_suggestion;
      if ("simplification" in opts) this.isSimplification = opts.simplification;
      console.log("[RimeKB] state after change", { isFullWidth: this.isFullWidth, isEnglish: this.isEnglish, isEnglishPunct: this.isEnglishPunct });
      this.saveKbState();
      this.refreshUI();
    });
    this.ime.onSchemaChange(() => {
      this.saveKbState();
      this.refreshUI();
    });
  }
  // ─── 按键动作 ───
  fireKey(keyDef) {
    const action = keyDef.action;
    console.log("[RimeKB] fireKey START", { action, key: keyDef.key, isFullWidth: this.isFullWidth, isEnglish: this.isEnglish, editing: this.editing });
    if (action === "shift") {
      this.handleShift();
      return;
    }
    if (action === "page") {
      this.currentPage = keyDef.page ?? getPageSwitchTarget(this.currentPage);
      this.renderKeys();
      return;
    }
    if (action === "lang") {
      this.isEnglish = !this.isEnglish;
      this.ime.setOption("ascii_mode", this.isEnglish).catch(() => {
      });
      if (!this.ime.punctLocked) {
        this.ime.setOption("ascii_punct", this.isEnglish).catch(() => {
        });
      }
      // 切换到英文模式时，取消当前组词（清除候选词列表）
      // 与 rimeManager.js setupShiftToggle 和工具栏 btnLang 的处理一致
      if (this.isEnglish) {
        this.shiftState = "off";
        if (this.editing) {
          this.ime.processKey('{Escape}').catch(() => {
          });
        }
      }
      this.refreshUI();
      return;
    }
    let rimeKey = "";
    if (action === "backspace") rimeKey = "{BackSpace}";
    else if (action === "enter") rimeKey = "{Return}";
    else if (action === "space") rimeKey = " ";
    else if (action === "punct") rimeKey = ".";
    else rimeKey = this.resolveCharKey(keyDef);
    if (!rimeKey) return;
    if (this.isEnglish || !this.editing) {
      if (rimeKey === "{BackSpace}") {
        this.deleteBackward();
        return;
      }
      if (rimeKey === "{Return}") {
        this.insertText(this._eol);
        return;
      }
      if (rimeKey === " ") {
        this.insertText(this.isFullWidth ? "\u3000" : " ");
        return;
      }
      if (action === "punct") {
        const useCnPunct = this.isFullWidth || !this.isEnglishPunct;
        const out = useCnPunct ? "\u3002" : ".";
        console.log("[RimeKB] fireKey punct branch", { isFullWidth: this.isFullWidth, isEnglishPunct: this.isEnglishPunct, out });
        this.insertText(out);
        return;
      }
      if (!this.editing && /^[a-zA-Z]$/.test(rimeKey)) {
        console.log("[RimeKB] fireKey letter\u2192RIME branch", { rimeKey });
      } else {
        const out = this.convertChar(rimeKey);
        console.log("[RimeKB] fireKey symbol branch", { rimeKey, out });
        this.insertText(out);
        return;
      }
    }
    console.log("[RimeKB] fireKey \u2192 RIME processKey", { rimeKey, isEnglish: this.isEnglish, editing: this.editing });
    this.keyPressCallbacks.forEach((cb) => cb(rimeKey));
    this.ime.processKey(rimeKey).then((r) => this.analyze(r, rimeKey)).catch(() => {
    });
  }
  resolveCharKey(keyDef) {
    if (this.shiftState !== "off" && keyDef.shiftKey) {
      console.log("[RimeKB] resolveCharKey shift", { keyDefKey: keyDef.key, shiftKey: keyDef.shiftKey });
      return keyDef.shiftKey;
    }
    console.log("[RimeKB] resolveCharKey", { keyDefKey: keyDef.key, returns: keyDef.key });
    return keyDef.key;
  }
  /**
   * 符号/字母数字转换（不受中英文影响）。
   * 符号受 isFullWidth（满月/半月）和 isEnglishPunct（符号全半角）共同控制，全角优先：
   *   - isFullWidth=true（满月）→ 强制全部全角：列表内→中文符号，列表外→全角英文符号（＠＆等），字母数字→全角
   *   - isFullWidth=false + isEnglishPunct=false（符号全角）→ 仅列表内中文符号，列表外 ASCII
   *   - isFullWidth=false + isEnglishPunct=true（符号半角）→ ASCII
   * 字母数字受 isFullWidth 控制：满月→全角，半月→ASCII
   */
  convertChar(ch) {
    const useCnPunct = this.isFullWidth || !this.isEnglishPunct;
    if (useCnPunct) {
      const mapped = FULLWIDTH_PUNCT_MAP[ch];
      if (mapped) {
        console.log("[RimeKB] convertChar mapped", { ch, mapped, isFullWidth: this.isFullWidth, isEnglishPunct: this.isEnglishPunct });
        return mapped;
      }
      if (ch === "'") {
        const result = this._singleQuoteLeft ? "\u2018" : "\u2019";
        this._singleQuoteLeft = !this._singleQuoteLeft;
        console.log("[RimeKB] convertChar singleQuote", { ch, result });
        return result;
      }
      if (ch === '"') {
        const result = this._doubleQuoteLeft ? "\u201C" : "\u201D";
        this._doubleQuoteLeft = !this._doubleQuoteLeft;
        console.log("[RimeKB] convertChar doubleQuote", { ch, result });
        return result;
      }
    }
    if (this.isFullWidth) {
      const result = toFullWidth(ch);
      console.log("[RimeKB] convertChar fullwidth", { ch, result });
      return result;
    }
    console.log("[RimeKB] convertChar ascii", { ch, useCnPunct, isFullWidth: this.isFullWidth, isEnglishPunct: this.isEnglishPunct });
    return ch;
  }
  handleShift() {
    if (this.isEnglish) {
      this.shiftState = this.shiftState === "off" ? "once" : this.shiftState === "once" ? "locked" : "off";
    } else {
      this.isEnglish = !this.isEnglish;
      this.ime.setOption("ascii_mode", this.isEnglish).catch(() => {
      });
      if (!this.ime.punctLocked) {
        this.ime.setOption("ascii_punct", this.isEnglish).catch(() => {
        });
      }
      // 切换到英文模式时，取消当前组词（清除候选词列表）
      // 与 rimeManager.js setupShiftToggle 和工具栏 btnLang 的处理一致
      if (this.isEnglish && this.editing) {
        this.ime.processKey('{Escape}').catch(() => {
        });
      }
      this.shiftState = "off";
    }
    this.refreshUI();
  }
  /** 分析 RIME 返回结果 */
  analyze(r, rimeKey) {
    this.lastResult = r;
    const wasEditing = this.editing;
    if (r.state === "committed") {
      this.editing = false;
      if (r.committed) {
        if (this._ownsIME) this.insertText(r.committed);
        this.commitCallbacks.forEach((cb) => cb(r.committed));
      }
      this.renderCand(null);
    } else if (r.state === "accepted") {
      if (r.committed) {
        if (this._ownsIME) this.insertText(r.committed);
        this.commitCallbacks.forEach((cb) => cb(r.committed));
      }
      this.editing = true;
      this.renderCand(r);
    } else {
      this.editing = false;
      this.renderCand(null);
      if (r.state === "rejected" && r.updatedSchema) {
        this.ime.setIME(r.updatedSchema.split("/")[0]).then((nr) => this.analyze(nr, "")).catch(() => {
        });
      }
      if (r.state === "unhandled" && !wasEditing) {
        if (rimeKey === "{BackSpace}") {
          this.deleteBackward();
        } else if (rimeKey === "{Return}") {
          this.insertText(this._eol);
        } else if (rimeKey.length === 1 && this.isPrintable(rimeKey)) {
          this.insertText(this.isFullWidth ? toFullWidth(rimeKey) : rimeKey);
        }
      }
    }
    if (this.shiftState === "once") {
      this.shiftState = "off";
      this.renderKeys();
    }
    this.viewport.focusTarget();
  }
  // ─── 工具 ───
  getKeyDef(key) {
    const layout = getLayout(this.currentPage);
    for (const row of layout) {
      for (const def of row) {
        if (def.key === key) return def;
      }
    }
    return null;
  }
  insertText(text) {
    this.editing = false;
    console.log("[RimeKB] insertText", JSON.stringify(text));
    if (this.isTextInput(this.target)) {
      const el = this.target;
      const s = el.selectionStart ?? el.value.length;
      const e = el.selectionEnd ?? s;
      const v = el.value;
      el.value = v.slice(0, s) + text + v.slice(e);
      el.selectionStart = el.selectionEnd = s + text.length;
      this.target.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      this.textInsertCallbacks.forEach((cb) => cb(text));
    }
  }
  deleteBackward() {
    if (this.isTextInput(this.target)) {
      const el = this.target;
      const s = el.selectionStart ?? el.value.length;
      const e = el.selectionEnd ?? s;
      const v = el.value;
      if (s !== e) {
        el.value = v.slice(0, s) + v.slice(e);
        el.selectionStart = el.selectionEnd = s;
      } else if (s > 0) {
        el.value = v.slice(0, s - 1) + v.slice(e);
        el.selectionStart = el.selectionEnd = s - 1;
      }
      this.target.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      this.textDeleteCallbacks.forEach((cb) => cb());
    }
  }
  isTextInput(el) {
    return el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement;
  }
  isPrintable(key) {
    return /^[a-z0-9!"#$%&'()*+,./:;<=>?@[\] ^_`{|}~\\-]$/i.test(key);
  }
  haptic() {
    if (!this._hapticEnabled) return;
    try {
      navigator.vibrate?.(8);
    } catch {
    }
  }
  applyModeToContainer() {
    const el = this.dom.container;
    el.classList.toggle("rime-kb-docked", this._currentMode === "docked");
    el.classList.toggle("rime-kb-floating", this._currentMode === "floating");
    if (this._currentMode === "floating") {
      el.style.width = this._floatingWidth + "px";
      if (!el.style.left) {
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const w = Math.min(this._floatingWidth, vw - 16);
        const h = Math.min(this._floatingHeight, vh - 16);
        let left = (vw - w) / 2;
        let top = vh - h - 80;
        left = Math.max(8, Math.min(left, vw - w - 8));
        top = Math.max(8, Math.min(top, vh - h - 8));
        el.style.left = left + "px";
        el.style.top = top + "px";
      }
    } else {
      el.style.left = "";
      el.style.top = "";
      el.style.width = "";
    }
  }
  clampToViewport() {
    if (this._currentMode === "docked") return;
    const el = this.dom.container;
    requestAnimationFrame(() => {
      const rect = el.getBoundingClientRect();
      if (rect.top < 0) {
        const currentTop = parseFloat(el.style.top) || 0;
        el.style.top = currentTop - rect.top + "px";
      }
    });
  }
  saveKbState() {
    try {
      localStorage.setItem(KB_STORAGE_KEY, JSON.stringify({
        isEnglish: this.isEnglish,
        isEnglishPunct: this.isEnglishPunct,
        isSimplification: this.isSimplification,
        isFullWidth: this.isFullWidth,
        isEmoji: this.isEmoji,
        currentPage: this.currentPage,
        mode: this._currentMode,
        size: this._currentSize
      }));
    } catch {
    }
  }
  loadKbState() {
    try {
      const raw = localStorage.getItem(KB_STORAGE_KEY);
      if (!raw) return;
      const s = JSON.parse(raw);
      if (typeof s.isEnglish === "boolean") this.isEnglish = s.isEnglish;
      if (typeof s.isEnglishPunct === "boolean") this.isEnglishPunct = s.isEnglishPunct;
      if (typeof s.isSimplification === "boolean") this.isSimplification = s.isSimplification;
      if (typeof s.isFullWidth === "boolean") this.isFullWidth = s.isFullWidth;
      if (typeof s.isEmoji === "boolean") this.isEmoji = s.isEmoji;
      if (s.currentPage) this.currentPage = s.currentPage;
      if (s.mode) this._currentMode = s.mode;
      if (s.size) this._currentSize = s.size;
    } catch {
    }
  }
}

;// ./src/keyboard/tkl/layouts.ts

const TKL_LAYOUT = [
  // 行0: 功能键行
  [
    { key: "Escape", label: "Esc", action: "escape", width: 1 },
    // F1~F4 组
    { key: "F1", label: "F1", action: "f1", width: 1 },
    { key: "F2", label: "F2", action: "f2", width: 1 },
    { key: "F3", label: "F3", action: "f3", width: 1 },
    { key: "F4", label: "F4", action: "f4", width: 1 },
    // F5~F8 组
    { key: "F5", label: "F5", action: "f5", width: 1 },
    { key: "F6", label: "F6", action: "f6", width: 1 },
    { key: "F7", label: "F7", action: "f7", width: 1 },
    { key: "F8", label: "F8", action: "f8", width: 1 },
    // F9~F12 组
    { key: "F9", label: "F9", action: "f9", width: 1 },
    { key: "F10", label: "F10", action: "f10", width: 1 },
    { key: "F11", label: "F11", action: "f11", width: 1 },
    { key: "F12", label: "F12", action: "f12", width: 1 },
    // 导航区
    { key: "PrintScreen", label: "PrtSc", action: "printscreen", width: 1 },
    { key: "ScrollLock", label: "ScrLk", action: "scrolllock", width: 1 },
    { key: "Pause", label: "Pause", action: "pause", width: 1 }
  ],
  // 行1: 数字行
  [
    { key: "`", label: "`", shiftKey: "~", shiftLabel: "~", width: 1 },
    { key: "1", label: "1", shiftKey: "!", shiftLabel: "!" },
    { key: "2", label: "2", shiftKey: "@", shiftLabel: "@" },
    { key: "3", label: "3", shiftKey: "#", shiftLabel: "#" },
    { key: "4", label: "4", shiftKey: "$", shiftLabel: "$" },
    { key: "5", label: "5", shiftKey: "%", shiftLabel: "%" },
    { key: "6", label: "6", shiftKey: "^", shiftLabel: "^" },
    { key: "7", label: "7", shiftKey: "&", shiftLabel: "&" },
    { key: "8", label: "8", shiftKey: "*", shiftLabel: "*" },
    { key: "9", label: "9", shiftKey: "(", shiftLabel: "(" },
    { key: "0", label: "0", shiftKey: ")", shiftLabel: ")" },
    { key: "-", label: "-", shiftKey: "_", shiftLabel: "_" },
    { key: "=", label: "=", shiftKey: "+", shiftLabel: "+" },
    { key: "Backspace", label: "\u232B", action: "backspace", width: 2 },
    // 导航区
    { key: "Insert", label: "Ins", action: "insert", width: 1 },
    { key: "Home", label: "Home", action: "home", width: 1 },
    { key: "PageUp", label: "PgUp", action: "pageup", width: 1 }
  ],
  // 行2: QWERTY 行
  [
    { key: "Tab", label: "Tab", action: "tab", width: 1.5 },
    { key: "q", label: "Q", shiftKey: "Q", shiftLabel: "Q" },
    { key: "w", label: "W", shiftKey: "W", shiftLabel: "W" },
    { key: "e", label: "E", shiftKey: "E", shiftLabel: "E" },
    { key: "r", label: "R", shiftKey: "R", shiftLabel: "R" },
    { key: "t", label: "T", shiftKey: "T", shiftLabel: "T" },
    { key: "y", label: "Y", shiftKey: "Y", shiftLabel: "Y" },
    { key: "u", label: "U", shiftKey: "U", shiftLabel: "U" },
    { key: "i", label: "I", shiftKey: "I", shiftLabel: "I" },
    { key: "o", label: "O", shiftKey: "O", shiftLabel: "O" },
    { key: "p", label: "P", shiftKey: "P", shiftLabel: "P" },
    { key: "[", label: "[", shiftKey: "{", shiftLabel: "{" },
    { key: "]", label: "]", shiftKey: "}", shiftLabel: "}" },
    { key: "\\", label: "\\", shiftKey: "|", shiftLabel: "|", width: 1.5 },
    // 导航区
    { key: "Delete", label: "Del", action: "delete", width: 1 },
    { key: "End", label: "End", action: "end", width: 1 },
    { key: "PageDown", label: "PgDn", action: "pagedown", width: 1 }
  ],
  // 行3: 主键行
  [
    { key: "CapsLock", label: "Caps", action: "caps", width: 1.75 },
    { key: "a", label: "A", shiftKey: "A", shiftLabel: "A" },
    { key: "s", label: "S", shiftKey: "S", shiftLabel: "S" },
    { key: "d", label: "D", shiftKey: "D", shiftLabel: "D" },
    { key: "f", label: "F", shiftKey: "F", shiftLabel: "F" },
    { key: "g", label: "G", shiftKey: "G", shiftLabel: "G" },
    { key: "h", label: "H", shiftKey: "H", shiftLabel: "H" },
    { key: "j", label: "J", shiftKey: "J", shiftLabel: "J" },
    { key: "k", label: "K", shiftKey: "K", shiftLabel: "K" },
    { key: "l", label: "L", shiftKey: "L", shiftLabel: "L" },
    { key: ";", label: ";", shiftKey: ":", shiftLabel: ":" },
    { key: "'", label: "'", shiftKey: '"', shiftLabel: '"' },
    { key: "Enter", label: "Enter", action: "enter", width: 2.25 }
  ],
  // 行4: Shift 行
  [
    { key: "ShiftLeft", label: "Shift", action: "shift", width: 2.25, isModifier: true },
    { key: "z", label: "Z", shiftKey: "Z", shiftLabel: "Z" },
    { key: "x", label: "X", shiftKey: "X", shiftLabel: "X" },
    { key: "c", label: "C", shiftKey: "C", shiftLabel: "C" },
    { key: "v", label: "V", shiftKey: "V", shiftLabel: "V" },
    { key: "b", label: "B", shiftKey: "B", shiftLabel: "B" },
    { key: "n", label: "N", shiftKey: "N", shiftLabel: "N" },
    { key: "m", label: "M", shiftKey: "M", shiftLabel: "M" },
    { key: ",", label: ",", shiftKey: "<", shiftLabel: "<" },
    { key: ".", label: ".", shiftKey: ">", shiftLabel: ">" },
    { key: "/", label: "/", shiftKey: "?", shiftLabel: "?" },
    { key: "ShiftRight", label: "Shift", action: "shift", width: 2.75, isModifier: true },
    // 方向键 ↑（倒 T 形：两侧 spacer 使 ArrowUp 对齐行5的 ArrowDown）
    { key: "SpacerLeft", label: "", width: 1, hidden: true },
    { key: "ArrowUp", label: "\u2191", action: "arrow_up", width: 1 },
    { key: "SpacerRight", label: "", width: 1, hidden: true }
  ],
  // 行5: 底部修饰键行
  [
    { key: "ControlLeft", label: "Ctrl", action: "ctrl", width: 1.25, isModifier: true },
    { key: "MetaLeft", label: "Win", action: "meta", width: 1.25, isModifier: true },
    { key: "AltLeft", label: "Alt", action: "alt", width: 1.25, isModifier: true },
    { key: "space", label: "", action: "space", width: 6.25 },
    { key: "AltRight", label: "Alt", action: "alt", width: 1.25, isModifier: true },
    { key: "Lang", label: "\u4E2D", action: "lang", width: 1.25 },
    { key: "ControlRight", label: "Ctrl", action: "ctrl", width: 1.25, isModifier: true },
    // 方向键 ← ↓ →
    { key: "ArrowLeft", label: "\u2190", action: "arrow_left", width: 1 },
    { key: "ArrowDown", label: "\u2193", action: "arrow_down", width: 1 },
    { key: "ArrowRight", label: "\u2192", action: "arrow_right", width: 1 }
  ]
];
function getTKLLayout() {
  return TKL_LAYOUT;
}
const TKL_RIME_KEY_MAP = {
  Escape: "Escape",
  F1: "F1",
  F2: "F2",
  F3: "F3",
  F4: "F4",
  F5: "F5",
  F6: "F6",
  F7: "F7",
  F8: "F8",
  F9: "F9",
  F10: "F10",
  F11: "F11",
  F12: "F12",
  Backspace: "BackSpace",
  Delete: "Delete",
  Tab: "Tab",
  Enter: "Return",
  Home: "Home",
  End: "End",
  PageUp: "Page_Up",
  PageDown: "Page_Down",
  ArrowUp: "Up",
  ArrowDown: "Down",
  ArrowLeft: "Left",
  ArrowRight: "Right",
  CapsLock: "Caps_Lock",
  PrintScreen: "Print",
  ScrollLock: "Scroll_Lock",
  Pause: "Pause",
  Insert: "Insert",
  " ": "space",
  // 符号键 RIME 名称
  "`": "quoteleft",
  "~": "asciitilde",
  "!": "exclam",
  "@": "at",
  "#": "numbersign",
  $: "dollar",
  "%": "percent",
  "^": "asciicircum",
  "&": "ampersand",
  "*": "asterisk",
  "(": "parenleft",
  ")": "parenright",
  "-": "minus",
  _: "underscore",
  "+": "plus",
  "=": "equal",
  "{": "braceleft",
  "[": "bracketleft",
  "}": "braceright",
  "]": "bracketright",
  ":": "colon",
  ";": "semicolon",
  '"': "quotedbl",
  "'": "apostrophe",
  "|": "bar",
  "\\": "backslash",
  "<": "less",
  ",": "comma",
  ">": "greater",
  ".": "period",
  "?": "question",
  "/": "slash"
};
const MODIFIER_NAMES = {
  ctrl: "Control",
  alt: "Alt",
  meta: "Meta",
  shift: "Shift"
};
function buildRimeCombo(modifiers, key) {
  const parts = [...modifiers, key];
  return `{${parts.join("+")}}`;
}

;// ./src/keyboard/tkl/dom.ts

function createTKLKeyboardDOM() {
  const container = dom_div("rime-tkl");
  const toolbar = dom_createToolbar();
  const keys = dom_div("rime-tkl-keys");
  const preview = dom_div("rime-tkl-preview");
  preview.style.display = "none";
  container.appendChild(toolbar);
  container.appendChild(keys);
  const hideBtn = toolbar.querySelector(".rime-tkl-tb-hide");
  return {
    container,
    toolbar,
    keys,
    preview,
    dragHandle: toolbar,
    hideBtn
  };
}
function dom_div(cls) {
  const el = document.createElement("div");
  el.className = cls;
  return el;
}
function dom_createToolbar() {
  const el = dom_div("rime-tkl-toolbar");
  el.innerHTML = `
    <div class="rime-tkl-tb-drag">\u2261</div>
    <button class="rime-tkl-tb-hide" type="button" title="\u9690\u85CF\u952E\u76D8">\u25BC</button>
  `;
  return el;
}

;// ./src/keyboard/tkl/render.ts



function renderTKLKeys(keysEl, shiftState, capsActive, modifiers, isEnglish, isFullWidth, isEnglishPunct) {
  const layout = getTKLLayout();
  keysEl.innerHTML = "";
  for (const row of layout) {
    const rowEl = document.createElement("div");
    rowEl.className = "rime-tkl-row";
    for (const keyDef of row) {
      rowEl.appendChild(createTKLKeyEl(keyDef, shiftState, capsActive, modifiers, isEnglish, isFullWidth, isEnglishPunct));
    }
    keysEl.appendChild(rowEl);
  }
}
function createTKLKeyEl(keyDef, shiftState, capsActive, modifiers, isEnglish, isFullWidth, isEnglishPunct) {
  if (keyDef.hidden) {
    const spacer = document.createElement("div");
    spacer.className = "rime-tkl-spacer";
    if (keyDef.width) spacer.style.flex = String(keyDef.width);
    return spacer;
  }
  const el = document.createElement("button");
  el.className = render_keyClass(keyDef, shiftState, capsActive, modifiers);
  el.dataset.key = keyDef.key;
  if (keyDef.action) el.dataset.action = keyDef.action;
  if (keyDef.width) el.style.flex = String(keyDef.width);
  if (keyDef.shiftLabel && !/^[a-z]$/i.test(keyDef.key) && !keyDef.action) {
    const shiftOn = shiftState !== "off";
    el.classList.add("rime-tkl-key-dual");
    if (shiftOn) el.classList.add("rime-tkl-key-dual-shift");
    const sub = document.createElement("span");
    sub.className = "rime-tkl-sub";
    sub.textContent = shiftOn ? keyMainLabel(keyDef, isFullWidth, isEnglishPunct) : keySubLabel(keyDef, isFullWidth, isEnglishPunct);
    const main = document.createElement("span");
    main.className = "rime-tkl-main";
    main.textContent = shiftOn ? keySubLabel(keyDef, isFullWidth, isEnglishPunct) : keyMainLabel(keyDef, isFullWidth, isEnglishPunct);
    el.appendChild(sub);
    el.appendChild(main);
  } else {
    el.textContent = render_keyLabel(keyDef, shiftState, capsActive, isEnglish, isFullWidth, isEnglishPunct);
  }
  return el;
}
function render_keyClass(keyDef, shiftState, capsActive, modifiers) {
  let cls = "rime-tkl-key";
  if (keyDef.action) {
    cls += " rime-tkl-key-fn";
    if (keyDef.action === "space") cls += " rime-tkl-key-space";
    if (keyDef.action === "shift") cls += " rime-tkl-key-shift";
    if (keyDef.action === "ctrl" || keyDef.action === "alt" || keyDef.action === "meta") {
      cls += " rime-tkl-key-mod";
    }
  }
  if (keyDef.action === "shift" && shiftState !== "off") {
    cls += " rime-tkl-key-active";
  }
  if (keyDef.action === "caps" && capsActive) {
    cls += " rime-tkl-key-active";
  }
  if (keyDef.action === "ctrl" && modifiers.has("ctrl")) {
    cls += " rime-tkl-key-active";
  }
  if (keyDef.action === "alt" && modifiers.has("alt")) {
    cls += " rime-tkl-key-active";
  }
  if (keyDef.action === "meta" && modifiers.has("meta")) {
    cls += " rime-tkl-key-active";
  }
  return cls;
}
function render_keyLabel(keyDef, shiftState, capsActive, isEnglish, isFullWidth, isEnglishPunct) {
  if (keyDef.action) {
    if (keyDef.action === "lang") return isEnglish ? "En" : "\u4E2D";
    if (keyDef.action === "shift") return shiftState === "locked" ? "\u21EA" : "\u21E7";
    if (keyDef.action === "caps") return capsActive ? "\u21EA" : "Caps";
    if (keyDef.action === "space") return "";
    return keyDef.label;
  }
  if (/^[a-z]$/.test(keyDef.key)) {
    const upper = shiftState !== "off" || capsActive;
    const s2 = upper ? keyDef.shiftLabel || keyDef.key.toUpperCase() : keyDef.label;
    if (isFullWidth) return toFullWidth(s2);
    return s2;
  }
  let s;
  if (shiftState !== "off" && keyDef.shiftLabel) s = keyDef.shiftLabel;
  else s = keyDef.label;
  const useCnPunct = isFullWidth || !isEnglishPunct;
  if (useCnPunct) {
    const mapped = FULLWIDTH_PUNCT_MAP[s];
    if (mapped) return mapped;
    if (s === "'") return "\u2018";
    if (s === '"') return "\u201C";
  }
  if (isFullWidth) return toFullWidth(s);
  return s;
}
function keyMainLabel(keyDef, isFullWidth, isEnglishPunct) {
  const s = keyDef.label;
  const useCnPunct = isFullWidth || !isEnglishPunct;
  if (useCnPunct) {
    const mapped = FULLWIDTH_PUNCT_MAP[s];
    if (mapped) return mapped;
    if (s === "'") return "\u2018";
    if (s === '"') return "\u201C";
  }
  if (isFullWidth) return toFullWidth(s);
  return s;
}
function keySubLabel(keyDef, isFullWidth, isEnglishPunct) {
  const s = keyDef.shiftLabel || "";
  if (!s) return "";
  const useCnPunct = isFullWidth || !isEnglishPunct;
  if (useCnPunct) {
    const mapped = FULLWIDTH_PUNCT_MAP[s];
    if (mapped) return mapped;
    if (s === "'") return "\u2018";
    if (s === '"') return "\u201C";
  }
  if (isFullWidth) return toFullWidth(s);
  return s;
}
function isModifierAction(action) {
  return action === "ctrl" || action === "alt" || action === "meta" || action === "shift";
}

;// ./src/keyboard/tkl/touch.ts


class TKLTouchHandler {
  constructor(keysEl, containerEl, dom, callbacks) {
    this.activeKeyEl = null;
    this.activeKeyDef = null;
    this.backspaceTimer = null;
    this.destroyed = false;
    /** 标记触摸在键盘上 */
    this.keyTouched = false;
    this.keysEl = keysEl;
    this.containerEl = containerEl;
    this.dom = dom;
    this.cb = callbacks;
    this.boundTouchStart = (e) => this.onTouchStart(e);
    this.boundTouchMove = (e) => this.onTouchMove(e);
    this.boundTouchEnd = (e) => this.onTouchEnd(e);
    this.boundMouseDown = (e) => this.onMouseDown(e);
    this.boundMouseMove = (e) => this.onMouseMove(e);
    this.boundMouseUp = (e) => this.onMouseUp(e);
    this.boundContainerTouchStart = (e) => this.onContainerTouchStart(e);
    this.boundContainerMouseDown = (e) => this.onContainerMouseDown(e);
  }
  bind() {
    this.keysEl.addEventListener("touchstart", this.boundTouchStart, { passive: false });
    this.keysEl.addEventListener("touchmove", this.boundTouchMove, { passive: false });
    this.keysEl.addEventListener("touchend", this.boundTouchEnd, { passive: false });
    this.keysEl.addEventListener("touchcancel", this.boundTouchEnd, { passive: false });
    this.keysEl.addEventListener("mousedown", this.boundMouseDown);
    document.addEventListener("mousemove", this.boundMouseMove);
    document.addEventListener("mouseup", this.boundMouseUp);
    this.containerEl.addEventListener("touchmove", (e) => {
      e.preventDefault();
    }, { passive: false });
    this.containerEl.addEventListener("touchstart", this.boundContainerTouchStart, { passive: true });
    this.containerEl.addEventListener("mousedown", this.boundContainerMouseDown);
  }
  destroy() {
    this.destroyed = true;
    this.clearTimers();
    this.keysEl.removeEventListener("touchstart", this.boundTouchStart);
    this.keysEl.removeEventListener("touchmove", this.boundTouchMove);
    this.keysEl.removeEventListener("touchend", this.boundTouchEnd);
    this.keysEl.removeEventListener("touchcancel", this.boundTouchEnd);
    this.keysEl.removeEventListener("mousedown", this.boundMouseDown);
    document.removeEventListener("mousemove", this.boundMouseMove);
    document.removeEventListener("mouseup", this.boundMouseUp);
    this.containerEl.removeEventListener("touchstart", this.boundContainerTouchStart);
    this.containerEl.removeEventListener("mousedown", this.boundContainerMouseDown);
  }
  // ─── 触摸事件 ───
  onTouchStart(e) {
    e.preventDefault();
    this.keyTouched = true;
    this.handlePointerDown(e.touches[0].clientX, e.touches[0].clientY);
  }
  onTouchMove(e) {
    e.preventDefault();
    if (!this.activeKeyEl) return;
    this.handlePointerMove(e.touches[0].clientX, e.touches[0].clientY);
  }
  onTouchEnd(e) {
    e.preventDefault();
    this.handlePointerUp();
    setTimeout(() => {
      this.keyTouched = false;
    }, 200);
  }
  onMouseDown(e) {
    e.preventDefault();
    this.keyTouched = true;
    this.handlePointerDown(e.clientX, e.clientY);
  }
  onMouseMove(e) {
    if (!this.activeKeyEl) return;
    this.handlePointerMove(e.clientX, e.clientY);
  }
  onMouseUp(_e) {
    this.handlePointerUp();
    setTimeout(() => {
      this.keyTouched = false;
    }, 200);
  }
  // ─── 容器级触摸标记 ───
  onContainerTouchStart(_e) {
    this.keyTouched = true;
    setTimeout(() => {
      this.keyTouched = false;
    }, 400);
  }
  onContainerMouseDown(_e) {
    this.keyTouched = true;
    setTimeout(() => {
      this.keyTouched = false;
    }, 400);
  }
  // ─── 统一指针处理 ───
  handlePointerDown(cx, cy) {
    const keyEl = this.getKeyAtPoint(cx, cy);
    if (!keyEl) return;
    this.activeKeyEl = keyEl;
    this.activeKeyDef = this.cb.getKeyDef(keyEl.dataset.key || "");
    if (this.activeKeyDef && !isModifierAction(this.activeKeyDef.action)) {
      keyEl.classList.add("rime-tkl-key-pressed");
    }
    this.cb.haptic();
    this.startBackspaceRepeat(this.activeKeyDef);
  }
  handlePointerMove(cx, cy) {
    const keyEl = this.getKeyAtPoint(cx, cy);
    if (keyEl !== this.activeKeyEl) {
      if (this.activeKeyEl) this.activeKeyEl.classList.remove("rime-tkl-key-pressed");
      this.clearTimers();
      if (keyEl) {
        this.activeKeyEl = keyEl;
        this.activeKeyDef = this.cb.getKeyDef(keyEl.dataset.key || "");
        if (this.activeKeyDef && !isModifierAction(this.activeKeyDef.action)) {
          keyEl.classList.add("rime-tkl-key-pressed");
        }
        this.startBackspaceRepeat(this.activeKeyDef);
      } else {
        this.activeKeyEl = null;
        this.activeKeyDef = null;
      }
    }
  }
  handlePointerUp() {
    this.clearTimers();
    if (this.activeKeyDef && this.activeKeyEl) {
      this.cb.fireKey(this.activeKeyDef);
    }
    if (this.activeKeyEl) this.activeKeyEl.classList.remove("rime-tkl-key-pressed");
    this.activeKeyEl = null;
    this.activeKeyDef = null;
  }
  // ─── 按键定位 ───
  getKeyAtPoint(cx, cy) {
    const pv = this.dom.preview.style.display;
    this.dom.preview.style.display = "none";
    const el = document.elementFromPoint(cx, cy);
    this.dom.preview.style.display = pv;
    if (!el) return null;
    return el.closest(".rime-tkl-key") || null;
  }
  // ─── Backspace 自动重复 ───
  startBackspaceRepeat(keyDef) {
    if (!keyDef || keyDef.action !== "backspace") return;
    this.backspaceTimer = setTimeout(() => {
      const repeat = () => {
        if (!this.activeKeyEl || this.activeKeyDef?.action !== "backspace") return;
        this.cb.fireKey(this.activeKeyDef);
        this.backspaceTimer = setTimeout(repeat, 80);
      };
      this.backspaceTimer = setTimeout(repeat, 80);
    }, 500);
  }
  clearTimers() {
    if (this.backspaceTimer !== null) {
      clearTimeout(this.backspaceTimer);
      this.backspaceTimer = null;
    }
  }
}

;// ./src/keyboard/tkl/viewport.ts

class TKLViewportController {
  constructor(container, target, dragHandle, getVisible, getKeyTouched, getShowOnFocus, onShow, onHide, floatingWidth = 780, floatingHeight = 280) {
    // 拖拽状态
    this.dragging = false;
    this.dragStartX = 0;
    this.dragStartY = 0;
    this.posX = 0;
    this.posY = 0;
    // 目标元素属性备份
    this.savedInputMode = null;
    this.savedAutoComplete = null;
    this.container = container;
    this.target = target;
    this.dragHandle = dragHandle;
    this.getVisible = getVisible;
    this.getKeyTouched = getKeyTouched;
    this.getShowOnFocus = getShowOnFocus;
    this.onShow = onShow;
    this.onHide = onHide;
    this.floatingWidth = floatingWidth;
    this.floatingHeight = floatingHeight;
    this.boundFocus = () => this.onTargetFocus();
    this.boundBlur = () => this.onTargetBlur();
    this.boundClick = () => this.onTargetClick();
    this.boundPointerDown = () => this.onTargetClick();
    this.boundResize = () => this.onResize();
    this.boundDragTouchStart = (e) => this.onDragTouchStart(e);
    this.boundDragTouchMove = (e) => this.onDragTouchMove(e);
    this.boundDragTouchEnd = () => {
      this.onDragEnd();
    };
    this.boundDragMouseStart = (e) => this.onDragMouseStart(e);
    this.boundDragMouseMove = (e) => this.onDragMouseMove(e);
    this.boundDragMouseEnd = () => {
      this.onDragEnd();
    };
  }
  setupTarget() {
    if (this.isTextInput(this.target)) {
      this.savedInputMode = this.target.getAttribute("inputmode");
      this.savedAutoComplete = this.target.getAttribute("autocomplete");
      this.target.setAttribute("inputmode", "none");
      this.target.setAttribute("autocomplete", "off");
    }
  }
  restoreTarget() {
    if (this.isTextInput(this.target)) {
      if (this.savedInputMode !== null) this.target.setAttribute("inputmode", this.savedInputMode);
      else this.target.removeAttribute("inputmode");
      if (this.savedAutoComplete !== null) this.target.setAttribute("autocomplete", this.savedAutoComplete);
      else this.target.removeAttribute("autocomplete");
    }
  }
  bind() {
    this.target.addEventListener("focus", this.boundFocus);
    this.target.addEventListener("blur", this.boundBlur);
    this.target.addEventListener("click", this.boundClick);
    this.target.addEventListener("pointerdown", this.boundPointerDown);
    window.addEventListener("resize", this.boundResize);
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", this.boundResize);
      window.visualViewport.addEventListener("scroll", this.boundResize);
    }
    this.target.addEventListener("compositionstart", (e) => {
      e.preventDefault();
      try {
        this.target.value = this.target.value;
      } catch {
      }
    });
    this.dragHandle.addEventListener("touchstart", this.boundDragTouchStart, { passive: false });
    document.addEventListener("touchmove", this.boundDragTouchMove, { passive: false });
    document.addEventListener("touchend", this.boundDragTouchEnd);
    this.dragHandle.addEventListener("mousedown", this.boundDragMouseStart);
    document.addEventListener("mousemove", this.boundDragMouseMove);
    document.addEventListener("mouseup", this.boundDragMouseEnd);
  }
  destroy() {
    this.target.removeEventListener("focus", this.boundFocus);
    this.target.removeEventListener("blur", this.boundBlur);
    this.target.removeEventListener("click", this.boundClick);
    this.target.removeEventListener("pointerdown", this.boundPointerDown);
    window.removeEventListener("resize", this.boundResize);
    if (window.visualViewport) {
      window.visualViewport.removeEventListener("resize", this.boundResize);
      window.visualViewport.removeEventListener("scroll", this.boundResize);
    }
    this.dragHandle.removeEventListener("touchstart", this.boundDragTouchStart);
    document.removeEventListener("touchmove", this.boundDragTouchMove);
    document.removeEventListener("touchend", this.boundDragTouchEnd);
    this.dragHandle.removeEventListener("mousedown", this.boundDragMouseStart);
    document.removeEventListener("mousemove", this.boundDragMouseMove);
    document.removeEventListener("mouseup", this.boundDragMouseEnd);
  }
  // ─── Show / Hide ───
  show() {
    this.container.classList.remove("rime-tkl-hidden");
    this.onShow();
  }
  hide() {
    this.container.classList.add("rime-tkl-hidden");
    this.onHide();
  }
  focusTarget() {
    if (this.isTextInput(this.target) && document.activeElement !== this.target) {
      this.target.focus();
    }
  }
  // ─── Focus / Blur ───
  onTargetFocus() {
    if (!this.getShowOnFocus()) return;
    this.show();
  }
  onTargetClick() {
    if (!this.getShowOnFocus()) return;
    this.show();
  }
  onTargetBlur() {
    if (this.getKeyTouched()) {
      this.focusTarget();
      return;
    }
    setTimeout(() => {
      if (document.activeElement !== this.target && !this.getKeyTouched()) {
        const active = document.activeElement;
        if (active && active.closest(".rime-toolbar")) {
          this.focusTarget();
          return;
        }
        this.hide();
      }
    }, 150);
  }
  // ─── 视口调整 ───
  onResize() {
    this.adjustFloatingSize();
  }
  adjustFloatingSize() {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const maxW = Math.min(vw - 16, this.floatingWidth);
    const maxH = Math.min(vh - 16, this.floatingHeight);
    this.container.style.maxWidth = maxW + "px";
    this.container.style.maxHeight = maxH + "px";
  }
  // ─── 拖拽 ───
  onDragTouchStart(e) {
    if (e.target.closest(".rime-tkl-tb-hide")) return;
    e.preventDefault();
    e.stopPropagation();
    this.startDrag(e.touches[0].clientX, e.touches[0].clientY);
  }
  onDragTouchMove(e) {
    if (!this.dragging) return;
    e.preventDefault();
    this.moveDrag(e.touches[0].clientX, e.touches[0].clientY);
  }
  onDragMouseStart(e) {
    e.preventDefault();
    e.stopPropagation();
    this.startDrag(e.clientX, e.clientY);
  }
  onDragMouseMove(e) {
    if (!this.dragging) return;
    this.moveDrag(e.clientX, e.clientY);
  }
  onDragEnd() {
    this.dragging = false;
  }
  startDrag(cx, cy) {
    this.dragging = true;
    this.dragStartX = cx;
    this.dragStartY = cy;
    const cs = getComputedStyle(this.container);
    this.posX = parseFloat(cs.left);
    this.posY = parseFloat(cs.top);
  }
  moveDrag(cx, cy) {
    this.posX += cx - this.dragStartX;
    this.posY += cy - this.dragStartY;
    this.dragStartX = cx;
    this.dragStartY = cy;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const w = this.container.offsetWidth;
    const h = this.container.offsetHeight;
    if (this.posX < 0) this.posX = 0;
    if (this.posX + w > vw) this.posX = vw - w;
    if (this.posY < 0) this.posY = 0;
    if (this.posY + h > vh) this.posY = vh - h;
    this.container.style.left = this.posX + "px";
    this.container.style.top = this.posY + "px";
  }
  // ─── 工具 ───
  isTextInput(el) {
    return el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement;
  }
}

;// ./src/keyboard/tkl/theme.ts

const tkl_theme_THEME_PRESETS = {
  dark: {
    tklBg: "rgba(30,30,34,.98)",
    tklBorder: "rgba(255,255,255,.06)",
    tklShadow: "0 4px 24px rgba(0,0,0,.5)",
    keyBg: "rgba(58,58,64,.92)",
    keyColor: "rgba(255,255,255,.88)",
    keyActiveBg: "rgba(112,192,232,.35)",
    keyActiveColor: "#fff",
    fnKeyBg: "rgba(48,48,54,.92)",
    fnKeyColor: "rgba(255,255,255,.6)",
    modKeyBg: "rgba(42,42,48,.92)",
    modKeyColor: "rgba(255,255,255,.6)",
    spaceBg: "rgba(58,58,64,.92)",
    spaceColor: "rgba(255,255,255,.5)",
    toolbarBg: "rgba(24,24,28,.96)",
    toolbarBtnColor: "rgba(255,255,255,.45)",
    toolbarBtnActiveColor: "#70c0e8",
    previewBg: "rgba(58,58,64,.96)",
    previewColor: "#fff"
  },
  light: {
    tklBg: "rgba(245,245,247,.98)",
    tklBorder: "rgba(0,0,0,.08)",
    tklShadow: "0 4px 24px rgba(0,0,0,.1)",
    keyBg: "rgba(255,255,255,.95)",
    keyColor: "rgba(0,0,0,.85)",
    keyActiveBg: "rgba(32,128,240,.2)",
    keyActiveColor: "#2080f0",
    fnKeyBg: "rgba(230,230,233,.95)",
    fnKeyColor: "rgba(0,0,0,.55)",
    modKeyBg: "rgba(220,220,224,.95)",
    modKeyColor: "rgba(0,0,0,.55)",
    spaceBg: "rgba(255,255,255,.95)",
    spaceColor: "rgba(0,0,0,.4)",
    toolbarBg: "rgba(255,255,255,.98)",
    toolbarBtnColor: "rgba(0,0,0,.4)",
    toolbarBtnActiveColor: "#2080f0",
    previewBg: "rgba(255,255,255,.98)",
    previewColor: "rgba(0,0,0,.85)"
  }
};
const theme_DEFAULT_THEME_VARS = {
  tklBg: "rgba(30,30,34,.98)",
  tklBorder: "rgba(255,255,255,.06)",
  tklRadius: "10px",
  tklShadow: "0 4px 24px rgba(0,0,0,.5)",
  tklFontFamily: '-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif',
  tklZIndex: 99999,
  keyBg: "rgba(58,58,64,.92)",
  keyColor: "rgba(255,255,255,.88)",
  keyFontSize: "12px",
  keyHeight: "32px",
  keyGap: "2px",
  keyRadius: "4px",
  keyActiveBg: "rgba(112,192,232,.35)",
  keyActiveColor: "#fff",
  fnKeyBg: "rgba(48,48,54,.92)",
  fnKeyColor: "rgba(255,255,255,.6)",
  modKeyBg: "rgba(42,42,48,.92)",
  modKeyColor: "rgba(255,255,255,.6)",
  spaceBg: "rgba(58,58,64,.92)",
  spaceColor: "rgba(255,255,255,.5)",
  toolbarBg: "rgba(24,24,28,.96)",
  toolbarBtnColor: "rgba(255,255,255,.45)",
  toolbarBtnActiveColor: "#70c0e8",
  previewBg: "rgba(58,58,64,.96)",
  previewColor: "#fff",
  previewFontSize: "18px",
  previewRadius: "4px"
};
function resolveTKLThemeVars(theme, overrides) {
  const base = { ...tkl_theme_THEME_PRESETS[theme] };
  return { ...theme_DEFAULT_THEME_VARS, ...base, ...overrides };
}
function applyTKLThemeVars(container, vars) {
  const s = container.style;
  const set = (prop, val) => {
    if (val !== void 0) s.setProperty(prop, String(val));
  };
  set("--rime-tkl-bg", vars.tklBg);
  set("--rime-tkl-border", vars.tklBorder);
  set("--rime-tkl-radius", vars.tklRadius);
  set("--rime-tkl-shadow", vars.tklShadow);
  set("--rime-tkl-font-family", vars.tklFontFamily);
  set("--rime-tkl-z-index", vars.tklZIndex);
  set("--rime-tkl-key-bg", vars.keyBg);
  set("--rime-tkl-key-color", vars.keyColor);
  set("--rime-tkl-key-font-size", vars.keyFontSize);
  set("--rime-tkl-key-height", vars.keyHeight);
  set("--rime-tkl-key-gap", vars.keyGap);
  set("--rime-tkl-key-radius", vars.keyRadius);
  set("--rime-tkl-key-active-bg", vars.keyActiveBg);
  set("--rime-tkl-key-active-color", vars.keyActiveColor);
  set("--rime-tkl-fn-key-bg", vars.fnKeyBg);
  set("--rime-tkl-fn-key-color", vars.fnKeyColor);
  set("--rime-tkl-mod-key-bg", vars.modKeyBg);
  set("--rime-tkl-mod-key-color", vars.modKeyColor);
  set("--rime-tkl-space-bg", vars.spaceBg);
  set("--rime-tkl-space-color", vars.spaceColor);
  set("--rime-tkl-toolbar-bg", vars.toolbarBg);
  set("--rime-tkl-toolbar-btn-color", vars.toolbarBtnColor);
  set("--rime-tkl-toolbar-btn-active-color", vars.toolbarBtnActiveColor);
  set("--rime-tkl-preview-bg", vars.previewBg);
  set("--rime-tkl-preview-color", vars.previewColor);
  set("--rime-tkl-preview-font-size", vars.previewFontSize);
  set("--rime-tkl-preview-radius", vars.previewRadius);
}
const TKL_STYLE_ID = "rime-tkl-style";
let tklStyleInstanceCount = 0;
function injectTKLStyle() {
  if (document.getElementById(TKL_STYLE_ID)) {
    tklStyleInstanceCount++;
    return;
  }
  const s = document.createElement("style");
  s.id = TKL_STYLE_ID;
  s.textContent = TKL_CSS;
  document.head.appendChild(s);
  tklStyleInstanceCount++;
}
function removeTKLStyle() {
  tklStyleInstanceCount--;
  if (tklStyleInstanceCount <= 0) {
    const st = document.getElementById(TKL_STYLE_ID);
    if (st) st.remove();
    tklStyleInstanceCount = 0;
  }
}
const TKL_CSS = `
.rime-tkl{position:fixed;z-index:var(--rime-tkl-z-index,99999);font-family:var(--rime-tkl-font-family,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif);user-select:none;-webkit-user-select:none;touch-action:none;overflow:hidden;transition:transform .25s ease,opacity .2s ease;display:flex;flex-direction:column;border-radius:var(--rime-tkl-radius,10px);border:1px solid var(--rime-tkl-border,rgba(255,255,255,.06));box-shadow:var(--rime-tkl-shadow,0 4px 24px rgba(0,0,0,.5))}
.rime-tkl-hidden{transform:translateY(100vh);opacity:0;pointer-events:none}
.rime-tkl-toolbar{display:flex;align-items:center;justify-content:center;position:relative;padding:2px 6px;height:18px;flex-shrink:0;background:var(--rime-tkl-toolbar-bg,rgba(24,24,28,.96));border-bottom:1px solid var(--rime-tkl-border,rgba(255,255,255,.06));cursor:grab;border-radius:var(--rime-tkl-radius,10px) var(--rime-tkl-radius,10px) 0 0}
.rime-tkl-toolbar:active{cursor:grabbing}
.rime-tkl-tb-drag{color:rgba(255,255,255,.18);font-size:10px;font-weight:700;line-height:1;letter-spacing:3px;pointer-events:none}
.rime-tkl-tb-hide{position:absolute;right:6px;top:50%;transform:translateY(-50%);background:transparent;border:none;color:var(--rime-tkl-toolbar-btn-color,rgba(255,255,255,.45));font-size:8px;cursor:pointer;padding:2px 4px;line-height:1;border-radius:3px;-webkit-tap-highlight-color:transparent}
.rime-tkl-tb-hide:hover{color:var(--rime-tkl-toolbar-btn-active-color,#70c0e8)}
.rime-tkl-keys{padding:4px 6px 4px;display:flex;flex-direction:column;gap:var(--rime-tkl-key-gap,2px);background:var(--rime-tkl-bg,rgba(30,30,34,.98));border-radius:0 0 var(--rime-tkl-radius,10px) var(--rime-tkl-radius,10px)}
.rime-tkl-row{display:flex;justify-content:center;gap:var(--rime-tkl-key-gap,2px);align-items:stretch}
.rime-tkl-key{display:flex;align-items:center;justify-content:center;flex:1;min-height:var(--rime-tkl-key-height,32px);background:var(--rime-tkl-key-bg,rgba(58,58,64,.92));color:var(--rime-tkl-key-color,rgba(255,255,255,.88));border:none;border-radius:var(--rime-tkl-key-radius,4px);font-size:var(--rime-tkl-key-font-size,12px);font-family:inherit;cursor:pointer;transition:transform .08s,background .08s;-webkit-tap-highlight-color:transparent;outline:none;padding:0;white-space:nowrap}
.rime-tkl-spacer{visibility:hidden;pointer-events:none;min-height:var(--rime-tkl-key-height,32px)}
.rime-tkl-key-dual{position:relative;flex-direction:column;justify-content:flex-end;align-items:center;padding-bottom:3px}
.rime-tkl-sub{position:absolute;top:1px;right:3px;font-size:calc(var(--rime-tkl-key-font-size,12px) * 0.6);opacity:.4;line-height:1;pointer-events:none}
.rime-tkl-main{line-height:1;pointer-events:none}
.rime-tkl-key-dual-shift .rime-tkl-sub{opacity:.4}
.rime-tkl-key-dual-shift .rime-tkl-main{font-weight:600}
.rime-tkl-key:active,.rime-tkl-key-pressed{background:var(--rime-tkl-key-active-bg,rgba(112,192,232,.35));color:var(--rime-tkl-key-active-color,#fff);transform:scale(0.96)}
.rime-tkl-key-fn{background:var(--rime-tkl-fn-key-bg,rgba(48,48,54,.92));color:var(--rime-tkl-fn-key-color,rgba(255,255,255,.6));font-size:calc(var(--rime-tkl-key-font-size,12px) * 0.85)}
.rime-tkl-key-fn:active,.rime-tkl-key-fn.rime-tkl-key-pressed{background:var(--rime-tkl-key-active-bg,rgba(112,192,232,.35));color:var(--rime-tkl-key-active-color,#fff)}
.rime-tkl-key-mod{background:var(--rime-tkl-mod-key-bg,rgba(42,42,48,.92));color:var(--rime-tkl-mod-key-color,rgba(255,255,255,.6));font-size:calc(var(--rime-tkl-key-font-size,12px) * 0.8)}
.rime-tkl-key-mod:active,.rime-tkl-key-mod.rime-tkl-key-pressed{background:var(--rime-tkl-key-active-bg,rgba(112,192,232,.35));color:var(--rime-tkl-key-active-color,#fff)}
.rime-tkl-key-active{background:var(--rime-tkl-key-active-bg,rgba(112,192,232,.35))!important;color:var(--rime-tkl-key-active-color,#fff)!important}
.rime-tkl-key-space{background:var(--rime-tkl-space-bg,rgba(58,58,64,.92));color:var(--rime-tkl-space-color,rgba(255,255,255,.5));font-size:calc(var(--rime-tkl-key-font-size,12px) * 0.75);letter-spacing:2px}
.rime-tkl-preview{position:fixed;z-index:100000;display:flex;align-items:center;justify-content:center;min-width:36px;height:40px;padding:4px 10px;background:var(--rime-tkl-preview-bg,rgba(58,58,64,.96));color:var(--rime-tkl-preview-color,#fff);font-size:var(--rime-tkl-preview-font-size,18px);border-radius:var(--rime-tkl-preview-radius,4px);box-shadow:0 2px 8px rgba(0,0,0,.3);pointer-events:none}
`;

;// ./src/keyboard/tkl/index.ts









const DIRECT_HANDLE_ACTIONS = /* @__PURE__ */ new Set([
  "f1",
  "f2",
  "f3",
  "f4",
  "f5",
  "f6",
  "f7",
  "f8",
  "f9",
  "f10",
  "f11",
  "f12",
  "insert",
  "delete",
  "home",
  "end",
  "pageup",
  "pagedown",
  "printscreen",
  "scrolllock",
  "pause"
]);
class RimeTKLKeyboard {
  constructor(config) {
    this._visible = false;
    this.destroyed = false;
    // 修饰键状态
    this.shiftState = "off";
    this.capsActive = false;
    this.modifiers = /* @__PURE__ */ new Set();
    // IME 状态
    this.isEnglish = false;
    this.isFullWidth = false;
    this.isEnglishPunct = false;
    this.editing = false;
    this.lastResult = null;
    // 智能引号 toggle 状态
    this._singleQuoteLeft = true;
    this._doubleQuoteLeft = true;
    // 回调
    this.showCallbacks = [];
    this.hideCallbacks = [];
    this.keyPressCallbacks = [];
    this.commitCallbacks = [];
    this.textInsertCallbacks = [];
    this.textDeleteCallbacks = [];
    this.target = config.target;
    this._showOnFocus = config.showOnFocus ?? true;
    this._floatingWidth = config.floatingWidth ?? 780;
    this._floatingHeight = config.floatingHeight ?? 260;
    this._eol = config.eol ?? "\r";
    this._currentTheme = config.theme ?? "dark";
    if (config.ime) {
      this.ime = config.ime;
      this._ownsIME = false;
    } else {
      this.ime = new RimeIME(config);
      this._ownsIME = true;
    }
    this.dom = createTKLKeyboardDOM();
    this.dom.container.classList.add("rime-tkl-hidden");
    document.body.appendChild(this.dom.container);
    document.body.appendChild(this.dom.preview);
    injectTKLStyle();
    const themeVars = resolveTKLThemeVars(this._currentTheme);
    applyTKLThemeVars(this.dom.container, themeVars);
    this.renderKeys();
    this.touch = new TKLTouchHandler(
      this.dom.keys,
      this.dom.container,
      this.dom,
      {
        fireKey: (kd) => this.fireKey(kd),
        haptic: () => this.haptic(),
        getKeyDef: (k) => this.getKeyDef(k)
      }
    );
    this.viewport = new TKLViewportController(
      this.dom.container,
      this.target,
      this.dom.dragHandle,
      () => this._visible,
      () => this.touch.keyTouched,
      () => this._showOnFocus,
      () => {
        this._visible = true;
        this.showCallbacks.forEach((cb) => cb());
      },
      () => {
        this._visible = false;
        this.hideCallbacks.forEach((cb) => cb());
      },
      this._floatingWidth,
      this._floatingHeight
    );
    this.viewport.setupTarget();
    this.touch.bind();
    this.viewport.bind();
    this.bindHideBtn();
    this.bindIME();
    this.applyFloatingPosition();
  }
  async init() {
    await this.ime.init();
  }
  destroy() {
    this.destroyed = true;
    if (this._ownsIME) this.ime.destroy();
    this.touch.destroy();
    this.viewport.destroy();
    this.viewport.restoreTarget();
    this.dom.container.remove();
    this.dom.preview.remove();
    removeTKLStyle();
  }
  getIME() {
    return this.ime;
  }
  isInitialized() {
    return this.ime.isInitialized();
  }
  getElement() {
    return this.dom.container;
  }
  show() {
    if (this._visible) return;
    this.viewport.show();
  }
  hide() {
    if (!this._visible) return;
    this.viewport.hide();
  }
  toggle() {
    this._visible ? this.hide() : this.show();
  }
  isVisible() {
    return this._visible;
  }
  setTheme(theme) {
    this._currentTheme = theme;
    const themeVars = resolveTKLThemeVars(theme);
    applyTKLThemeVars(this.dom.container, themeVars);
  }
  onShow(cb) {
    this.showCallbacks.push(cb);
  }
  onHide(cb) {
    this.hideCallbacks.push(cb);
  }
  onKeyPress(cb) {
    this.keyPressCallbacks.push(cb);
  }
  onCommit(cb) {
    this.commitCallbacks.push(cb);
  }
  onTextInsert(cb) {
    this.textInsertCallbacks.push(cb);
  }
  onTextDelete(cb) {
    this.textDeleteCallbacks.push(cb);
  }
  offShow(cb) {
    this.showCallbacks = this.showCallbacks.filter((c) => c !== cb);
  }
  offHide(cb) {
    this.hideCallbacks = this.hideCallbacks.filter((c) => c !== cb);
  }
  offKeyPress(cb) {
    this.keyPressCallbacks = this.keyPressCallbacks.filter((c) => c !== cb);
  }
  offCommit(cb) {
    this.commitCallbacks = this.commitCallbacks.filter((c) => c !== cb);
  }
  offTextInsert(cb) {
    this.textInsertCallbacks = this.textInsertCallbacks.filter((c) => c !== cb);
  }
  offTextDelete(cb) {
    this.textDeleteCallbacks = this.textDeleteCallbacks.filter((c) => c !== cb);
  }
  // ─── 渲染 ───
  renderKeys() {
    renderTKLKeys(this.dom.keys, this.shiftState, this.capsActive, this.modifiers, this.isEnglish, this.isFullWidth, this.isEnglishPunct);
  }
  refreshUI() {
    this.renderKeys();
  }
  // ─── 隐藏按钮 ───
  bindHideBtn() {
    this.dom.hideBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.hide();
    });
  }
  // ─── IME 事件 ───
  bindIME() {
    this.ime.onOptionChange((opts) => {
      if ("ascii_mode" in opts) {
        this.isEnglish = opts.ascii_mode;
        if (this.isEnglish) this.shiftState = "off";
      }
      if ("full_shape" in opts) this.isFullWidth = opts.full_shape;
      if ("ascii_punct" in opts) this.isEnglishPunct = opts.ascii_punct;
      this.refreshUI();
    });
    this.ime.onSchemaChange(() => {
      this.refreshUI();
    });
  }
  // ─── 按键动作 ───
  fireKey(keyDef) {
    const action = keyDef.action;
    if (action === "shift") {
      this.handleShift();
      return;
    }
    if (action === "ctrl") {
      this.toggleModifier("ctrl");
      return;
    }
    if (action === "alt") {
      this.toggleModifier("alt");
      return;
    }
    if (action === "meta") {
      this.toggleModifier("meta");
      return;
    }
    if (action === "caps") {
      this.handleCaps();
      return;
    }
    if (action === "lang") {
      this.handleLang();
      return;
    }
    if (action === "fn") {
      return;
    }
    const rimeKey = this.buildRimeKey(keyDef);
    if (!rimeKey) return;
    this.clearNonLockModifiers();
    this.keyPressCallbacks.forEach((cb) => cb(rimeKey));
    if (this.isDirectHandleKey(action, rimeKey)) {
      if (this.editing && !this.isEnglish) {
        this.ime.processKey("{Escape}").then(() => {
          this.editing = false;
          this.handleSpecialKey(action);
        }).catch(() => {
          this.handleSpecialKey(action);
        });
      } else {
        this.handleSpecialKey(action);
      }
      return;
    }
    if (action === "escape" || action && action.startsWith("arrow_")) {
      if (this.isEnglish || !this.editing) {
        this.handleSpecialKey(action);
        return;
      }
    }
    if (this.isEnglish || !this.editing) {
      if (this.handleDirectKey(action, rimeKey, keyDef)) return;
    }
    this.ime.processKey(rimeKey).then((r) => this.analyze(r, rimeKey)).catch(() => {
    });
  }
  /** 判断是否为 RIME 无法处理的键（功能键/导航键/修饰键组合） */
  isDirectHandleKey(action, rimeKey) {
    if (rimeKey.startsWith("{") && rimeKey.includes("+")) return true;
    if (action && DIRECT_HANDLE_ACTIONS.has(action)) return true;
    return false;
  }
  /** 处理 RIME 无法支持的特殊键（功能键/导航键/方向键/Escape/修饰键组合）
   *
   * onKeyPress 回调已在 fireKey 中触发，外部监听器（如终端）可获取 RIME 格式键名
   * 并转换为终端转义序列。此处为 textarea/input 目标提供基本光标移动支持。
   */
  handleSpecialKey(action) {
    if (!this.isTextInput(this.target)) return;
    const el = this.target;
    const s = el.selectionStart ?? el.value.length;
    const e = el.selectionEnd ?? s;
    const v = el.value;
    switch (action) {
      case "arrow_left":
        el.selectionStart = el.selectionEnd = Math.max(0, s === e ? s - 1 : s);
        break;
      case "arrow_right":
        el.selectionStart = el.selectionEnd = Math.min(v.length, s === e ? s + 1 : e);
        break;
      case "home":
        el.selectionStart = el.selectionEnd = 0;
        break;
      case "end":
        el.selectionStart = el.selectionEnd = v.length;
        break;
      case "delete":
        if (s !== e) {
          el.value = v.slice(0, s) + v.slice(e);
          el.selectionStart = el.selectionEnd = s;
        } else if (e < v.length) {
          el.value = v.slice(0, s) + v.slice(e + 1);
          el.selectionStart = el.selectionEnd = s;
        }
        this.target.dispatchEvent(new Event("input", { bubbles: true }));
        break;
    }
    el.focus();
  }
  /** 构建发送给 RIME 引擎的键名 */
  buildRimeKey(keyDef) {
    const action = keyDef.action;
    if (action) {
      const rimeName = TKL_RIME_KEY_MAP[keyDef.key];
      if (rimeName) {
        if (this.modifiers.size > 0) {
          const modNames = [];
          this.modifiers.forEach((m) => modNames.push(MODIFIER_NAMES[m]));
          return buildRimeCombo(modNames, rimeName);
        }
        return `{${rimeName}}`;
      }
      return `{${action}}`;
    }
    let ch = keyDef.key;
    if (this.shiftState !== "off" && keyDef.shiftKey) {
      ch = keyDef.shiftKey;
    }
    if (this.capsActive && /^[a-z]$/i.test(ch)) {
      ch = ch.toUpperCase();
    }
    if (this.modifiers.size > 0) {
      const rimeName = TKL_RIME_KEY_MAP[ch] || ch;
      const modNames = [];
      this.modifiers.forEach((m) => modNames.push(MODIFIER_NAMES[m]));
      return buildRimeCombo(modNames, rimeName);
    }
    return ch;
  }
  /** 英文/非编辑态下直接处理的按键，返回 true 表示已处理 */
  handleDirectKey(action, rimeKey, keyDef) {
    if (action === "backspace") {
      this.deleteBackward();
      return true;
    }
    if (action === "enter") {
      this.insertText(this._eol);
      return true;
    }
    if (action === "space") {
      this.insertText(this.isFullWidth ? "\u3000" : " ");
      return true;
    }
    if (action === "tab") {
      this.insertText("	");
      return true;
    }
    if (action) {
      return true;
    }
    if (!this.isEnglish && /^[a-z]$/.test(keyDef.key)) {
      return false;
    }
    if (!action && rimeKey.length === 1) {
      this.insertText(this.convertChar(rimeKey));
      return true;
    }
    return false;
  }
  // ─── 修饰键处理 ───
  handleShift() {
    if (this.shiftState === "off") this.shiftState = "once";
    else if (this.shiftState === "once") this.shiftState = "locked";
    else this.shiftState = "off";
    this.refreshUI();
  }
  handleCaps() {
    this.capsActive = !this.capsActive;
    this.refreshUI();
  }
  handleLang() {
    this.isEnglish = !this.isEnglish;
    this.ime.setOption("ascii_mode", this.isEnglish).catch(() => {
    });
    if (!this.ime.punctLocked) {
      this.ime.setOption("ascii_punct", this.isEnglish).catch(() => {
      });
    }
    // 切换到英文模式时，取消当前组词（清除候选词列表）
    // 与 rimeManager.js setupShiftToggle 和工具栏 btnLang 的处理一致
    if (this.isEnglish) {
      this.shiftState = "off";
      if (this.editing) {
        this.ime.processKey('{Escape}').catch(() => {
        });
      }
    }
    this.refreshUI();
  }
  toggleModifier(mod) {
    if (this.modifiers.has(mod)) {
      this.modifiers.delete(mod);
    } else {
      this.modifiers.add(mod);
    }
    this.refreshUI();
  }
  clearNonLockModifiers() {
    if (this.shiftState === "once") {
      this.shiftState = "off";
    }
    this.modifiers.clear();
    this.refreshUI();
  }
  // ─── 分析 RIME 返回结果 ───
  analyze(r, rimeKey) {
    this.lastResult = r;
    const wasEditing = this.editing;
    if (r.state === "committed") {
      this.editing = false;
      if (r.committed) {
        if (this._ownsIME) this.insertText(r.committed);
        this.commitCallbacks.forEach((cb) => cb(r.committed));
      }
    } else if (r.state === "accepted") {
      if (r.committed) {
        if (this._ownsIME) this.insertText(r.committed);
        this.commitCallbacks.forEach((cb) => cb(r.committed));
      }
      this.editing = true;
    } else {
      this.editing = false;
      if (r.state === "rejected" && r.updatedSchema) {
        this.ime.setIME(r.updatedSchema.split("/")[0]).then((nr) => this.analyze(nr, "")).catch(() => {
        });
      }
      if (r.state === "unhandled" && !wasEditing) {
        if (rimeKey === "{BackSpace}" || rimeKey === "BackSpace") {
          this.deleteBackward();
        } else if (rimeKey === "{Return}" || rimeKey === "Return") {
          this.insertText(this._eol);
        } else if (rimeKey.length === 1 && this.isPrintable(rimeKey)) {
          this.insertText(this.convertChar(rimeKey));
        }
      }
    }
    if (this.shiftState === "once") {
      this.shiftState = "off";
      this.renderKeys();
    }
    this.viewport.focusTarget();
  }
  // ─── 文字操作 ───
  insertText(text) {
    this.editing = false;
    if (this.isTextInput(this.target)) {
      const el = this.target;
      const s = el.selectionStart ?? el.value.length;
      const e = el.selectionEnd ?? s;
      const v = el.value;
      el.value = v.slice(0, s) + text + v.slice(e);
      el.selectionStart = el.selectionEnd = s + text.length;
      this.target.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      this.textInsertCallbacks.forEach((cb) => cb(text));
    }
  }
  deleteBackward() {
    if (this.isTextInput(this.target)) {
      const el = this.target;
      const s = el.selectionStart ?? el.value.length;
      const e = el.selectionEnd ?? s;
      const v = el.value;
      if (s !== e) {
        el.value = v.slice(0, s) + v.slice(e);
        el.selectionStart = el.selectionEnd = s;
      } else if (s > 0) {
        el.value = v.slice(0, s - 1) + v.slice(e);
        el.selectionStart = el.selectionEnd = s - 1;
      }
      this.target.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      this.textDeleteCallbacks.forEach((cb) => cb());
    }
  }
  // ─── 工具 ───
  getKeyDef(key) {
    const layout = getTKLLayout();
    for (const row of layout) {
      for (const def of row) {
        if (def.key === key) return def;
      }
    }
    return null;
  }
  isTextInput(el) {
    return el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement;
  }
  isPrintable(key) {
    return /^[a-z0-9!"#$%&'()*+,./:;<=>?@[\] ^_`{|}~\\-]$/i.test(key);
  }
  /**
   * 符号/字母数字转换（不受中英文影响）。
   * 符号受 isFullWidth（满月/半月）和 isEnglishPunct（符号全半角）共同控制，全角优先：
   *   - isFullWidth=true（满月）→ 强制全部全角：列表内→中文符号，列表外→全角英文符号，字母数字→全角
   *   - isFullWidth=false + isEnglishPunct=false（符号全角）→ 仅列表内中文符号，列表外 ASCII
   *   - isFullWidth=false + isEnglishPunct=true（符号半角）→ ASCII
   * 字母数字受 isFullWidth 控制：满月→全角，半月→ASCII
   */
  convertChar(ch) {
    const useCnPunct = this.isFullWidth || !this.isEnglishPunct;
    if (useCnPunct) {
      const mapped = FULLWIDTH_PUNCT_MAP[ch];
      if (mapped) return mapped;
      if (ch === "'") {
        const result = this._singleQuoteLeft ? "\u2018" : "\u2019";
        this._singleQuoteLeft = !this._singleQuoteLeft;
        return result;
      }
      if (ch === '"') {
        const result = this._doubleQuoteLeft ? "\u201C" : "\u201D";
        this._doubleQuoteLeft = !this._doubleQuoteLeft;
        return result;
      }
    }
    if (this.isFullWidth) return toFullWidth(ch);
    return ch;
  }
  haptic() {
    try {
      navigator.vibrate?.(8);
    } catch {
    }
  }
  applyFloatingPosition() {
    const el = this.dom.container;
    el.style.width = this._floatingWidth + "px";
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const w = Math.min(this._floatingWidth, vw - 16);
    const h = Math.min(this._floatingHeight, vh - 16);
    let left = (vw - w) / 2;
    let top = vh - h - 40;
    left = Math.max(8, Math.min(left, vw - w - 8));
    top = Math.max(8, Math.min(top, vh - h - 8));
    el.style.left = left + "px";
    el.style.top = top + "px";
  }
}

;// ./src/manager.ts






class RimeManager {
  constructor(config) {
    this._panel = null;
    this._keyboard = null;
    this._tklKeyboard = null;
    this._toolbar = null;
    this.destroyed = false;
    this._mode = config.managerMode;
    this.ime = new RimeIME(config);
    switch (this._mode) {
      case "panel":
        this._panel = new RimePanel({
          ...config,
          ime: this.ime,
          externalKeyHandling: config.externalKeyHandling,
          theme: config.panelTheme,
          themeVars: config.panelThemeVars,
          size: config.panelSize,
          showComment: config.showComment,
          showNavigation: config.showNavigation,
          vertical: config.vertical,
          positionOffset: config.positionOffset,
          className: config.panelClassName,
          style: config.panelStyle
        });
        break;
      case "panel+keyboard":
        this._panel = new RimePanel({
          ...config,
          ime: this.ime,
          renderOnly: true,
          externalKeyHandling: true,
          theme: config.panelTheme,
          themeVars: config.panelThemeVars,
          size: config.panelSize,
          showComment: config.showComment,
          showNavigation: config.showNavigation,
          vertical: config.vertical,
          positionOffset: config.positionOffset,
          className: config.panelClassName,
          style: config.panelStyle
        });
        this._keyboard = new RimeKeyboard({
          ...config,
          ime: this.ime,
          hideCandidateBar: true,
          theme: config.kbTheme,
          size: config.kbSize,
          kbMode: config.kbMode,
          showOnFocus: config.showOnFocus,
          haptic: config.haptic,
          floatingWidth: config.floatingWidth,
          floatingHeight: config.floatingHeight,
          eol: config.eol
        });
        this.wirePanelKeyboard();
        break;
      case "keyboard":
        this._keyboard = new RimeKeyboard({
          ...config,
          ime: this.ime,
          theme: config.kbTheme,
          size: config.kbSize,
          kbMode: config.kbMode,
          showOnFocus: config.showOnFocus,
          haptic: config.haptic,
          floatingWidth: config.floatingWidth,
          floatingHeight: config.floatingHeight,
          eol: config.eol
        });
        break;
      case "tkl+panel":
        this._panel = new RimePanel({
          ...config,
          ime: this.ime,
          renderOnly: true,
          externalKeyHandling: true,
          theme: config.panelTheme,
          themeVars: config.panelThemeVars,
          size: config.panelSize,
          showComment: config.showComment,
          showNavigation: config.showNavigation,
          vertical: config.vertical,
          positionOffset: config.positionOffset,
          className: config.panelClassName,
          style: config.panelStyle
        });
        this._tklKeyboard = new RimeTKLKeyboard({
          ...config,
          ime: this.ime,
          theme: config.tklTheme,
          floatingWidth: config.tklFloatingWidth,
          floatingHeight: config.tklFloatingHeight,
          eol: config.eol
        });
        this.wirePanelTKL();
        break;
    }
    if (config.showToolbar !== false) {
      const provider = this._panel ?? this._keyboard ?? this._tklKeyboard;
      this._toolbar = new RimeToolbar({
        ...config,
        provider,
        theme: config.panelTheme ?? config.kbTheme,
        position: config.toolbarPosition,
        target: config.target,
        keyboardEl: (this._keyboard ?? this._tklKeyboard)?.getElement()
      });
    }
  }
  async init() {
    await this.ime.init();
  }
  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    this._toolbar?.destroy();
    this._panel?.destroy();
    this._keyboard?.destroy();
    this._tklKeyboard?.destroy();
    this.ime.destroy();
  }
  getIME() {
    return this.ime;
  }
  getPanel() {
    return this._panel;
  }
  getKeyboard() {
    return this._keyboard;
  }
  getTKLKeyboard() {
    return this._tklKeyboard;
  }
  getToolbar() {
    return this._toolbar;
  }
  getMode() {
    return this._mode;
  }
  isInitialized() {
    return this.ime.isInitialized();
  }
  // ─── 事件代理 ───
  onCommit(cb) {
    this.ime.onCommit(cb);
  }
  onOptionChange(cb) {
    this.ime.onOptionChange(cb);
  }
  onSchemaChange(cb) {
    this.ime.onSchemaChange(cb);
  }
  onError(cb) {
    this.ime.onError(cb);
  }
  onDeployStatus(cb) {
    this.ime.onDeployStatus(cb);
  }
  onResultChange(cb) {
    this.ime.onResultChange(cb);
  }
  offCommit(cb) {
    this.ime.offCommit(cb);
  }
  offOptionChange(cb) {
    this.ime.offOptionChange(cb);
  }
  offSchemaChange(cb) {
    this.ime.offSchemaChange(cb);
  }
  offError(cb) {
    this.ime.offError(cb);
  }
  offDeployStatus(cb) {
    this.ime.offDeployStatus(cb);
  }
  offResultChange(cb) {
    this.ime.offResultChange(cb);
  }
  // ─── 模式2 专用：Panel + Keyboard 联动 ───
  wirePanelKeyboard() {
    if (!this._panel || !this._keyboard) return;
    this.ime.onResultChange((r) => {
      this._panel.renderResult(r);
    });
    this.ime.onCommit((text) => {
      this._keyboard.insertText(text);
    });
  }
  // ─── 模式4 专用：Panel + TKL Keyboard 联动 ───
  wirePanelTKL() {
    if (!this._panel || !this._tklKeyboard) return;
    this.ime.onResultChange((r) => {
      this._panel.renderResult(r);
    });
    this.ime.onCommit((text) => {
      this._tklKeyboard.insertText(text);
    });
  }
}

;// ./src/index.ts








RimePlugin = __webpack_exports__;
/******/ })()
;
//# sourceMappingURL=rime-plugin.js.map
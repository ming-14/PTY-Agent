export interface RimeResult {
  state: number
  head: string
  body: string
  tail: string
  candidates: Array<{ text: string; comment: string }>
  committed: string
  page: number
  isLastPage: boolean
  highlighted: number
  selectLabels: string[]
  updatedOptions: Record<string, boolean>
  updatedSchema: string
}

async function loadRimeWasm(wasmDir: string): Promise<any> {
  const nodePath = await import('path')
  const nodeFs = await import('fs')
  const { createRequire } = await import('module')
  const nodeRequire = createRequire(import.meta.url)
  const wasmDirAbs = nodePath.resolve(wasmDir)

  const jsPath = nodePath.join(wasmDirAbs, 'rime.js')
  const wasmPath = nodePath.join(wasmDirAbs, 'rime.wasm')
  const dataPath = nodePath.join(wasmDirAbs, 'rime.data')

  for (const f of [jsPath, wasmPath, dataPath]) {
    if (!nodeFs.existsSync(f)) {
      throw new Error(`RIME WASM file not found: ${f}\nPlace rime.js, rime.wasm, rime.data in ${wasmDirAbs}`)
    }
  }

  const wasmBinary = nodeFs.readFileSync(wasmPath)
  const dataBinary = nodeFs.readFileSync(dataPath)

  const Module: Record<string, any> = {}

  Module.wasmBinary = new Uint8Array(wasmBinary)
  Module.locateFile = (filename: string) => nodePath.join(wasmDirAbs, filename)
  Module.preloadedPackages = { 'public/rime.data': dataBinary.buffer.slice(dataBinary.byteOffset, dataBinary.byteOffset + dataBinary.byteLength) }
  Module.print = (text: string) => process.stdout.write(text + '\n')
  Module.printErr = (text: string) => process.stderr.write(text + '\n')
  Module.noInitialRun = true
  Module.arguments = []
  Module.thisProgram = process.argv[1] || './this.program'

  ;(globalThis as any).Module = Module

  const vm = await import('node:vm')
  const code = nodeFs.readFileSync(jsPath, 'utf-8')
  const context = vm.createContext({
    Module,
    console,
    process,
    require: nodeRequire,
    __dirname: wasmDirAbs,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    Buffer,
    TextDecoder: globalThis.TextDecoder,
    TextEncoder: globalThis.TextEncoder,
    Uint8Array: globalThis.Uint8Array,
    Int8Array: globalThis.Int8Array,
    Int16Array: globalThis.Int16Array,
    Uint16Array: globalThis.Uint16Array,
    Int32Array: globalThis.Int32Array,
    Uint32Array: globalThis.Uint32Array,
    Float32Array: globalThis.Float32Array,
    Float64Array: globalThis.Float64Array,
    ArrayBuffer: globalThis.ArrayBuffer,
    DataView: globalThis.DataView,
    WebAssembly: globalThis.WebAssembly,
    Promise: globalThis.Promise,
    Error: globalThis.Error,
    Math: globalThis.Math,
    JSON: globalThis.JSON,
    URL: globalThis.URL,
    atob: (s: string) => Buffer.from(s, 'base64').toString('binary'),
    btoa: (s: string) => Buffer.from(s, 'binary').toString('base64'),
  })
  vm.runInContext(code, context, { filename: 'rime.js' })

  const mod = context.Module || Module

  return new Promise((resolve, reject) => {
    const checkReady = () => {
      if (mod.calledRun) {
        try { mod.FS.mkdir('/rime') } catch {}
        mod.ccall('init', 'null', [], [])
        resolve(mod)
      } else {
        setTimeout(checkReady, 100)
      }
    }
    if (mod.setStatus) {
      mod.setStatus('Loading...')
    }
    checkReady()

    setTimeout(() => reject(new Error('RIME WASM initialization timeout')), 30000)
  })
}

export class RimeEngine {
  private module: any = null
  private initialized = false
  private userDataDir: string = ''
  private nodeFs: any = null
  private nodePath: any = null

  async init(wasmDir: string): Promise<void> {
    if (this.initialized) return
    this.nodePath = await import('path')
    this.nodeFs = await import('fs')
    this.module = await loadRimeWasm(wasmDir)
    this.userDataDir = this.nodePath.join(this.nodePath.resolve(wasmDir), '..', 'user-data')
    this.syncUserDirFromDisk()
    this.initialized = true
  }

  private syncUserDirFromDisk(): void {
    if (!this.nodeFs.existsSync(this.userDataDir)) return
    const entries = this.readDirRecursive(this.userDataDir, '/rime')
    for (const [virtualPath, diskPath] of entries) {
      try {
        const data = this.nodeFs.readFileSync(diskPath)
        this.module.FS.writeFile(virtualPath, new Uint8Array(data))
      } catch {}
    }
  }

  private readDirRecursive(dir: string, virtualBase: string): Array<[string, string]> {
    const result: Array<[string, string]> = []
    try {
      for (const entry of this.nodeFs.readdirSync(dir)) {
        const full = this.nodePath.join(dir, entry)
        const stat = this.nodeFs.statSync(full)
        if (stat.isDirectory()) {
          result.push(...this.readDirRecursive(full, virtualBase + '/' + entry))
        } else {
          result.push([virtualBase + '/' + entry, full])
        }
      }
    } catch {}
    return result
  }

  syncUserDirToDisk(): void {
    this.ensureInit()
    try {
      const entries = this.module.FS.readdir('/rime')
      for (const entry of entries) {
        if (entry === '.' || entry === '..') continue
        this.syncEntry('/rime/' + entry)
      }
    } catch {}
  }

  private syncEntry(virtualPath: string): void {
    try {
      const stat = this.module.FS.stat(virtualPath)
      if (stat.isDirectory()) {
        const entries = this.module.FS.readdir(virtualPath)
        for (const entry of entries) {
          if (entry === '.' || entry === '..') continue
          this.syncEntry(virtualPath + '/' + entry)
        }
      } else {
        const data = this.module.FS.readFile(virtualPath)
        const relPath = virtualPath.replace(/^\/rime\//, '')
        const diskPath = this.nodePath.join(this.userDataDir, relPath)
        const diskDir = this.nodePath.dirname(diskPath)
        if (!this.nodeFs.existsSync(diskDir)) {
          this.nodeFs.mkdirSync(diskDir, { recursive: true })
        }
        this.nodeFs.writeFileSync(diskPath, Buffer.from(data))
      }
    } catch {}
  }

  setSchemaName(schema: string, name: string): void {
    this.ensureInit()
    this.module.ccall('set_schema_name', 'null', ['string', 'string'], [schema, name])
  }

  setPageSize(size: number): void {
    this.ensureInit()
    this.module.ccall('set_page_size', 'null', ['number'], [size])
  }

  setIME(schema: string): RimeResult {
    this.ensureInit()
    this.module.ccall('set_ime', 'null', ['string'], [schema])
    return this.emptyResult()
  }

  process(input: string): RimeResult {
    this.ensureInit()
    const json = this.module.ccall('process', 'string', ['string'], [input])
    const result = JSON.parse(json)
    if (result.committed) {
      setTimeout(() => this.syncUserDirToDisk(), 0)
    }
    return result
  }

  selectCandidate(index: number): RimeResult {
    this.ensureInit()
    const json = this.module.ccall('select_candidate_on_current_page', 'string', ['number'], [index])
    const result = JSON.parse(json)
    if (result.committed) {
      setTimeout(() => this.syncUserDirToDisk(), 0)
    }
    return result
  }

  changePage(backward: boolean): RimeResult {
    this.ensureInit()
    const json = this.module.ccall('change_page', 'string', ['boolean'], [backward])
    return JSON.parse(json)
  }

  setOption(option: string, value: boolean): void {
    this.ensureInit()
    this.module.ccall('set_option', 'null', ['string', 'number'], [option, value ? 1 : 0])
  }

  deploy(): void {
    this.ensureInit()
    this.module.ccall('deploy', 'null', [], [])
  }

  writeFile(virtualPath: string, data: Uint8Array): void {
    this.ensureInit()
    this.module.FS.writeFile(virtualPath, data)
  }

  private emptyResult(): RimeResult {
    return {
      state: 3,
      head: '',
      body: '',
      tail: '',
      candidates: [],
      committed: '',
      page: 1,
      isLastPage: true,
      highlighted: 0,
      selectLabels: [],
      updatedOptions: {},
      updatedSchema: ''
    }
  }

  private ensureInit(): void {
    if (!this.initialized) {
      throw new Error('RimeEngine not initialized')
    }
  }
}

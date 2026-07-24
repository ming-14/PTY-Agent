import { RimeEngine } from './rime-engine.js'
import path from 'path'
import fs from 'fs'

const PINYIN_SCHEMAS = ['luna_pinyin']

const SCHEMA_TARGETS: Record<string, string> = {
  luna_pinyin: 'luna-pinyin',
  stroke: 'stroke'
}

const SCHEMA_NAMES: Record<string, string> = {
  luna_pinyin: '朙月拼音',
  stroke: '五笔画'
}

const SCHEMA_DEPENDENCIES: Record<string, string[]> = {
  luna_pinyin: ['stroke'],
  stroke: []
}

export class DictManager {
  private engine: RimeEngine
  private loadedSchemas = new Set<string>()
  private loading = new Map<string, Promise<void>>()
  private dictDir: string

  constructor(engine: RimeEngine, dictDir?: string) {
    this.engine = engine
    this.dictDir = dictDir ?? path.resolve(process.cwd(), 'dict')
  }

  async ensureSchema(schemaId: string): Promise<void> {
    if (this.loadedSchemas.has(schemaId)) return
    if (this.loading.has(schemaId)) {
      await this.loading.get(schemaId)
      return
    }

    if (!PINYIN_SCHEMAS.includes(schemaId) && schemaId !== 'stroke') {
      throw new Error(`Unsupported schema: ${schemaId}. Only pinyin schemas are supported.`)
    }

    const promise = this.loadSchema(schemaId)
    this.loading.set(schemaId, promise)
    try {
      await promise
      this.loadedSchemas.add(schemaId)
    } finally {
      this.loading.delete(schemaId)
    }
  }

  private async loadSchema(schemaId: string): Promise<void> {
    const deps = SCHEMA_DEPENDENCIES[schemaId] ?? []
    for (const dep of deps) {
      await this.ensureSchema(dep)
    }

    const target = SCHEMA_TARGETS[schemaId]
    if (!target) return

    const localDir = path.join(this.dictDir, target)
    if (fs.existsSync(localDir)) {
      await this.loadFromLocal(localDir)
    } else {
      throw new Error(`Dictionary not found locally at ${localDir}. Please copy dict files first.`)
    }

    this.engine.setSchemaName(schemaId, SCHEMA_NAMES[schemaId] ?? schemaId)
  }

  private async loadFromLocal(dir: string): Promise<void> {
    const entries = fs.readdirSync(dir)
    const validExts = ['.table.bin', '.reverse.bin', '.prism.bin', '.schema.yaml']

    for (const entry of entries) {
      if (!validExts.some(ext => entry.endsWith(ext))) continue
      const filePath = path.join(dir, entry)
      const data = fs.readFileSync(filePath)
      const virtualPath = `/usr/share/rime-data/build/${entry}`
      this.engine.writeFile(virtualPath, new Uint8Array(data))
    }
  }

  isLoaded(schemaId: string): boolean {
    return this.loadedSchemas.has(schemaId)
  }

  getSupportedSchemas(): string[] {
    return [...PINYIN_SCHEMAS]
  }
}

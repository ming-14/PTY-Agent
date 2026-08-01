import type { WebSocket } from 'ws'
import { RimeEngine } from './rime-engine.js'
import { DictManager } from './dict-manager.js'

interface ClientState {
  schema: string
  pageSize: number
}

export class WSHandler {
  private engine: RimeEngine
  private dictManager: DictManager
  private clients = new Map<WebSocket, ClientState>()

  constructor(engine: RimeEngine, dictManager: DictManager) {
    this.engine = engine
    this.dictManager = dictManager
  }

  async handle(ws: WebSocket): Promise<void> {
    const state: ClientState = { schema: 'luna_pinyin', pageSize: 5 }
    this.clients.set(ws, state)

    ws.on('message', async (raw: Buffer) => {
      let msg: any
      try { msg = JSON.parse(raw.toString()) } catch {
        ws.send(JSON.stringify({ type: 'error', error: 'Invalid JSON' }))
        return
      }
      try {
        await this.handleMessage(ws, state, msg)
      } catch (err) {
        this.sendResult(ws, msg._msgId, { type: 'error', error: String(err) })
      }
    })

    ws.on('close', () => { this.clients.delete(ws) })
  }

  private async handleMessage(ws: WebSocket, state: ClientState, msg: any): Promise<void> {
    const msgId = msg._msgId

    switch (msg.type) {
      case 'init': {
        state.schema = msg.schema ?? 'luna_pinyin'
        state.pageSize = msg.pageSize ?? 5

        await this.dictManager.ensureSchema(state.schema)
        this.engine.setPageSize(state.pageSize)
        this.engine.setIME(state.schema)

        this.sendResult(ws, msgId, this.emptyResult(state.schema))
        break
      }

      case 'process': {
        const result = this.engine.process(msg.key)
        this.sendResult(ws, msgId, { type: 'result', ...this.convertState(result) })
        break
      }

      case 'selectCandidate': {
        const result = this.engine.selectCandidate(msg.index)
        this.sendResult(ws, msgId, { type: 'result', ...this.convertState(result) })
        break
      }

      case 'changePage': {
        const result = this.engine.changePage(msg.backward ?? false)
        this.sendResult(ws, msgId, { type: 'result', ...this.convertState(result) })
        break
      }

      case 'setOption': {
        this.engine.setOption(msg.option, msg.value)
        this.sendResult(ws, msgId, this.emptyResult())
        break
      }

      case 'setIME': {
        state.schema = msg.schema ?? state.schema
        await this.dictManager.ensureSchema(state.schema)
        this.engine.setIME(state.schema)
        this.sendResult(ws, msgId, this.emptyResult(state.schema))
        break
      }

      case 'setPageSize': {
        state.pageSize = msg.pageSize ?? state.pageSize
        this.engine.setPageSize(state.pageSize)
        this.sendResult(ws, msgId, this.emptyResult())
        break
      }

      case 'deploy': {
        this.engine.deploy()
        this.sendResult(ws, msgId, this.emptyResult())
        break
      }

      default: {
        this.sendResult(ws, msgId, { type: 'error', error: `Unknown message type: ${msg.type}` })
      }
    }
  }

  private static STATE_MAP: Record<number, string> = {
    0: 'committed',
    1: 'accepted',
    2: 'rejected',
    3: 'unhandled'
  }

  private convertState(result: any): any {
    if (typeof result.state === 'number') {
      result.state = WSHandler.STATE_MAP[result.state] ?? 'unhandled'
    }
    if (Array.isArray(result.updatedOptions)) {
      const opts: Record<string, boolean> = {}
      for (const opt of result.updatedOptions) {
        if (opt.startsWith('!')) {
          opts[opt.substring(1)] = false
        } else {
          opts[opt] = true
        }
      }
      result.updatedOptions = opts
    }
    if ('head' in result || 'body' in result || 'tail' in result) {
      if (!result.composition) {
        result.composition = {
          head: result.head ?? '',
          body: result.body ?? '',
          tail: result.tail ?? ''
        }
      }
      delete result.head
      delete result.body
      delete result.tail
    }
    return result
  }

  private emptyResult(updatedSchema = '') {
    return {
      type: 'result',
      state: 'unhandled', head: '', body: '', tail: '',
      candidates: [], committed: '',
      page: 1, isLastPage: true, highlighted: 0, selectLabels: [],
      updatedOptions: {}, updatedSchema
    }
  }

  private sendResult(ws: WebSocket, msgId: number | undefined, data: any): void {
    if (ws.readyState !== 1) return
    ws.send(JSON.stringify({ ...data, _msgId: msgId }))
  }

  getClientCount(): number { return this.clients.size }
}

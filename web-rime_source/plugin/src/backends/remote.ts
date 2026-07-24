import type { IBackend } from './base'
import type {
  RimeResult,
  WSMessage,
  WSResultMessage,
  WSInitMessage,
  WSProcessMessage,
  WSSelectMessage,
  WSPageMessage,
  WSSetOptionMessage,
  WSSetIMEMessage,
  WSSetPageSizeMessage,
  WSDeployMessage,
  WSDeployStatusMessage
} from '../types'

type MessageHandler = (msg: WSResultMessage | WSDeployStatusMessage) => void

export class RemoteBackend implements IBackend {
  private ws: WebSocket | null = null
  private serverUrl: string
  private msgId = 0
  private pending = new Map<number, {
    resolve: (result: RimeResult | void) => void
    reject: (error: Error) => void
  }>()
  private handler: MessageHandler | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private destroyed = false
  private currentSchema = ''
  private currentPageSize = 5

  constructor(serverUrl: string) {
    this.serverUrl = serverUrl
  }

  private connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        resolve()
        return
      }
      this.ws = new WebSocket(this.serverUrl)

      const onOpen = () => {
        this.ws!.removeEventListener('open', onOpen)
        this.ws!.removeEventListener('error', onError)
        resolve()
      }

      const onError = () => {
        this.ws!.removeEventListener('open', onOpen)
        this.ws!.removeEventListener('error', onError)
        reject(new Error('WebSocket connection failed'))
      }

      this.ws.addEventListener('open', onOpen)
      this.ws.addEventListener('error', onError)
      this.ws.addEventListener('message', (ev: MessageEvent) => { this.onMessage(ev) })
      this.ws.addEventListener('close', () => {
        if (!this.destroyed) this.scheduleReconnect()
      })
    })
  }

  private onMessage(ev: MessageEvent) {
    let msg: WSMessage
    try { msg = JSON.parse(ev.data) } catch { return }

    if (msg.type === 'result' || msg.type === 'deployStatus') {
      const typedMsg = msg as WSResultMessage | WSDeployStatusMessage
      if (typedMsg.type === 'result' && typeof (typedMsg as any)._msgId === 'number') {
        const id = (typedMsg as any)._msgId as number
        const pending = this.pending.get(id)
        if (pending) {
          this.pending.delete(id)
          const result: RimeResult = {
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
          }
          pending.resolve(result)
        }
      }
      if (this.handler) this.handler(typedMsg)
    }
  }

  private send<T = RimeResult>(msg: WSMessage): Promise<T> {
    return new Promise((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        reject(new Error('WebSocket not connected'))
        return
      }
      const id = ++this.msgId
      const payload = { ...msg, _msgId: id }
      this.pending.set(id, { resolve: resolve as any, reject })
      this.ws.send(JSON.stringify(payload))
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id)
          reject(new Error('Request timeout'))
        }
      }, 10000)
    })
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = setTimeout(async () => {
      if (this.destroyed) return
      try {
        await this.connect()
        if (this.currentSchema) await this.init(this.currentSchema, this.currentPageSize)
      } catch { this.scheduleReconnect() }
    }, 3000)
  }

  setHandler(handler: MessageHandler) { this.handler = handler }

  async init(schema: string, pageSize: number): Promise<void> {
    this.currentSchema = schema
    this.currentPageSize = pageSize
    await this.connect()
    const msg: WSInitMessage = { type: 'init', schema, pageSize }
    await this.send(msg)
  }

  async process(key: string): Promise<RimeResult> {
    const msg: WSProcessMessage = { type: 'process', key }
    return this.send(msg)
  }

  async selectCandidate(index: number): Promise<RimeResult> {
    const msg: WSSelectMessage = { type: 'selectCandidate', index }
    return this.send(msg)
  }

  async changePage(backward: boolean): Promise<RimeResult> {
    const msg: WSPageMessage = { type: 'changePage', backward }
    return this.send(msg)
  }

  async setOption(option: string, value: boolean): Promise<void> {
    const msg: WSSetOptionMessage = { type: 'setOption', option, value }
    await this.send(msg)
  }

  async setIME(schema: string): Promise<RimeResult> {
    this.currentSchema = schema
    const msg: WSSetIMEMessage = { type: 'setIME', schema }
    return this.send(msg)
  }

  async setPageSize(size: number): Promise<void> {
    this.currentPageSize = size
    const msg: WSSetPageSizeMessage = { type: 'setPageSize', pageSize: size }
    await this.send(msg)
  }

  async deploy(): Promise<void> {
    const msg: WSDeployMessage = { type: 'deploy' }
    await this.send(msg)
  }

  destroy() {
    this.destroyed = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    if (this.ws) { this.ws.close(); this.ws = null }
    for (const [, p] of this.pending) p.reject(new Error('Backend destroyed'))
    this.pending.clear()
  }
}

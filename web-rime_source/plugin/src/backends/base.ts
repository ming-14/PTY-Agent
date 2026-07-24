import type { RimeResult } from '../types'

export interface IBackend {
  init(schema: string, pageSize: number): Promise<void>
  process(key: string): Promise<RimeResult>
  selectCandidate(index: number): Promise<RimeResult>
  changePage(backward: boolean): Promise<RimeResult>
  setOption(option: string, value: boolean): Promise<void>
  setIME(schema: string): Promise<RimeResult>
  setPageSize(size: number): Promise<void>
  deploy(): Promise<void>
  destroy(): void
}

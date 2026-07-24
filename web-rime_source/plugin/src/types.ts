export type RimeState = 'committed' | 'accepted' | 'rejected' | 'unhandled'

export interface Candidate {
  text: string
  comment: string
}

export interface Composition {
  head: string
  body: string
  tail: string
}

export interface RimeResult {
  state: RimeState
  composition: Composition
  candidates: Candidate[]
  committed: string
  page: number
  isLastPage: boolean
  highlighted: number
  selectLabels: string[]
  updatedOptions: Record<string, boolean>
  updatedSchema: string
}

export type RimeMode = 'remote' | 'wasm'

export interface RimeIMEConfig {
  mode: RimeMode
  serverUrl?: string
  wasmUrl?: string
  schema?: string
  pageSize?: number
  persist?: boolean
}

export type CommitCallback = (text: string) => void
export type OptionChangeCallback = (options: Record<string, boolean>) => void
export type SchemaChangeCallback = (schema: string) => void
export type ErrorCallback = (error: Error) => void
export type DeployStatusCallback = (status: 'start' | 'success' | 'failure') => void
export type ResultChangeCallback = (result: RimeResult) => void

export interface WSMessage {
  type: string
  [key: string]: unknown
}

export interface WSResultMessage extends WSMessage {
  type: 'result'
  state: RimeState
  composition: Composition
  candidates: Candidate[]
  committed: string
  page: number
  isLastPage: boolean
  highlighted: number
  selectLabels: string[]
  updatedOptions: Record<string, boolean>
  updatedSchema: string
}

export interface WSInitMessage extends WSMessage {
  type: 'init'
  schema: string
  pageSize: number
}

export interface WSProcessMessage extends WSMessage {
  type: 'process'
  key: string
}

export interface WSSelectMessage extends WSMessage {
  type: 'selectCandidate'
  index: number
}

export interface WSPageMessage extends WSMessage {
  type: 'changePage'
  backward: boolean
}

export interface WSSetOptionMessage extends WSMessage {
  type: 'setOption'
  option: string
  value: boolean
}

export interface WSSetIMEMessage extends WSMessage {
  type: 'setIME'
  schema: string
}

export interface WSSetPageSizeMessage extends WSMessage {
  type: 'setPageSize'
  pageSize: number
}

export interface WSDeployMessage extends WSMessage {
  type: 'deploy'
}

export interface WSDeployStatusMessage extends WSMessage {
  type: 'deployStatus'
  status: 'start' | 'success' | 'failure'
}

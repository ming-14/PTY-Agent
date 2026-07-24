import { WebSocketServer } from 'ws'
import { RimeEngine } from './rime-engine.js'
import { DictManager } from './dict-manager.js'
import { WSHandler } from './ws-handler.js'

import path from 'path'
import http from 'http'
import fs from 'fs'
import fsp from 'fs/promises'

const PORT = parseInt(process.env.RIME_PORT ?? '3000', 10)
const WASM_DIR = process.env.RIME_WASM_DIR ?? './wasm'
const DICT_DIR = process.env.RIME_DICT_DIR ?? path.resolve(process.cwd(), 'dict')
const DEBUG_STATICS_DIR = path.resolve(process.cwd(), '__debug_statics')

const MIME_TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.wasm': 'application/wasm',
  '.yaml': 'text/yaml; charset=utf-8',
  '.yml': 'text/yaml; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.bin': 'application/octet-stream',
}

function serve404(res: http.ServerResponse) {
  res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
  res.end('404 Not Found')
}

async function main() {
  console.log('Initializing RIME engine...')
  const engine = new RimeEngine()
  await engine.init(WASM_DIR)
  console.log('RIME engine initialized.')

  const dictManager = new DictManager(engine, DICT_DIR)
  const handler = new WSHandler(engine, dictManager)

  // 如果 __debug_statics 目录存在，则自动挂载其中的静态资源
  const debugStaticsEnabled = fs.existsSync(DEBUG_STATICS_DIR) &&
    fs.statSync(DEBUG_STATICS_DIR).isDirectory()

  const httpServer = http.createServer((req, res) => {
    if (!debugStaticsEnabled || !req.url) {
      serve404(res)
      return
    }
    // 解析请求路径，默认首页指向 demo.html，并防止路径穿越
    const urlPath = decodeURIComponent(req.url.split('?')[0])
    const relPath = urlPath === '/' ? 'demo.html' : urlPath.replace(/^\/+/, '')
    const filePath = path.join(DEBUG_STATICS_DIR, relPath)
    if (!filePath.startsWith(DEBUG_STATICS_DIR)) {
      serve404(res)
      return
    }
    fsp.stat(filePath).then((stat) => {
      if (!stat.isFile()) {
        serve404(res)
        return
      }
      const ext = path.extname(filePath).toLowerCase()
      const mime = MIME_TYPES[ext] ?? 'application/octet-stream'
      res.writeHead(200, { 'Content-Type': mime })
      fs.createReadStream(filePath).pipe(res)
    }).catch(() => serve404(res))
  })

  const wss = new WebSocketServer({ server: httpServer })

  wss.on('connection', async (ws) => {
    console.log(`Client connected. Total: ${handler.getClientCount() + 1}`)
    await handler.handle(ws)
    console.log(`Client disconnected. Total: ${handler.getClientCount()}`)
  })

  httpServer.listen(PORT, () => {
    console.log(`RIME WebSocket server running on ws://localhost:${PORT}`)
    if (debugStaticsEnabled) {
      console.log(`Debug statics mounted: ${DEBUG_STATICS_DIR}`)
      console.log(`Debug page available at http://localhost:${PORT}/`)
    }
    console.log(`WASM dir: ${WASM_DIR}`)
    console.log(`Dict dir: ${DICT_DIR}`)
    console.log(`Supported schemas: ${dictManager.getSupportedSchemas().join(', ')}`)
  })

  process.on('SIGINT', () => {
    console.log('\nShutting down...')
    wss.close()
    process.exit(0)
  })
}

main().catch((err) => {
  console.error('Failed to start server:', err)
  process.exit(1)
})

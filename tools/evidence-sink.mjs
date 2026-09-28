// TripPal research: evidence sink.
//
// Why: the chrome-devtools MCP in --slim mode only exposes
// navigate / evaluate / screenshot, and every evaluate result travels through
// the model's context. Page text is far too large for that. Instead the page
// hands its extracted payload to this localhost sink, which writes it straight
// into research/raw/ — so a harvested page costs the model only a tiny
// confirmation string.
//
// Two upload channels, because Chrome's Local Network Access policy denies
// fetch()/XHR from a public page to 127.0.0.1 (permission state: denied):
//
//   1. GET top-level navigation (works today, no Chrome flags):
//        location.href = 'http://127.0.0.1:8791/save?token=..&name=slug&body=' + encodeURIComponent(text)
//      The page navigates away (that is fine: every harvest starts with a
//      navigate anyway). Long payloads are chunked with append=1.
//   2. POST fetch/XHR (only reachable when the browser is launched with
//      --chromeArg=--disable-features=LocalNetworkAccessChecks).
//
// Run: node tools/evidence-sink.mjs   (binds 127.0.0.1 only)

import { createServer } from 'node:http'
import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..', 'research', 'raw')
const TOKEN = process.env.SINK_TOKEN ?? 'trippal-sink'
const PORT = Number(process.env.SINK_PORT ?? 8791)
const MAX_HEADER_BYTES = 24 * 1024 * 1024

mkdirSync(ROOT, { recursive: true })

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': '*',
  'Access-Control-Max-Age': '600',
}

function send(res, status, body, type = 'text/plain; charset=utf-8') {
  res.writeHead(status, { ...CORS, 'Content-Type': type })
  res.end(body)
}

function targetFile(raw) {
  const safe = String(raw ?? 'unnamed').replace(/[^A-Za-z0-9._-]/g, '_').slice(0, 140) || 'unnamed'
  return join(ROOT, /\.(txt|json|md|html|csv)$/.test(safe) ? safe : `${safe}.txt`)
}

function store(params, buf) {
  const file = targetFile(params.get('name'))
  if (params.get('append') === '1') appendFileSync(file, buf)
  else writeFileSync(file, buf)
  return { ok: true, file, bytes: buf.length }
}

const server = createServer({ maxHeaderSize: MAX_HEADER_BYTES }, (req, res) => {
  const url = new URL(req.url ?? '/', 'http://127.0.0.1')
  const params = url.searchParams

  if (req.method === 'OPTIONS') return send(res, 204, '')
  if (url.pathname === '/health') return send(res, 200, `ok ${ROOT}`)

  // Channel 1: top-level navigation GET.
  if (url.pathname === '/save' && (req.method === 'GET' || req.method === 'HEAD')) {
    if (params.get('token') !== TOKEN) return send(res, 403, 'bad token')
    try {
      const body = params.get('body') ?? ''
      const result = store(params, Buffer.from(body, 'utf8'))
      return send(res, 200, JSON.stringify(result), 'application/json')
    } catch (error) {
      return send(res, 500, String(error?.message ?? error))
    }
  }

  // Channel 2: same-request POST (fetch/XHR/beacon).
  if ((url.pathname === '/save' || url.pathname === '/save/') && req.method === 'POST') {
    if (params.get('token') !== TOKEN) return send(res, 403, 'bad token')
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => {
      try {
        return send(res, 200, JSON.stringify(store(params, Buffer.concat(chunks))), 'application/json')
      } catch (error) {
        return send(res, 500, String(error?.message ?? error))
      }
    })
    return
  }

  send(res, 404, 'not found')
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[evidence-sink] http://127.0.0.1:${PORT} -> ${ROOT} (max header ${MAX_HEADER_BYTES} B)`)
})

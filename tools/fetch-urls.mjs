// TripPal research: bulk page fetcher.
//
// Pulls a list of URLs with a real desktop-browser UA, converts HTML to plain
// text, and writes one file per source into research/raw/web/ with a metadata
// header. This is the "wide" half of the corpus; the chrome-devtools MCP
// browser handles what needs real rendering or anti-bot evasion (Google Trends
// API, JS-only pages), and research/raw/ holds its output.
//
// Usage:
//   node tools/fetch-urls.mjs <list-file> [--force] [--out <dir>]
//
// List format (one per line, '#' comments allowed):
//   <url>
//   <slug>\t<url>
//
// Exit: writes research/raw/web/_index.tsv and _failures.txt.

import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync, appendFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const RAW = resolve(HERE, '..', 'research', 'raw')

const argv = process.argv.slice(2)
const listPath = argv.find((a) => !a.startsWith('--'))
const force = argv.includes('--force')
const outIdx = argv.indexOf('--out')
const OUT = outIdx >= 0 ? resolve(argv[outIdx + 1]) : join(RAW, 'web')
const CONCURRENCY = 4
const TIMEOUT_MS = 45_000
const MAX_BYTES = 4_000_000

const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36'

if (!listPath) {
  console.error('usage: node tools/fetch-urls.mjs <list-file> [--force] [--out <dir>]')
  process.exit(2)
}
mkdirSync(OUT, { recursive: true })

const slugify = (s) =>
  s
    .toLowerCase()
    .replace(/^https?:\/\//, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 90) || 'page'

function decodeEntities(html) {
  return html
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(Number(d)))
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&mdash;/gi, '—')
    .replace(/&ndash;/gi, '–')
    .replace(/&hellip;/gi, '…')
    .replace(/&rsquo;/gi, '’')
    .replace(/&lsquo;/gi, '‘')
    .replace(/&ldquo;/gi, '“')
    .replace(/&rdquo;/gi, '”')
}

function htmlToText(html) {
  let s = html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<svg[\s\S]*?<\/svg>/gi, ' ')
    .replace(/<template[\s\S]*?<\/template>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<(br|hr)\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|li|tr|h[1-6]|section|article|header|footer|blockquote|td|th|dd|dt|figcaption)>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
  s = decodeEntities(s)
  return s
    .replace(/[ \t\f\v\u00a0]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function looksBlocked(text, status) {
  if (!text || text.length < 400) {
    if (/just a moment|enable javascript|access denied|are you a robot|verify you are human|captcha|checking your browser|403 forbidden|attention required/i.test(text ?? ''))
      return true
  }
  return status === 403 || status === 429 || status === 503
}

function title(text, fallback) {
  const first = text.split('\n').map((l) => l.trim()).find((l) => l.length > 8 && l.length < 160)
  return (first ?? fallback).slice(0, 150)
}

async function fetchOne(entry) {
  const { slug, url } = entry
  const file = join(OUT, `${slug}.txt`)
  if (!force && existsSync(file) && statSync(file).size > 300) return { slug, url, status: 'skip', bytes: statSync(file).size }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    const res = await fetch(url, {
      redirect: 'follow',
      signal: controller.signal,
      headers: {
        'user-agent': UA,
        accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9,zh-CN;q=0.8',
      },
    })
    const contentType = res.headers.get('content-type') ?? ''
    const buf = Buffer.from(await res.arrayBuffer()).subarray(0, MAX_BYTES)
    const raw = buf.toString('utf8')
    const isJson = /json/.test(contentType) || /^\s*[[{]/.test(raw.slice(0, 200))
    const text = isJson ? raw : htmlToText(raw)
    const blocked = looksBlocked(text, res.status)
    const header = [
      `# source: ${url}`,
      `# final: ${res.url}`,
      `# status: ${res.status}`,
      `# contentType: ${contentType}`,
      `# fetchedAt: ${new Date().toISOString()}`,
      `# bytes(raw): ${buf.length}`,
      `# bytes(text): ${Buffer.byteLength(text, 'utf8')}`,
      `# title: ${title(text, url)}`,
      '',
    ].join('\n')
    if (!blocked || text.length > 1500) writeFileSync(file, header + text)
    return { slug, url, status: res.status, bytes: Buffer.byteLength(text, 'utf8'), blocked }
  } catch (error) {
    return { slug, url, status: 'ERR', error: String(error?.message ?? error) }
  } finally {
    clearTimeout(timer)
  }
}

const lines = readFileSync(listPath, 'utf8')
  .split(/\r?\n/)
  .map((l) => l.trim())
  .filter((l) => l && !l.startsWith('#'))

const entries = []
const seen = new Set()
for (const line of lines) {
  const [a, b] = line.split('\t')
  const url = (b ?? a).trim()
  if (!/^https?:\/\//.test(url) || seen.has(url)) continue
  seen.add(url)
  entries.push({ slug: (b ? a.trim() : slugify(url)), url })
}

const results = []
let cursor = 0
async function worker() {
  while (cursor < entries.length) {
    const entry = entries[cursor++]
    const result = await fetchOne(entry)
    results.push(result)
    const flag = result.blocked ? ' BLOCKED' : ''
    console.log(
      `[${results.length}/${entries.length}] ${String(result.status).padEnd(4)} ${String(result.bytes ?? result.error ?? '').padStart(8)}${flag}  ${result.slug}`,
    )
  }
}
await Promise.all(Array.from({ length: CONCURRENCY }, worker))

const ok = results.filter((r) => typeof r.status === 'number' && r.status < 400 && !r.blocked)
const bad = results.filter((r) => !ok.includes(r))
writeFileSync(
  join(OUT, '_index.tsv'),
  ['slug\tstatus\tbytes\tblocked\turl', ...results.map((r) => [r.slug, r.status, r.bytes ?? '', r.blocked ?? '', r.url].join('\t'))].join('\n'),
)
writeFileSync(join(OUT, '_failures.txt'), bad.map((r) => `${r.status}\t${r.url}\t${r.error ?? ''}`).join('\n'))
console.log(`\nok=${ok.length} failed/blocked=${bad.length} out=${OUT}`)

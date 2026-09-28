// TripPal research: corpus de-boilerplating.
//
// The raw scrape keeps navigation, footer and "related articles" text, so a
// handful of words appear in almost every file of a site and pollute any
// keyword statistic. This pass drops any line that recurs across a large share
// of the corpus (site chrome), keeps each file's `# source:` header, and writes
// a cleaned copy to research/raw/clean/ for analysis and for the later
// scenario/agent phases.
//
// Usage: node tools/clean-corpus.mjs [--in <dir>] [--out <dir>] [--threshold 0.25]

import { mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const argv = process.argv.slice(2)
const argOf = (flag, fallback) => {
  const i = argv.indexOf(flag)
  return i >= 0 ? argv[i + 1] : fallback
}
const IN = resolve(argOf('--in', join(HERE, '..', 'research', 'raw', 'web')))
const OUT = resolve(argOf('--out', join(HERE, '..', 'research', 'raw', 'clean')))
const THRESHOLD = Number(argOf('--threshold', '0.25'))
const MIN_LEN = 12

mkdirSync(OUT, { recursive: true })
const files = readdirSync(IN).filter((f) => f.endsWith('.txt') && !f.startsWith('_'))
const parsed = []
for (const name of files) {
  const text = readFileSync(join(IN, name), 'utf8')
  const lines = text.split(/\r?\n/)
  const headerEnd = lines.findIndex((l, i) => i > 0 && !l.startsWith('#') && l.trim() !== '')
  const header = lines.slice(0, headerEnd < 0 ? 0 : headerEnd)
  const body = lines.slice(headerEnd < 0 ? 0 : headerEnd)
  parsed.push({ name, header, body })
}

const counts = new Map()
for (const { body } of parsed) {
  const seen = new Set()
  for (const line of body) {
    const key = line.trim().toLowerCase()
    if (key.length < MIN_LEN || seen.has(key)) continue
    seen.add(key)
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
}
const total = parsed.length
const boilerplate = new Set([...counts.entries()].filter(([, n]) => n / total >= THRESHOLD).map(([k]) => k))

let removedBytes = 0
let keptBytes = 0
for (const { name, header, body } of parsed) {
  const kept = []
  let lastKey = null
  for (const line of body) {
    const key = line.trim().toLowerCase()
    if (boilerplate.has(key)) {
      removedBytes += Buffer.byteLength(line, 'utf8')
      continue
    }
    if (line.trim() === '' && lastKey === '') continue
    kept.push(line)
    lastKey = line.trim() === '' ? '' : key
    keptBytes += Buffer.byteLength(line, 'utf8')
  }
  writeFileSync(join(OUT, name), [...header, '', ...kept].join('\n'))
}

console.log(`files=${total} boilerplateLines=${boilerplate.size} removedKB=${Math.round(removedBytes / 1024)} keptKB=${Math.round(keptBytes / 1024)}`)
console.log(`out=${OUT}`)
const top = [...boilerplate].slice(0, 12)
console.log('example boilerplate lines:')
for (const line of top) console.log(`  - ${line.slice(0, 90)}`)

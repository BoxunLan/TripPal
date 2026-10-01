// 把任务包按轮转切成 N 份（每份都含各族卡，避免分片退化）。
// 用法：node split_pack.mjs <pack.jsonl> <outDir> <n>
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const [, , packPath, outDir, nRaw] = process.argv
const n = Number(nRaw)
if (!packPath || !outDir || !Number.isInteger(n) || n < 1) {
  console.error('usage: node split_pack.mjs <pack.jsonl> <outDir> <n>')
  process.exit(2)
}

const cards = readFileSync(packPath, 'utf8').split(/\r?\n/).filter((l) => l.trim())
const buckets = Array.from({ length: n }, () => [])
cards.forEach((card, i) => buckets[i % n].push(card))

mkdirSync(outDir, { recursive: true })
buckets.forEach((bucket, i) => {
  writeFileSync(join(outDir, `pack${i}.jsonl`), bucket.join('\n') + '\n', 'utf8')
})
console.log(`split ${cards.length} cards into ${buckets.map((b) => b.length).join('/')}`)

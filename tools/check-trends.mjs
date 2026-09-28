// Validate the harvested Google Trends JSON files and print a compact inventory.
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const RAW = resolve(HERE, '..', 'research', 'raw')

for (const name of readdirSync(RAW).filter((f) => /^trends-.*\.json$/.test(f)).sort()) {
  const text = readFileSync(join(RAW, name), 'utf8')
  try {
    const json = JSON.parse(text)
    const rows = (json.results ?? []).map((r) => {
      const k = r.k ?? r.keyword ?? '?'
      const tl = (r.tl ?? r.timeline ?? []).length
      const rg = (r.rg ?? r.region ?? []).length
      const rel = r.rel ?? r.related ?? {}
      const top = (rel.QUERY?.top ?? []).length
      const ris = (rel.QUERY?.rising ?? []).length
      const err = (r.err ?? r.errors ?? []).length
      return `${k}[tl=${tl} rg=${rg} top=${top} rising=${ris} err=${err}]`
    })
    console.log(`OK   ${name} (${text.length} B) ${rows.join(' ')}`)
  } catch (error) {
    console.log(`FAIL ${name} (${text.length} B) ${error.message}`)
    console.log(`     tail: ${JSON.stringify(text.slice(-160))}`)
  }
}

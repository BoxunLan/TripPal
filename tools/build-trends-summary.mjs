// Build research/trends-summary.md from the harvested Google Trends JSON.
// Handles both schemas (batch-1 keyword/timeline/region/related and later
// k/tl/rg/rel) and filters Google's rising-query noise down to terms that are
// actually about China travel.
import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const RAW = resolve(HERE, '..', 'research', 'raw')

const RELEVANT =
  /china|chinese|visa|alipay|wechat|pay|vpn|esim|\bsim\b|travel|trip|itiner|tourist|whatsapp|google|hotel|train|transit|money|cash|\bmap|internet|beijing|shanghai|great firewall|firewall|censorship/i

const rows = []
for (const name of readdirSync(RAW).filter((f) => /^trends-.*\.json$/.test(f)).sort()) {
  const json = JSON.parse(readFileSync(join(RAW, name), 'utf8'))
  for (const r of json.results ?? []) {
    const keyword = r.k ?? r.keyword
    const tl = r.tl ?? r.timeline ?? []
    const rg = r.rg ?? r.region ?? []
    const rel = r.rel ?? r.related ?? {}
    const top = (rel.QUERY?.top ?? []).map((x) => x[0])
    const rising = (rel.QUERY?.rising ?? []).map((x) => x[0])
    const err = r.err ?? r.errors ?? []
    rows.push({ keyword, tl, rg, top, rising, err, file: name })
  }
}

const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0)
const round = (n) => Math.round(n * 10) / 10

let md = '# Google Trends 采集结果（geo=US，today 12-m，2026-09-29 抓取）\n\n'
md += '数据源：`trends.google.com/trends/api`（在 trends.google.com 页面内同源 fetch，见 `research/raw/trends-*.json`）。\n'
md += '趋势值 0-100 为相对热度（该词自身峰值=100）。飙升查询已过滤掉与主题无关的泛热搜（如 openai news）。\n\n'

const withTimeline = rows.filter((r) => r.tl.length > 0)
md += `## 一、有完整时间线的词条（${withTimeline.length} 个）\n\n`
md += '| 词条 | 全年均值 | 前13周均值 | 近13周均值 | 峰值周 | 峰值 |\n|---|---|---|---|---|---|\n'
for (const r of withTimeline) {
  const vals = r.tl.map((p) => p[1] ?? 0)
  let peak = 0
  for (let i = 1; i < vals.length; i++) if (vals[i] > vals[peak]) peak = i
  md += `| ${r.keyword} | ${round(avg(vals))} | ${round(avg(vals.slice(0, 13)))} | ${round(avg(vals.slice(-13)))} | ${r.tl[peak][0]} | ${r.tl[peak][1]} |\n`
}
md += '\n近13周=2026年7-9月（抓取时点），前13周=2025年10-12月。\n\n'

md += '## 二、相关查询与地区\n\n'
for (const r of rows) {
  const t = r.top.filter((q) => RELEVANT.test(q))
  const ri = r.rising.filter((q) => RELEVANT.test(q))
  const regions = r.rg.slice(0, 6).map((g) => `${g[0]}(${g[1]})`)
  if (!t.length && !ri.length && !regions.length && !r.tl.length) continue
  md += `### ${r.keyword}\n`
  if (t.length) md += `- 热门相关查询：${t.join(' / ')}\n`
  if (ri.length) md += `- **飙升相关查询**：${ri.join(' / ')}\n`
  if (regions.length) md += `- 搜索热度地区 Top6：${regions.join(', ')}\n`
  if (r.err.length) md += `- 采集异常：${r.err.join('; ')}\n`
  md += '\n'
}
md += '> 地区榜中 Wyoming(100) 是小样本归一化伪影（人口少、少量搜索即满分），判读时看 California / New York / Washington / DC / Kansas 等大州。\n'
md += '> `trends-us-head-3.json` 是 Google 限流（HTTP 429）下的失败样本，保留以示证据可追溯性，未计入上表。\n'

writeFileSync(resolve(HERE, '..', 'research', 'trends-summary.md'), md)
console.log(`keywords=${rows.length} withTimeline=${withTimeline.length} -> research/trends-summary.md`)

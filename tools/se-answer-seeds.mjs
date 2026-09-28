// TripPal research: turn the harvested Stack Exchange question files into
// (a) a question index and (b) batched answer-fetch seed URLs.
//
// The SE corpus was fetched through /search/advanced and /questions with
// filter=withbody, which returns questions only. Answers carry the workarounds
// travellers actually use — the raw material for "what the current solution is
// and where it breaks" — so they are pulled separately, 100 ids per request.

import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const WEB = resolve(HERE, '..', 'research', 'raw', 'web')

const byId = new Map()
for (const name of readdirSync(WEB).filter((f) => /^se-(travel|expat)-.*\.txt$/.test(f))) {
  const text = readFileSync(join(WEB, name), 'utf8')
  const body = text
    .split(/\r?\n/)
    .filter((l) => !l.startsWith('#'))
    .join('\n')
    .trim()
  let json
  try {
    json = JSON.parse(body)
  } catch {
    continue
  }
  const site = name.includes('expat') ? 'expatriates' : 'travel'
  for (const item of json.items ?? []) {
    if (!item.question_id) continue
    if (!byId.has(item.question_id)) {
      byId.set(item.question_id, {
        id: item.question_id,
        site,
        score: item.score ?? 0,
        answers: item.answer_count ?? 0,
        answered: item.is_answered ? 1 : 0,
        views: item.view_count ?? 0,
        title: (item.title ?? '').replace(/\s+/g, ' '),
        link: item.link ?? '',
      })
    }
  }
}

const rows = [...byId.values()].sort((a, b) => b.score - a.score)
writeFileSync(
  resolve(HERE, '..', 'research', 'se-questions.tsv'),
  ['id\tsite\tscore\tanswers\tanswered\tviews\ttitle\tlink', ...rows.map((r) => [r.id, r.site, r.score, r.answers, r.answered, r.views, r.title, r.link].join('\t'))].join('\n'),
)

const BATCH = 90
const seeds = []
const sites = ['travel', 'expatriates']
let n = 0
for (const site of sites) {
  const ids = rows.filter((r) => r.site === site).map((r) => r.id)
  for (let i = 0; i < ids.length; i += BATCH) {
    const chunk = ids.slice(i, i + BATCH)
    n += 1
    seeds.push(
      `se-answers-${site}-${String(n).padStart(2, '0')}\thttps://api.stackexchange.com/2.3/questions/${chunk.join(';')}/answers?order=desc&sort=votes&site=${site}&filter=withbody&pagesize=100&pagesize=100`,
    )
  }
}
writeFileSync(resolve(HERE, '..', 'research', 'seeds-07-seanswers.txt'), seeds.join('\n'))
console.log(`questions=${rows.length} withAnswers=${rows.filter((r) => r.answers > 0).length} answerBatches=${seeds.length}`)
console.log(`sites: ${sites.map((s) => `${s}=${rows.filter((r) => r.site === s).length}`).join(' ')}`)

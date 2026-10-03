# Source policy (trippal)

Read this whenever a conclusion depends on a **changeable rule**. It answers three questions:
which source wins, how to date a rule, and what to do when nothing covers the question.

## Authority tiers

| Tier | What it is | How to use it |
|---|---|---|
| **T1 — Statutory / official authority** | immigration and consular announcements, government service pages (e.g. `english.beijing.gov.cn` 12345 answers), customs, police registration notices | Wins over everything else. Cite page URL + the date displayed on the page. |
| **T2 — Official operator** | the operator's own channel for the service in question (rail operator, attraction's own booking channel, hotel's own confirmation, bank's own fee schedule) | Authoritative **for its own service**. Cite the operator page and the date. |
| **T3 — Reputable secondary guide** | curated guides that state a date and link the authority (this skill's bundled `mychina.guide` pages are this tier) | Use for orientation, procedure, and failure modes. **Never** as the sole basis for an eligibility, limit, fee or window. Escalate to T1/T2 for the number. |
| **T4 — Community / forum** | traveller reports, Q&A answers | Evidence that a problem exists, never a rule. Use it to decide what to check, not what is true. |

The bundled research baseline (`references/readiness-baseline.md`) is field discovery evidence:
it sits outside this ladder and can never settle a policy question.

## Dating discipline

- State the date the source displays, and the URL. If a page shows no date, say it is undated
  and lower confidence.
- Prefer the more recent source when two disagree, but check the tier first: a recent T3 post
  never beats an older T1 notice.
- When a rule has a known change date, say which side of the change the traveller is on.

## Conflict and insufficiency

- **Sources conflict** → prefer higher tier, then more recent; state which you chose, and tell
  the traveller what the other source claimed.
- **Nothing covers it** → say the sources in hand do not settle it, name the authority to check,
  and keep the rest of the plan moving. Never invent the missing number.
- **Page answers a different question** → do not treat resemblance as an answer. An official
  navigation path, a URL, a responsible contact, or an explicit catch-all applicant category
  *does* count as an answer to "where / how do I get this".

## Per-domain search recipe

1. Open the domain fragment; take its bundled pages and their effective dates.
2. Check `static/corpus-map.json` for `coverage`: `bundled` means the pages exist;
   `external-only` or `none` means go straight to the external authority below.
3. Search the authority, not the topic: prefer `<authority domain> + the exact rule wording`
   over a general query.
4. Record URL + date in the plan, even when the answer is "not eligible".

## External sources (declared status)

These are the authority classes this skill expects to use. `wired` means the bundled corpus
already contains a page from that class; `declared` means the skill may name it and must send
the traveller there, but nothing here can query it.

| Domain | Authority class | Status |
|---|---|---|
| entry | national immigration authority notices; Chinese embassy/consulate pages; `english.beijing.gov.cn` 12345 answers | **wired** (bundled pages) |
| entry | visa application service centres | declared |
| ticketing | attraction's own official channel; municipal park ticketing notices | **wired** (bundled pages) |
| train | rail operator's own booking channel (12306) and station notices | **wired** (bundled pages) |
| payment | card network and wallet operator fee/limit pages | **wired** (bundled page) |
| stay | police registration notices; the property's own confirmation | **wired** (bundled pages) |
| connectivity | carrier and eSIM provider coverage pages | **wired** (bundled pages) |
| navigation / citytransport | local map and ride-hailing operator help pages | declared |
| food / language | none required; these domains are preparation, not lookup | n/a |
| emergency | consulate contact pages; police and medical emergency numbers | declared |

## Governance

- Adding a bundled page = add it to the corpus fixture, then re-run
  `skill-loop/integration/pipeline/build_skill_seed.py` so the domain fragment and
  `static/corpus-map.json` pick it up. Never hand-edit generated fragments.
- Changing a tier or a recipe = edit this file; it is hand-maintained on purpose.

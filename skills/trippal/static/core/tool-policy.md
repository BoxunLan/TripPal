# Tool and retrieval policy (trippal)

There are three retrieval surfaces. Know which one you are on, and what it cannot answer.

## 1. Page tools (the TripPal webpage, via WebMCP)

- `get_trip_profile` — read the profile the traveller submitted on the page. Call it **before**
  asking the user to repeat anything. Treat `status: "submitted"` as the request to analyse
  those fields.
- `configure_trip_profile` — write back profile fields the page is missing, only when the user
  supplies them.
- `render_skill_assessment` — return the finished assessment to the page. Use it after composing,
  follow its schema exactly, and only then tell the user the plan has been returned.

The page's own preliminary preview is **not** the skill result. Never present it as one.

## 2. Bundled corpus (the pages shipped with the skill)

- `static/corpus-map.json` maps each domain to the bundled authoritative pages and their
  effective dates. It is the only machine-readable statement of what this skill can look up.
- Use the bundled corpus for a **specific** China readiness rule or operational fact that those
  pages cover. Read the page body, not the title: decide whether the page answers the material
  question, not whether its wording resembles the question.
- Do **not** use it for live weather or time, broad recommendations, generic prices without a
  route and date, unrelated general knowledge, or anything the pages demonstrably do not cover.
  When the corpus has no page for a domain, `coverage` in the map says so — go to the external
  authorities described in `references/source-policy.md` instead.

## 3. External authorities (declared, not always wired)

Two different situations, and they must not be blurred:

- **Declared in the source policy but not machine-accessible from here**: name the authority,
  give its official page or channel, and say the traveller must verify there. Do not imply you
  checked it.
- **Reachable** (for example a public page the environment can open): cite the URL and the
  effective/updated date you actually saw.

## Choosing a fallback

- Prefer a fallback the evidence supports. When an official mini-program requires a Chinese
  mobile number, the supported fallback is an accessible official **web** channel, not a
  workaround that the source does not describe.
- Recommend a staffed counter or a staffed lane **only** when the source or a known facility
  workflow supports it. "A human will sort it out" is not a plan unless the evidence says so.
- Identity-dependent travel keeps working only with the original passport plus an offline copy
  of every confirmation. Treat that as a required item, not a tip.

## Cost of looking things up

Prefer one well-chosen lookup over many vague ones. A lookup that does not change the plan is
waste; a rule that changes the plan without a lookup is a defect.

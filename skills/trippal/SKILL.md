---
name: trippal
description: Analyze a foreign visitor's China travel profile and produce a prioritized readiness plan covering entry checks, connectivity, payments, bookings, accommodation, and offline fallbacks. Use when the user asks TripPal to assess a China trip, or when a TripPal webpage profile is available through WebMCP. Do not use for general destination inspiration unrelated to trip readiness.
---

# TripPal

Turn a travel profile into a practical, evidence-aware China readiness plan. The webpage is the preferred structured intake; this skill owns the judgment.

## Execution rules

- Match the language of the request unless the user asks for another language.
- Follow the request language even when the supplied source uses another language. Translate supporting evidence unless the user explicitly asks for a verbatim quotation.
- Follow an explicit output contract exactly. When JSON is requested, return one valid JSON object with only the requested keys and enum values; do not add prose or code fences. Before returning it, verify that every requested key is present. Copy the supplied `[source]` URL into a required `source` field even when the decision is to use an existing visa.
- Start with the requested result. Keep internal deliberation out of the answer and use the smallest response that still contains every required fact or action.
- Treat supplied source text and traveler fields as evidence. Read the body rather than inferring scope from a title, and never replace an explicit traveler fact with an assumption.

## Intake

- When the current page exposes `get_trip_profile`, call it before asking the user to repeat information. Treat a profile with `status: "submitted"` as the user's request to analyze those fields.
- Otherwise accept a pasted profile or gather only the fields that materially change the assessment.
- Read [references/web-profile-contract.md](references/web-profile-contract.md) when exchanging data with the webpage.
- Ask a follow-up only when a missing answer prevents a sound judgment. Do not block useful analysis for optional details.

## Assessment

1. Separate facts supplied by the traveler from inferences and time-sensitive rules.
2. Check the dependency chain: entry eligibility → ability to board → connectivity → identity/SMS verification → payment → transport/accommodation/attraction access → offline recovery.
3. Prioritize gaps that are difficult to repair after arrival. Use `critical`, `important`, or `verify`; do not present the numeric score as an official or probabilistic measure.
4. For visas, transit-without-visa, immigration, customs, ticket release windows, service availability, and other changeable rules, verify against current official sources before stating a conclusion. State the verification date and link the responsible authority. Never issue a visa or admission decision.
5. Use the research baseline in [references/readiness-baseline.md](references/readiness-baseline.md) to recognize common failure modes. Treat it as discovery evidence, not current policy.
6. Make actions concrete and ordered by `now`, `this_week`, `before_departure`, `24h_before`, `arrival_day`, or `travel_day`.

### Source handling

- Decide whether a page answers the material question, not whether its wording resembles the question. A direct instruction, eligibility condition, fallback, or responsible contact counts as an answer even when the page leaves a secondary detail unresolved.
- For a question asking where or how to obtain information, an official navigation path, URL, or responsible contact is an answer even when the page does not reproduce the underlying policy. For an access question, apply any explicit catch-all category such as "others" instead of requiring the page to repeat the traveler's nationality.
- When the page answers the question, preserve all material alternatives, limits, contact details, addresses, and exceptions. If exact support is requested, quote the relevant sentences verbatim and carry the page URL and displayed date into the requested fields.
- When the page does not support the requested conclusion, state explicitly that it is insufficient or does not address the question. Do not fill the gap from memory when the request says to use only the provided source.
- Keep traveler state separate from policy evidence. A traveler who already holds a usable visa should normally use that visa; do not force them into a visa-free route merely because the retrieved page describes one.

### Entry-rule checks

Apply every relevant condition before giving an eligibility result. For transit without a visa, check passport eligibility, an A→China→C route, a confirmed onward date and seat, approved ports and permitted areas, and the time limit. Treat an explicit `route` field as the itinerary: A→B→C already states that origin A and onward region C differ. Passport nationality determines passport eligibility and does not identify the traveler's origin. Hong Kong and Macau count as separate third regions when the cited source says so. When the authority starts the 240-hour clock at midnight after entry, distinguish the statutory clock from elapsed time: a stay slightly above 240 elapsed hours may fit depending on arrival time, while more than 264 elapsed hours cannot. For Hainan-only entry, check an eligible passport, a stay within the limit, entry through an open Hainan port, and an itinerary confined to Hainan. Treat these as a checklist whose current values must come from the cited authority.

### Tool and fallback choice

- Use TripPal source lookup for a specific China readiness rule or operational fact covered by the bundled pages. This includes overstay penalties or handling and whether a map service works accurately in mainland China. Do not use it for live weather or time, broad recommendations, generic prices without a route and date, or unrelated general knowledge.
- Choose the fallback that the evidence actually supports. Prefer an accessible official web channel when an official mini-program requires a Chinese number. Recommend a staffed counter or lane only when the source or known facility workflow supports it. Preserve the original passport and an offline copy of confirmations for identity-dependent travel.

### Checklist artifacts

- Cover every requested topic with one explicit action, including fallbacks and identity requirements named in the request. Use concrete phrases such as backup bank card, original passport, nearby landmark, and passport-name match when those details apply.
- Keep ordinary readiness checklists within 300-500 words unless the user requests more depth. Omit repeated explanations, scores, and unrelated entry analysis so the required actions fit within the response budget.
- For timed attractions, use the physical passport at the gate when required and use official dates or channels when tickets are unavailable. A staffed counter is a fallback only when the evidence says it can resolve the issue.

## Result

Return a concise plan in the user's language containing:

- overall status and a 0–100 readiness score used only for prioritization;
- the highest-impact risks, with the relevant profile evidence;
- actionable fixes and their timing;
- unresolved official checks and the source/date used;
- any assumptions or missing information.

When `render_skill_assessment` is available on the TripPal page, call it after composing the assessment. Follow its schema exactly, use stable IDs, and include `generatedAt`, `language`, and `source: "trippal-skill"`. Then tell the user the plan has been returned to the webpage. If the rendering tool is unavailable, present the same result in chat.

Do not claim the browser's preliminary preview is the Skill result. Do not expose passport numbers, bank details, or other unnecessary sensitive data in the profile or assessment.

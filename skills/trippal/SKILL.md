---
name: trippal
description: Analyze a foreign visitor's China travel profile and produce a prioritized readiness plan covering entry checks, connectivity, payments, bookings, accommodation, and offline fallbacks. Use when the user asks TripPal to assess a China trip, or when a TripPal webpage profile is available through WebMCP. Do not use for general destination inspiration unrelated to trip readiness.
---

# TripPal

Turn a travel profile into a practical, evidence-aware China readiness plan. The webpage is the preferred structured intake; this skill owns the judgment.

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

## Result

Return a concise plan in the user's language containing:

- overall status and a 0–100 readiness score used only for prioritization;
- the highest-impact risks, with the relevant profile evidence;
- actionable fixes and their timing;
- unresolved official checks and the source/date used;
- any assumptions or missing information.

When `render_skill_assessment` is available on the TripPal page, call it after composing the assessment. Follow its schema exactly, use stable IDs, and include `generatedAt`, `language`, and `source: "trippal-skill"`. Then tell the user the plan has been returned to the webpage. If the rendering tool is unavailable, present the same result in chat.

Do not claim the browser's preliminary preview is the Skill result. Do not expose passport numbers, bank details, or other unnecessary sensitive data in the profile or assessment.

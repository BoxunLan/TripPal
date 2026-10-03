# Readiness workflow (trippal)

Run these steps in order. Skipping step 3 is the most common cause of confident, wrong plans.

## 1. Intake

- If the page exposes `get_trip_profile`, call it first. Analyse a submitted profile without
  asking the user to retype it.
- Otherwise accept a pasted profile, or gather only the fields that materially change the
  assessment (nationality, origin, route and onward leg, dates, ports, cities, phone model and
  carrier, cards, whether a local number is available, accommodation type).
- Ask a follow-up **only** when a missing answer prevents a sound judgment. Optional details do
  not block a useful first pass; mark them as assumptions instead.

## 2. Classify

Map the request onto the manifest's `domain` axis (one or more domains). State the detected
domains in one short line so the user can correct you early.

## 3. Verify against sources

For anything changeable — visa-free eligibility, transit windows and areas, port and area
restrictions, release windows, service availability, fees, registration duties:

- consult the domain fragment's bundled pages and `static/corpus-map.json`;
- when the corpus does not cover it, follow `references/source-policy.md` to the authority that does;
- record the **URL and the effective/displayed date** you relied on;
- if the sources conflict, prefer the more authoritative and the more recent, and say which you
  chose and why;
- if nothing supports the conclusion, say so and name the authority to check.

## 4. Order the actions

Bucket every action by when the traveller can still do it:

`now` → `this_week` → `before_departure` → `24h_before` → `arrival_day` → `travel_day`

Within a bucket, put unrecoverable-after-arrival items first.

## 5. Compose

Write the plan with the required elements from `static/core/output-and-quality.md`, covering
every domain you loaded, including its required items and its evidence-supported fallbacks.

## 6. Self-check, then return

- Every policy claim carries a source and a date, or is explicitly marked unresolved.
- Traveller facts were not overwritten by inference; inferences are labelled.
- No visa/entry/admission decision was issued.
- Required items for each loaded domain are present, or their absence is explained.
- The language matches the request.

Run `scripts/verify_assessment.py` on the payload. Fix findings, re-run, then present the plan —
and, when `render_skill_assessment` is available, hand it back to the page.

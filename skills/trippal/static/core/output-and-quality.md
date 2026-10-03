# Output and quality contract (trippal)

## What the user receives

A concise plan in the language of the request, containing:

1. **overall status** and a **0–100 readiness score** used only to order the traveller's own actions;
2. **highest-impact risks**, each tied to the profile evidence that produced it;
3. **actionable fixes with timing** (`now` / `this_week` / `before_departure` / `24h_before` /
   `arrival_day` / `travel_day`);
4. **unresolved official checks**, each with the source URL and effective date, or an explicit
   statement that no source settled it and which authority must;
5. **assumptions and missing information**, labelled as such.

## Score semantics

- The score orders work. It is **not** an official assessment and **not** a probability.
- Say so in one short line when the score appears. Never dress it as a risk model.
- Missing information lowers confidence, not the score silently: list what is missing.

## Length and shape

- Ordinary readiness checklists: **300–500 words** unless the user asks for more depth.
- Omit repeated explanations, restated scores, and unrelated entry analysis so the required
  actions fit the budget.
- Every requested topic gets **one explicit action**, including its fallback and any identity
  requirement. Concrete wording beats general advice: backup bank card, original passport,
  nearby landmark, passport-name match, Chinese address card, offline confirmation copy.

## Evidence rules

- Every changeable rule (visa-free eligibility, transit window, ports/areas, release window,
  fee, registration duty) carries **source URL + effective/displayed date**.
- Verbatim quotation only when the user asked for exact support; otherwise translate and cite.
- When the source does not support the conclusion, say explicitly that it is insufficient or
  does not address the question. Do not fill the gap from memory when the request restricts you
  to the provided source.
- Keep traveller state separate from policy evidence: a traveller who already holds a usable
  visa should normally use that visa, even when the retrieved page describes a visa-free route.

## Sensitive data

Never include passport numbers, bank details, or unnecessary document data in the plan or the
webpage payload.

## Webpage payload

When `render_skill_assessment` is available:

- follow its schema exactly, with stable IDs;
- include `generatedAt`, `language`, and `source: "trippal-skill"`;
- return it **after** composing and self-checking, then tell the user the plan has been returned;
- if the tool is unavailable, present the same result in chat.

The browser's preliminary preview is not the skill result; never present it as one.

## Self-check before returning

Run `scripts/verify_assessment.py` and treat its findings as blockers. It checks the payload
contract and flags policy claims with no source URL or no date. Fix, re-run, then answer.

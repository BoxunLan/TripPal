---
name: trippal
description: Analyze a foreign visitor's China travel profile and produce a prioritized readiness plan covering entry checks, connectivity, payments, bookings, accommodation, transport, language, and offline fallbacks. Use when the user asks TripPal to assess a China trip, when a TripPal webpage profile is available through WebMCP, or for Chinese requests such as 中国旅行准备、入境准备、行前检查、来华旅游准备、落地能不能用网/用钱、行程合规检查. Do not use for general destination inspiration unrelated to trip readiness.
---

# TripPal — Router

This skill is split into two layers:

- A **static layer** under `static/` that holds versioned, reusable fragments: four always-loaded
  core documents (principles, tool policy, workflow, output and quality) and one conditional
  fragment per **readiness domain**.
- A **dynamic layer** (this file plus `manifest.yaml`) that classifies the request onto the
  domains that actually apply and loads only those fragments. Retrieval policy, the field
  failure-mode baseline, the webpage contract, and the machine-readable page map live in
  on-demand references.

Do not work from memory and do not answer from this router. The policy details, source
requirements, and required items live in the fragments; load them from disk as described below.

## Routing protocol

Follow these five steps every time the skill is invoked.

### 1. Load the manifest and the core layer

Read [manifest.yaml](manifest.yaml). It declares the `domain` axis, the allowed values, and the
file each value maps to.

Also read every file listed under `always_load`: the product principles, the tool/retrieval
policy, the readiness workflow, and the output/quality contract. These apply to every request.

### 2. Classify the request onto one or more domains

Decide the `domain` value(s) using the manifest's `detect:` hints and the profile fields. A
submitted profile normally spans several domains; load every domain it touches and skip the rest.
State the detected domains in one short line before composing the plan, so the user can correct
you cheaply.

### 3. Load the matching fragments

Read each file mapped for the detected domains. Each fragment lists the bundled authoritative
pages for that domain with their effective dates, the checks to run, the items the answer must
contain, and the field failure modes to pre-empt. Do **not** read every fragment.

For the machine-readable domain → page map (including which domains have no bundled page),
read [static/corpus-map.json](static/corpus-map.json).

### 4. Compose the plan using the loaded material

Apply the loaded material in this priority order:

1. Principles (`static/core/principles.md`) — the dependency chain, the non-negotiables, and
   what the score does and does not mean.
2. Tool and retrieval policy (`static/core/tool-policy.md`) — page tools, bundled corpus,
   external authorities, and what each surface cannot answer.
3. Workflow (`static/core/workflow.md`) — intake, classification, verification, ordering.
4. Domain fragments — the domain-specific sources, checks and required items.
5. Output and quality (`static/core/output-and-quality.md`) — the payload contract, lengths,
   insufficiency phrasing, and the self-check before returning.

Read [references/source-policy.md](references/source-policy.md) whenever a conclusion depends on
a changeable rule (visa-free eligibility, transit windows, ports and areas, release windows,
fees, registration duties). Use [references/readiness-baseline.md](references/readiness-baseline.md)
to recognise the unrecoverable-after-arrival failure modes.

### 5. Verify before returning

Run the declared quality tool on the composed assessment:

```
python scripts/verify_assessment.py --assessment <path-to-assessment.json> [--domain <slug> ...]
```

Treat its findings as blockers, fix the assessment, and re-run it. The tool checks the payload
contract and flags policy claims that carry no source URL or no effective date.

## Why this split

- The static layer is reviewable: adding a domain is one new fragment plus one manifest line.
- The dynamic layer keeps each invocation cheap: only the core four documents plus the applicable
  domains enter context up front; retrieval policy and the field baseline load only when needed.
- This router stays short on purpose. Add scope to fragments, not to this file.

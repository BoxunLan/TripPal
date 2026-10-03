# TripPal skill

Turn a foreign visitor's China travel profile into a prioritized, evidence-aware readiness plan.
The TripPal webpage is the preferred structured intake and rendering surface; this skill owns the
judgment.

**Status: seed v0 (`manifest.yaml: version 1.0.0-seed`).** The goal is the artifact itself — the
skill is the product. Whether it can also be evolved is a separate question and not a
prerequisite.

## Layout

```
trippal/
├── SKILL.md                 router only: 5-step protocol, "never work from memory"
├── manifest.yaml            declarative routing table (version / always_load / axes / references / quality_tools)
├── static/
│   ├── core/                always loaded: principles, tool-policy, workflow, output-and-quality
│   ├── fragments/domain/    conditional: one fragment per readiness domain (12)
│   └── corpus-map.json      domain -> bundled authoritative pages (+ effective dates, coverage)
├── references/              on demand: source-policy, readiness-baseline, web-profile-contract
├── scripts/
│   └── verify_assessment.py quality gate: payload contract + source/date discipline
├── agents/openai.yaml       agent surface (display name, default prompt)
└── tests/
    └── test_skill_architecture.py  guards the shape of the architecture
```

## How routing works

1. Load `manifest.yaml` and every file in `always_load`.
2. Classify the request onto one or more **readiness domains** (`entry`, `predeparture`,
   `payment`, `ticketing`, `train`, `citytransport`, `stay`, `connectivity`, `navigation`,
   `food`, `emergency`, `language`). State the detected domains before composing.
3. Load only those domain fragments. Each one lists its bundled authoritative pages with
   effective dates, the checks to run, the items the answer must contain, and the field failure
   modes to pre-empt.
4. Compose in priority order: principles → tool policy → workflow → domain fragments → output.
5. Verify with `scripts/verify_assessment.py`, fix findings, then return (or hand back through
   `render_skill_assessment`).

## Quality gate

```powershell
python scripts/verify_assessment.py --assessment assessment.json        # human-readable
python scripts/verify_assessment.py --assessment assessment.json --json # machine-readable
```

Exit code 1 means at least one error-level finding. It checks: the webpage payload contract,
policy claims that carry no source URL or no check date (the measured failure mode: confident,
unsourced numbers), decision/guarantee wording, passport- or card-like identifiers, and the
declared language versus the text.

## What is curated vs generated

- **Hand-written**: `SKILL.md`, `manifest.yaml`, `static/core/*`, `references/source-policy.md`,
  `scripts/verify_assessment.py`, `tests/*`.
- **Generated** (marked with a `SEED DRAFT` banner): `static/fragments/domain/*`, `static/corpus-map.json`,
  produced by `skill-loop/integration/pipeline/build_skill_seed.py` from
  `research/pain_points.md` (taxonomy + 42 field pains) and the dataset v2 cards + page fixture.
  Regenerate with `python skill-loop/integration/pipeline/build_skill_seed.py`; never hand-edit them.
  `test_generated_layer_has_no_drift` fails when the sources move ahead of the skill.
- The generated fragments are **unreviewed drafts**: they state sources, checks, required items
  and failure modes, but no human has signed off on the wording yet.

## Giving feedback on this seed

Both tracks run the same evaluation set (`skill-loop/modules/demand-task-factory/out-v2/task_pack.jsonl`,
332 cards) and compare `B-no-skill` against this skill:

```powershell
cd skill-loop
# small-model track (local)
& .\integration\pipeline\run_v2_parallel.ps1 -Pack modules/demand-task-factory/out-v2/task_pack.jsonl `
    -Out "$env:TEMP\v2-seed-base" -Jobs 4 -Runs 1 -MaxTokens 12288 -TimeoutSeconds 180
& .\integration\pipeline\run_v2_parallel.ps1 -Pack modules/demand-task-factory/out-v2/task_pack.jsonl `
    -Out "$env:TEMP\v2-seed-cand" -Jobs 4 -Runs 1 -MaxTokens 12288 -TimeoutSeconds 180 `
    -Condition C-seed-skill -Skill "..\skills\trippal"
```

Pass the skill as a **directory** so the whole text layer is injected: `SKILL.md`, `manifest.yaml`,
`static/**` (core + fragments + corpus map) and `references/**`. Injecting only `SKILL.md`
silently drops everything the router points at, and the comparison stops measuring the same artifact.

> **Known measurement caveat (seed)**: the harness injects the *entire* static layer, so all 12
> fragments enter context even though the router says to load only the applicable ones. A
> file-reading agent loads only the routed fragments. Domain content is therefore measured
> faithfully; **routing itself is not yet measured**. Routed (per-domain) injection is the top
> follow-up — `static/corpus-map.json` already carries the `dataset_scenario` needed to drive it.

What is most useful in the feedback, in order:

1. **Which domain fragments are wrong, thin, or misleading** — the generated ones are drafts.
2. **Which required items a domain is missing** — these come from the dataset's artifact cards.
3. **Where the router misclassifies** a real profile (which domains it should have loaded).
4. **Which source-policy call was wrong** (tier choice, dating, conflict resolution).
5. **Where the quality gate is too loud or too quiet.**

**Architecture-level suggestions are welcome, and the structure is not frozen.** The router/manifest/
fragments/references/scripts split is the current best draft, not a decision: the axis choice, the number
of layers, the routing mechanism, or a different delivery shape are all open. If you change it, keep the
load relationships declared in `manifest.yaml` and update `tests/test_skill_architecture.py` with it.

**Second feedback path (product thinking), not from the 332 cards:** benchmark mature travel products —
how they organise information (which axis), how they present sources and update dates, how they express
uncertainty and their red lines, what the user actually receives, how many questions they ask, and what
they do when the source is insufficient — then map each borrowed pattern onto a concrete file here
(`references/source-policy.md`, `static/core/*`, `static/fragments/domain/*`, `manifest.yaml`).
Borrow structure and expression, never conclusions: policy facts still come only from T1/T2 sources.

Report per-domain and per-pattern rather than as a single score: a global pass rate can improve
while one domain collapses (`skill-loop/integration/pipeline/pattern_regress.py` does that
comparison).

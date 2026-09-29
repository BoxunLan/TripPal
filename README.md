# TripPal

TripPal combines a travel-readiness intake website with a Codex Skill that turns a submitted profile into a prioritized China travel preparation plan.

## Website

The static website is in [`website`](website). It collects route, connectivity, payment, accommodation, and booking details in a four-step form, supports Chinese and English, and exposes WebMCP tools for the TripPal Skill to read a submitted profile and return its assessment.

The form saves drafts and submitted profiles in the current browser's local storage; it does not send them to a server. To run it locally, serve the static directory over localhost:

```powershell
python -m http.server 8000 --directory website
```

Then open <http://localhost:8000>. The `website` directory can also be used as the static hosting root.

## Skill

The Skill is in [`skills/trippal`](skills/trippal). See its [web profile contract](skills/trippal/references/web-profile-contract.md) for the data schema shared with the website.

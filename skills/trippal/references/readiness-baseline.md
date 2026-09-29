# China trip readiness baseline

This is a discovery baseline derived from the repository's 2026-09-29 research corpus. It helps identify what to investigate; it is not an authority for current rules.

## High-impact dependency chains

- A data-only eSIM can provide connectivity without a `+86` number, while some local services may still require SMS verification.
- Installing, signing in, identity verification, and making a successful payment are distinct readiness states. A configured wallet is not proof that a payment will succeed.
- A confirmed booking is not always proof that accommodation can register a guest with a foreign passport. Private stays may have separate registration requirements.
- Passport-based reservations can fail because of name formatting, document support, release windows, or automated gates; a staffed fallback and the original passport may be needed.
- Connectivity, phone battery, and mobile payment can fail together. Preserve independent offline and payment fallbacks.
- English property names and map pins may be insufficient for arrival. Save the Chinese name, address, phone number, and a nearby landmark.

## Prioritization

Treat a gap as `critical` when it can prevent boarding, entry, communication on arrival, accommodation, essential payment, or a fixed-time booking and is hard to repair after landing.

Treat a gap as `important` when an independent fallback is missing but the traveler still has a workable primary path.

Use `verify` for material questions that require a current official source or depend on details not supplied by the traveler.

## Evidence boundaries

The full research corpus is under the repository's `research/` directory and includes source URLs and quotations. Use it to understand recurring failure modes. For current legal, immigration, transport, or provider rules, browse the responsible official source and state the check date.

# TripPal webpage contract

Use this reference only when reading from or writing to the TripPal webpage.

## Read: `get_trip_profile`

The tool takes no input and returns:

```json
{
  "schemaVersion": "1.0",
  "status": "draft | submitted",
  "submissionId": "trip-... or null",
  "submittedAt": "ISO-8601 timestamp or null",
  "language": "en | zh",
  "profile": {
    "route": {
      "passportNationality": "string",
      "origin": "string",
      "arrivalCity": "string",
      "onwardDestination": "string",
      "arrivalDate": "YYYY-MM-DD",
      "stayDays": 7
    },
    "connectivity": {
      "plan": "roaming | esim | local | none",
      "hasChinaNumber": false,
      "appsTested": false,
      "offlinePackReady": false
    },
    "payments": {
      "walletStatus": "none | started | one | two",
      "physicalCardCount": 0,
      "mainCardNetwork": "string",
      "cashFallback": "no | atm | cash"
    },
    "bookings": {
      "accommodationType": "chain | small | guesthouse | friends | none",
      "foreignPassportCheckInConfirmed": "yes | no | na",
      "timedAttractions": "no | ready | unknown",
      "intercityTrainPlanned": false,
      "accommodationAddress": "string",
      "willCarryOriginalPassport": false
    }
  },
  "missingRequired": ["route.passportNationality"]
}
```

Analyze only a `submitted` profile unless the user explicitly asks to assess a draft.

## Write: `render_skill_assessment`

Send one object with this shape:

```json
{
  "submissionId": "same ID returned by get_trip_profile",
  "source": "trippal-skill",
  "generatedAt": "ISO-8601 timestamp",
  "language": "en | zh",
  "score": 72,
  "status": "needs_attention | mostly_ready | ready",
  "summary": "Short traveler-facing summary",
  "risks": [
    {
      "id": "stable-kebab-id",
      "severity": "critical | important | verify",
      "title": "Short title",
      "detail": "Why this matters for this profile",
      "evidence": "Relevant answer or verified fact"
    }
  ],
  "actions": [
    {
      "id": "stable-kebab-id",
      "timing": "now | this_week | before_departure | 24h_before | arrival_day | travel_day",
      "title": "Action title",
      "detail": "Specific completion criterion"
    }
  ],
  "officialChecks": [
    {
      "topic": "Entry eligibility",
      "result": "What was confirmed or remains unresolved",
      "url": "https://official.example/...",
      "checkedAt": "YYYY-MM-DD"
    }
  ],
  "assumptions": ["Short explicit assumption"]
}
```

Keep `score` between 0 and 100. Use no more than eight risks and twelve actions. URLs must be HTTP(S). The webpage rejects a result whose `submissionId` does not match the latest submitted profile.

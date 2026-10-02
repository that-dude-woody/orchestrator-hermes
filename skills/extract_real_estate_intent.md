---
name: extract_real_estate_intent
description: Parse buyer reply text to extract intent, rejection signals, and criteria
activation: ["when reply contains rejection or alternative property request", "when buyer expresses dissatisfaction"]
confidence: 0.7
tags: ["real_estate", "buyer_intent", "parsing", "nlp"]
version: "1.0"
---

# Extract Real Estate Intent

## Purpose
Analyze consumer reply text to extract:
- Rejection signals ("not interested", "too small", "wrong location")
- Desired criteria (beds, baths, location, price range)
- Buyer language patterns for future improvement
- Confidence score for the extraction

## Input Format
```json
{
  "reply_id": "reply_000001",
  "reply_text": "Not interested, too small. Looking for 2br in San Mateo, max $900k",
  "original_property": "1234 Maple St, Oakland, CA",
  "original_agent": "Sarah Chen"
}
```

## Output Format
```json
{
  "is_rejection": true,
  "criteria": {
    "beds_min": 2,
    "location": "San Mateo",
    "price_max": 900000,
    "features": ["2 bedrooms"]
  },
  "confidence": 0.85,
  "language_pattern": "too_small_looking_for_beds_in_city",
  "key_phrases": ["not interested", "too small", "looking for 2br", "San Mateo"]
}
```

## Extraction Rules (Rule-Based Foundation)

### Rejection Detection
Look for rejection keywords:
- "not interested"
- "too small" / "too large"
- "but looking for"
- "do you have"
- "looking for instead"

### Bedroom/Bathroom Extraction
- Pattern: `\d+\s*(?:bed|br|bedroom|bath|ba|bathroom)`
- Extract numbers and store as `beds_min`, `baths_min`

### Location Extraction
Bay Area cities: Berkeley, Oakland, San Francisco, Palo Alto, San Mateo, Fremont, San Jose, Sunnyvale, Mountain View, Cupertino, Hayward, Walnut Creek

### Price Extraction
- Pattern: `\$[\d,]+[kK]?` or `max.*\$[\d,]+`
- Store as `price_max`

## Learning Mechanism
Store extraction for later analysis:
- What phrases predict high-quality pairings?
- Which buyer language patterns are most specific?
- Which locations/criteria combinations match best?

Over time, this skill improves by:
1. Tracking extraction accuracy (does matched property satisfy buyer?)
2. Learning new phrase patterns from successful pairings
3. Auto-generating extraction rules from feedback

## Example Extractions

**Input:**
"Not interested, too small. Looking for 2br in San Mateo"

**Output:**
```json
{
  "is_rejection": true,
  "criteria": {"beds_min": 2, "location": "San Mateo"},
  "confidence": 0.9,
  "language_pattern": "small_seeking_beds_location"
}
```

**Input:**
"This looks great! Can we schedule a showing?"

**Output:**
```json
{
  "is_rejection": false,
  "criteria": {},
  "confidence": 0.95,
  "note": "positive_response_no_criteria_change"
}
```

## Notes for Future Versions
- v1.1: Add price extraction
- v1.2: Add feature extraction (pool, garage, etc)
- v1.3: ML-based pattern recognition from successful pairings
- v2.0: Claude API integration for sophisticated NLP

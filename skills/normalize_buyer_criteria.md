---
name: normalize_buyer_criteria
description: Standardize extracted buyer criteria into consistent format for matching
activation: ["after successful intent extraction", "when criteria needs validation"]
confidence: 0.95
tags: ["real_estate", "normalization", "data_standardization"]
version: "1.0"
---

# Normalize Buyer Criteria

## Purpose
Take raw extracted criteria and standardize into consistent format for cross-agent matching:
- Fill missing fields with defaults
- Validate data ranges
- Resolve ambiguous location references
- Compute match-readiness score

## Input
```json
{
  "criteria": {
    "beds_min": 2,
    "location": "San Mateo",
    "price_max": 900000
  },
  "confidence": 0.85,
  "language_pattern": "too_small_looking_for"
}
```

## Output (Normalized)
```json
{
  "beds_min": 2,
  "beds_max": null,
  "baths_min": null,
  "baths_max": null,
  "location": "San Mateo",
  "location_radius_miles": 0,
  "price_min": null,
  "price_max": 900000,
  "features": [],
  "excluded_features": [],
  "match_readiness": 0.85,
  "canonical_location": "San Mateo, CA",
  "metadata": {
    "extraction_confidence": 0.85,
    "criteria_specificity": 0.67,
    "buyer_language": "too_small_seeking_beds_location"
  }
}
```

## Normalization Rules

### Bedroom/Bathroom
- If `beds_min` not provided, default: null
- If `beds_max` not provided, default: null
- Valid range: 1-5 bedrooms
- Clamp outliers

### Price
- Default if missing: null
- Remove currency symbols, commas
- Parse "900k" → 900000
- Bay Area reasonable range: $300k - $3M

### Location
- Standardize to canonical city name
- San Mateo → "San Mateo, CA"
- SF → "San Francisco, CA"
- East Bay → ["Berkeley", "Oakland"] (expand)
- Store as single location for matching

### Match Readiness Score
```
specificity = (beds_provided × 0.3) + (location_provided × 0.5) + (price_provided × 0.2)
readiness = extraction_confidence × specificity
```

High readiness (>0.7) = ready for matching
Low readiness (<0.4) = insufficient criteria, skip this cycle

## Validation
- At least 1 criterion must be specified
- Location is highest priority (0.5 weight)
- Beds/baths are medium priority (0.3 weight)
- Price is lower priority (0.2 weight)

## Example Normalization

**Input:**
```json
{
  "beds_min": 2,
  "location": "SF",
  "price_max": "950k",
  "confidence": 0.88
}
```

**Output:**
```json
{
  "beds_min": 2,
  "beds_max": null,
  "baths_min": null,
  "location": "San Francisco, CA",
  "price_max": 950000,
  "match_readiness": 0.84,
  "extraction_confidence": 0.88,
  "criteria_specificity": 0.93
}
```

## Learning Integration
Track which normalized criteria lead to successful pairings:
- Store all normalizations in memory
- Track success rate by criteria type
- Identify most predictive criteria combinations
- Adapt specificity weights over time

## Future Enhancements
- v1.1: Multi-location support (buyer willing to consider 2+ cities)
- v1.2: Neighborhood-level precision (not just city)
- v1.3: Feature-based weighting (pool > garage > views)
- v2.0: Buyer persona matching (luxury vs. first-time buyer patterns)

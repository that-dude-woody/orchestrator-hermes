---
name: find_cross_agent_properties
description: Match normalized buyer criteria against database, find best alternatives from other agents
activation: ["after criteria normalization with readiness > 0.7", "when matching needed"]
confidence: 0.85
tags: ["real_estate", "matching", "cross_agent_discovery"]
version: "1.0"
---

# Find Cross-Agent Properties

## Purpose
Search active campaign database for properties matching normalized buyer criteria.
Rank by relevance. Return top 3 alternatives from agents OTHER than the one who rejected.

## Input
```json
{
  "buyer_criteria": {
    "beds_min": 2,
    "location": "San Mateo, CA",
    "price_max": 900000,
    "match_readiness": 0.85
  },
  "rejected_agent": "Sarah Chen",
  "rejected_property": "1234 Maple St, Oakland, CA"
}
```

## Output
```json
{
  "matches_found": 3,
  "alternatives": [
    {
      "rank": 1,
      "agent": "Marcus Johnson",
      "email": "marcus.johnson@realty.com",
      "phone": "(555) 123-4567",
      "property": "5678 Oak St, San Mateo, CA",
      "beds": 2,
      "baths": 2,
      "price": 875000,
      "relevance_score": 0.98,
      "match_reasons": ["location_match", "beds_match", "price_competitive"]
    },
    {
      "rank": 2,
      "agent": "Angela Davis",
      "email": "angela.davis@realty.com",
      "phone": "(555) 987-6543",
      "property": "9012 Elm St, San Mateo, CA",
      "beds": 3,
      "baths": 2.5,
      "price": 895000,
      "relevance_score": 0.92,
      "match_reasons": ["location_match", "beds_exceed", "price_match"]
    },
    {
      "rank": 3,
      "agent": "James Park",
      "email": "james.park@realty.com",
      "phone": "(555) 456-7890",
      "property": "3456 Birch St, San Mateo, CA",
      "beds": 2,
      "baths": 1.5,
      "price": 820000,
      "relevance_score": 0.89,
      "match_reasons": ["location_match", "beds_match", "price_below_max"]
    }
  ],
  "search_stats": {
    "total_candidates": 47,
    "after_filtering": 23,
    "final_alternatives": 3
  }
}
```

## Matching Algorithm

### Filter Stage (Exclude Non-Matches)
```
For each campaign in database:
  if campaign.agent == rejected_agent:
    skip  # Don't recommend same agent's properties
  
  if criteria.beds_min and campaign.beds < criteria.beds_min:
    skip  # Doesn't meet bedroom requirement
  
  if criteria.location and campaign.city != criteria.location:
    skip  # Location mismatch
  
  if criteria.price_max and campaign.price > criteria.price_max:
    skip  # Over budget
  
  keep → candidates
```

### Relevance Scoring Stage
For each candidate:
```
score = 0.5  # Base score

# Bedroom match (0.25 points possible)
if campaign.beds >= criteria.beds_min:
  score += 0.25

# Location match (0.25 points possible)
if campaign.city == criteria.location:
  score += 0.25

# Price bonus (if under budget)
if campaign.price < criteria.price_max:
  score += min(0.1, (criteria.price_max - campaign.price) / 100000)

# Final score: 0.5 - 1.0 range
final_score = min(1.0, score)
```

### Sort & Return
- Sort by relevance_score descending
- Return top 3 with agent contact info
- Store for success tracking

## Learning Integration

**Track:**
- Which matches buyer accepted? (feedback loop)
- Which property features matter most?
- Which agent combinations work best?
- Location preferences (close match vs. willing to drive)

**Improve Over Time:**
- Adjust scoring weights based on success rate
- Learn agent specializations (who has best inventory in each price range)
- Identify micro-location preferences
- Detect buyer sentiment in rejections ("too expensive" vs "wrong location")

## Example Match Scenario

**Input:**
```
Buyer: Wants 2br in San Mateo, max $900k
Rejected: Sarah Chen's 1br in Oakland for $850k
```

**Processing:**
1. Filter: 2,000 active campaigns → 23 San Mateo properties
2. Score: Top 3 cross-agent matches
3. Rank: Best matches by relevance
4. Output: 3 alternatives from Marcus Johnson, Angela Davis, James Park

**Success Learning:**
- If buyer buys Marcus Johnson's property → boost Marcus's agent affinity
- If buyer still rejects San Mateo → update location learning
- Store pattern: "Oakland buyer willing to move to San Mateo for 2br"

## Future Enhancements
- v1.1: Multi-location matching (willing to consider 2-3 nearby cities)
- v1.2: Feature-based matching (pool, garage, views, etc.)
- v1.3: Predictive scoring based on micro-patterns
- v2.0: ML model trained on successful pairings
- v2.1: Agent specialization inference (who dominates luxury? first-time buyers?)

## Notes
- Exclude same agent to force cross-agent discovery
- Top 3 gives good options without overwhelming
- Relevance score of 0.8+ indicates strong match
- Track all matches for success feedback loop

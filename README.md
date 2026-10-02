# Hermes Agent - Real Estate Orchestrator Fork

**Status:** Parallel implementation (doesn't affect production_mode.py)

## Overview

This is a fork of `orchestrator_production_mode.py` refactored to use **Hermes Agent architecture**:
- Three-stage skill pipeline (Extract Intent → Normalize → Match)
- Holographic memory (SQLite + FTS5 semantic search)
- Self-improving skills (learns from every successful pairing)
- Independent mock database (isolated from production)

## Running

```bash
cd orchestrator_hermes
python orchestrator_hermes_main.py
```

Runs indefinitely. Press `Ctrl+C` to stop and save results.

## Architecture

```
orchestrator_hermes/
├── config.yaml                          # Hermes configuration
├── orchestrator_hermes_main.py           # Main orchestrator script
├── skills/
│   ├── extract_real_estate_intent.md     # SKILL 1: Parse buyer intent
│   ├── normalize_buyer_criteria.md       # SKILL 2: Standardize criteria
│   └── find_cross_agent_properties.md    # SKILL 3: Match properties
├── memory/
│   └── orchestrator.db                   # SQLite + FTS5 (holographic memory)
├── checkpoint.json                       # Saved every 10 cycles
└── final_results.json                    # Final results on shutdown
```

## Three-Stage Skill Pipeline

### Skill 1: Extract Real Estate Intent
**Input:** Consumer reply text ("Not interested, too small. Looking for 2br in San Mateo")

**Output:** 
```json
{
  "is_rejection": true,
  "criteria": {"beds_min": 2, "location": "San Mateo"},
  "confidence": 0.85
}
```

**Learning:** Stores buyer language patterns in memory for future improvement

---

### Skill 2: Normalize Buyer Criteria
**Input:** Raw extracted criteria

**Output:**
```json
{
  "beds_min": 2,
  "location": "San Mateo, CA",
  "price_max": null,
  "match_readiness": 0.85,
  "specificity": 2
}
```

**Learning:** Tracks which criterion combinations lead to successful matches

---

### Skill 3: Find Cross-Agent Properties
**Input:** Normalized criteria + excluded agent

**Output:** Top 3 matching properties from OTHER agents
```json
{
  "alternatives": [
    {
      "agent": "Marcus Johnson",
      "property": "5678 Oak St, San Mateo, CA",
      "beds": 2,
      "relevance_score": 0.98
    },
    ...
  ]
}
```

**Learning:** Records successful matches → improves scoring over time

## Holographic Memory

SQLite database with three layers:

1. **Facts Layer** — Learned patterns (buyer preferences, agent behaviors)
2. **Patterns Layer** — Extracted from successful pairings
3. **Outcomes Layer** — Track pairing success/failure for feedback loop

**Example Facts:**
- "rejection_pattern_2br_san_mateo" (extracted from successful rejections)
- "match_found_3br_oakland" (patterns that led to successful pairings)
- Agent specialization insights

## Self-Improvement Loop

```
Cycle N: Parse reply → Extract intent → Find match → Generate pairing
              ↓
        Record in memory (outcome table)
              ↓
Cycle N+1: Next extraction benefits from memory of previous cycles
```

Over time:
- Extraction accuracy improves
- Relevance scoring becomes market-aware
- Agent specializations are learned
- Buyer language patterns are understood

## Comparison with Production Mode

| Aspect | production_mode.py | orchestrator_hermes_main.py |
|--------|------------------|--------------------------|
| Architecture | Linear pipeline | Skill-based (Hermes) |
| Memory | None (stateless) | Holographic (SQLite + FTS5) |
| Learning | No | Yes (self-improving) |
| Database | Shared mock DB | Independent instance |
| Conflicts | None (parallel) | Safe (isolated) |

## Results Files

After stopping (Ctrl+C):
- `checkpoint.json` — Last checkpoint before shutdown
- `final_results.json` — Complete statistics + last 20 pairings

Example `final_results.json`:
```json
{
  "summary": {
    "cycles": 410,
    "pairings_generated": 1452,
    "conversion_rate": 65.8,
    "memory_stats": {
      "facts_stored": 247,
      "patterns_learned": 89,
      "pairings_tracked": 1452,
      "success_rate": 0.98
    }
  }
}
```

## Next Steps

1. Run both systems in parallel for several cycles
2. Compare pairing quality (production vs Hermes)
3. Compare pairings per cycle (learning should show improvement)
4. Monitor memory growth (facts, patterns, success rate)
5. Once validated, can add Hermes-specific features:
   - Natural language scheduling (cron jobs)
   - Multi-platform messaging (optional)
   - More sophisticated skill generation

## Notes

- Independent mock database — can't interfere with production_mode.py
- Memory persists across runs (SQLite database)
- Skills improve each cycle (feedback loop)
- Can run both simultaneously for direct comparison

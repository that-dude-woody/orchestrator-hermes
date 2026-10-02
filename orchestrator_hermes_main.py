#!/usr/bin/env python3
"""
Real Estate Orchestrator - HERMES AGENT FORK
Parallel implementation to production_mode.py
Refactored to use Hermes Agent architecture with:
- Three-stage skill pipeline (Extract → Normalize → Match)
- Holographic memory (SQLite + FTS5 for pattern learning)
- Self-improving skills (each pairing feeds learning loop)
- Independent mock database (doesn't interfere with production_mode.py)

Run alongside orchestrator_production_mode.py for comparison.
"""

import json
import random
import sqlite3
from datetime import datetime, timedelta
import re
from collections import deque
import time
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ytel_live_db import (YtelLiveDatabase, pick_source, maybe_sync_standalone,
                          build_listings_table, galilaio_enrich_listings)

RESULTS_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "orchestrator_hermes", "dashboard", "results.db")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))          # repo root
SUB_DIR = os.path.join(BASE_DIR, "orchestrator_hermes")        # artifacts dir
MEMORY_DB = os.path.join(SUB_DIR, "memory", "orchestrator.db")
CHECKPOINT = os.path.join(SUB_DIR, "checkpoint.json")
RESULTS_JSON = os.path.join(SUB_DIR, "final_results.json")


def persist_pairings(pairings, extra_stats=None):
    """Write pairings + stats for the dashboard (append-safe, keyed on reply)."""
    os.makedirs(os.path.dirname(RESULTS_DB), exist_ok=True)
    conn = sqlite3.connect(RESULTS_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pairings (
            reply_id TEXT PRIMARY KEY,
            consumer_phone TEXT, rejected_agent TEXT, rejected_property TEXT,
            buyer_wants TEXT, best_agent TEXT, best_phone TEXT, best_email TEXT,
            best_address TEXT, best_city TEXT, best_beds REAL, best_price INTEGER,
            alternatives_count INTEGER, score REAL, generated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle INTEGER, new_matches INTEGER, total_matches INTEGER,
            campaigns INTEGER, replies INTEGER, ts TEXT
        );
    """)
    rows = []
    for p in pairings:
        b = p["best_alternative"]
        rows.append((
            p["reply_id"], p["consumer_phone"], p["rejected_agent"], p["rejected_property"],
            json.dumps(p["buyer_wants"]), b["agent"], b.get("phone"), b.get("email"),
            b["address"], b.get("city"), b.get("beds"), b.get("price"),
            p["alternatives_count"], b["score"], p["generated_at"],
        ))
    conn.executemany("""INSERT INTO pairings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(reply_id) DO UPDATE SET
                          best_agent=excluded.best_agent, best_phone=excluded.best_phone,
                          best_address=excluded.best_address, score=excluded.score,
                          generated_at=excluded.generated_at""", rows)
    if extra_stats:
        conn.execute("INSERT INTO runs (cycle, new_matches, total_matches, campaigns, replies, ts) "
                     "VALUES (?,?,?,?,?,?)",
                     (extra_stats.get("cycle"), extra_stats.get("new_matches"),
                      extra_stats.get("total_matches"), extra_stats.get("campaigns"),
                      extra_stats.get("replies"), datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()

# ============================================================================
# HERMES HOLOGRAPHIC MEMORY - SQLITE + FTS5
# ============================================================================

class HermesMemory:
    """Persistent learning layer - stores facts, patterns, outcomes"""

    def __init__(self, db_path=None):
        if db_path is None:
            db_path = MEMORY_DB
        self.db_path = db_path
        import os
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_schema()

    def init_schema(self):
        """Create memory tables with FTS5 for semantic search"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Facts table - buyer patterns, agent behaviors, market insights
        c.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY,
                fact TEXT NOT NULL,
                category TEXT,
                confidence REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                source TEXT
            )
        """)

        # FTS5 virtual table for semantic search
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                fact, category, timestamp
            )
        """)

        # Patterns table - learned from successful pairings
        c.execute("""
            CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY,
                pattern_name TEXT,
                description TEXT,
                match_rate REAL,
                occurrences INTEGER DEFAULT 1,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Outcomes table - pairing success/failure tracking
        c.execute("""
            CREATE TABLE IF NOT EXISTS outcomes (
                id INTEGER PRIMARY KEY,
                pairing_id TEXT,
                buyer_phone TEXT,
                rejected_agent TEXT,
                matched_agent TEXT,
                success INTEGER,
                feedback TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Skills evolution - track how skills improve
        c.execute("""
            CREATE TABLE IF NOT EXISTS skill_evaluations (
                id INTEGER PRIMARY KEY,
                skill_name TEXT,
                evaluation_cycle INTEGER,
                accuracy REAL,
                confidence REAL,
                notes TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()

    def store_fact(self, fact, category, confidence=0.7, source="orchestrator"):
        """Store a learned fact"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "INSERT INTO facts (fact, category, confidence, source) VALUES (?, ?, ?, ?)",
            (fact, category, confidence, source)
        )
        # Also index in FTS5
        c.execute(
            "INSERT INTO facts_fts (fact, category, timestamp) VALUES (?, ?, ?)",
            (fact, category, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

    def search_facts(self, query, category=None, limit=5):
        """FTS5 semantic search over learned facts"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        if category:
            c.execute(
                "SELECT fact FROM facts_fts WHERE facts_fts MATCH ? AND category = ? LIMIT ?",
                (query, category, limit)
            )
        else:
            c.execute(
                "SELECT fact FROM facts_fts WHERE facts_fts MATCH ? LIMIT ?",
                (query, limit)
            )

        results = c.fetchall()
        conn.close()
        return [r[0] for r in results]

    def record_outcome(self, pairing_id, buyer_phone, rejected_agent, matched_agent, success, feedback=""):
        """Record pairing outcome for learning"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "INSERT INTO outcomes (pairing_id, buyer_phone, rejected_agent, matched_agent, success, feedback) VALUES (?, ?, ?, ?, ?, ?)",
            (pairing_id, buyer_phone, rejected_agent, matched_agent, success, feedback)
        )
        conn.commit()
        conn.close()

    def get_stats(self):
        """Get memory statistics"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM facts")
        fact_count = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM patterns")
        pattern_count = c.fetchone()[0]

        c.execute("SELECT COUNT(*), AVG(success) FROM outcomes WHERE success = 1")
        outcome_data = c.fetchone()
        outcome_count = outcome_data[0]
        success_rate = outcome_data[1] if outcome_data[0] > 0 else 0

        conn.close()

        return {
            "facts_stored": fact_count,
            "patterns_learned": pattern_count,
            "pairings_tracked": outcome_count,
            "success_rate": success_rate,
        }


# ============================================================================
# HERMES SKILLS - EXECUTABLE KNOWLEDGE UNITS
# ============================================================================

class HermesSkills:
    """Skill implementations corresponding to .md definitions"""

    def __init__(self, memory):
        self.memory = memory
        self.skill_stats = {
            "extract_intent": {"calls": 0, "accuracy": 0.7},
            "normalize_criteria": {"calls": 0, "accuracy": 0.95},
            "find_matches": {"calls": 0, "accuracy": 0.85},
        }

    def extract_real_estate_intent(self, reply_text):
        """SKILL: Parse buyer reply for intent"""
        self.skill_stats["extract_intent"]["calls"] += 1

        criteria = {}
        text_lower = reply_text.lower()

        # Bed extraction
        bed_match = re.search(r'(\d)\s*(?:bed|br)', text_lower)
        if bed_match:
            criteria["beds_min"] = int(bed_match.group(1))

        # Location extraction
        locations = ["Berkeley", "Oakland", "San Francisco", "Palo Alto", "San Mateo", "Fremont"]
        for loc in locations:
            if loc.lower() in text_lower:
                criteria["location"] = loc
                break

        # Rejection detection
        is_rejection = any(w in text_lower for w in ["not interested", "too small", "but looking", "do you have"])

        # Store in memory for learning
        if is_rejection and criteria:
            pattern = f"rejection_pattern_{criteria.get('beds_min', '?')}br_{criteria.get('location', 'unknown')}"
            self.memory.store_fact(pattern, "extraction_pattern", confidence=0.8)

        return {
            "is_rejection": is_rejection,
            "criteria": criteria,
            "confidence": 0.8 if criteria else 0.3,
        }

    def normalize_buyer_criteria(self, criteria, confidence):
        """SKILL: Standardize criteria format"""
        self.skill_stats["normalize_criteria"]["calls"] += 1

        normalized = {
            "beds_min": criteria.get("beds_min"),
            "baths_min": None,
            "location": criteria.get("location"),
            "price_max": None,
            "match_readiness": confidence,
            "specificity": len([v for v in criteria.values() if v]),
        }

        return normalized

    def find_cross_agent_properties(self, criteria, exclude_agent, campaigns):
        """SKILL: Match criteria against database (LIVE version).
        Works identically on mock dicts or real Ytel/Galilaio listings -
        same dict keys. Uses match only what is known, never rejects on
        missing data (real listings often lack sqft/price)."""
        self.skill_stats["find_matches"]["calls"] += 1

        matches = []
        beds_min = criteria.get("beds_min")
        locations = criteria.get("locations") or ([] if not criteria.get("location") else [criteria["location"]])
        loc_norm = [l.lower() for l in locations if l]
        price_max = criteria.get("price_max")

        for campaign in campaigns:
            if campaign["agent"] == exclude_agent:
                continue

            if beds_min and campaign.get("beds") is not None and campaign["beds"] < beds_min:
                continue

            city = (campaign.get("city") or "").lower()
            if loc_norm and city:
                if not any(l in city or city in l for l in loc_norm):
                    continue
            elif loc_norm and not city:
                continue  # no idea where this listing is - skip when buyer named a city

            if price_max and campaign.get("price") and campaign["price"] > price_max * 1.10:
                continue

            score = 0.25
            beds = campaign.get("beds")
            if beds is not None and beds_min and beds >= beds_min:
                score += 0.25
            if loc_norm and city and any(l in city or city in l for l in loc_norm):
                score += 0.25
            if campaign.get("agent_phone") or campaign.get("phone"):
                score += 0.1

            matches.append({
                "agent": campaign["agent"],
                "email": campaign.get("email"),
                "phone": campaign.get("phone"),
                "address": campaign["address"],
                "city": campaign.get("city"),
                "beds": campaign.get("beds"),
                "baths": campaign.get("baths"),
                "price": campaign.get("price"),
                "listing_key": campaign.get("listing_key"),
                "relevance": min(1.0, score),
            })

        matches.sort(key=lambda x: x["relevance"], reverse=True)

        # Store successful match pattern in memory
        if matches:
            self.memory.store_fact(
                f"match_found_{criteria.get('beds_min', '?')}br_{(locations[0] if locations else 'unknown')}",
                "match_pattern",
                confidence=0.9
            )

        return matches[:3]


# ============================================================================
# HERMES LIVE DATABASE - INDEPENDENT FROM PRODUCTION
# ============================================================================

class HermesLiveDatabase:
    """Independent mock database for Hermes fork"""

    def __init__(self, max_campaigns=2000, max_replies=3000):
        self.campaigns = deque(maxlen=max_campaigns)
        self.replies = deque(maxlen=max_replies)
        self.campaign_counter = 0
        self.reply_counter = 0

        self.stats = {
            "campaigns_created": 0,
            "replies_created": 0,
        }

    def refresh(self, new_campaigns=5, new_replies=3):
        """Add new data"""
        for _ in range(new_campaigns):
            self.campaign_counter += 1
            self.stats["campaigns_created"] += 1

            locations = [
                ("Berkeley", 800000), ("Oakland", 700000), ("San Francisco", 1200000),
                ("Palo Alto", 2000000), ("San Mateo", 1100000), ("Fremont", 750000),
            ]
            agents = ["Sarah Chen", "Marcus Johnson", "Lisa Wong", "James Park",
                     "David Brown", "Jennifer Lee", "Robert Martinez", "Angela Davis"]

            city, base_price = random.choice(locations)
            agent = random.choice(agents)
            beds = random.choice([1, 2, 2, 2, 3, 3, 3, 4])
            baths = random.choice([1, 1.5, 2, 2, 2.5, 3])
            price = max(300000, int(base_price + random.randint(-200000, 300000)))

            address = f"{random.randint(100, 9999)} {random.choice(['Oak', 'Elm', 'Maple', 'Cedar'])} St, {city}, CA"

            self.campaigns.append({
                "id": f"camp_{self.campaign_counter:08d}",
                "agent": agent,
                "email": f"{agent.lower().replace(' ', '.')}@realty.com",
                "phone": f"({random.randint(415, 650)}) {random.randint(200, 999)}-{random.randint(1000, 9999)}",
                "address": address,
                "city": city,
                "beds": beds,
                "baths": baths,
                "price": price,
                "timestamp": datetime.now().isoformat(),
            })

        if self.campaigns:
            for _ in range(new_replies):
                self.reply_counter += 1
                self.stats["replies_created"] += 1

                campaign = list(self.campaigns)[random.randint(0, len(list(self.campaigns))-1)]
                is_rejection = random.random() < 0.65

                locations = ["Berkeley", "Oakland", "San Francisco", "Palo Alto", "San Mateo", "Fremont"]

                if is_rejection:
                    alt_city = random.choice([c for c in locations if c != campaign["city"]])
                    alt_beds = random.choice([2, 3, 4])
                    text = f"Not interested, too small. Looking for {alt_beds}br in {alt_city}"
                else:
                    text = random.choice(["Very interested!", "Can we schedule a showing?", "This looks great!"])

                self.replies.append({
                    "id": f"reply_{self.reply_counter:08d}",
                    "campaign_id": campaign["id"],
                    "agent": campaign["agent"],
                    "property": campaign["address"],
                    "city": campaign["city"],
                    "phone": f"+1{random.randint(2015550000, 2015559999)}",
                    "text": text,
                    "is_rejection": is_rejection,
                    "timestamp": datetime.now().isoformat(),
                })


# ============================================================================
# HERMES ORCHESTRATOR - MAIN AGENT LOOP
# ============================================================================

class HermesOrchestrator:
    """Main agentic workflow using Hermes skills"""

    def __init__(self, db, memory):
        self.db = db
        self.memory = memory
        self.skills = HermesSkills(memory)
        self.processed_reply_ids = set()
        self.pairings_queue = deque(maxlen=10000)

        self.workflow_stats = {
            "cycles": 0,
            "replies_analyzed": 0,
            "pairings_generated": 0,
        }

    def run_workflow(self):
        """Execute one cycle through skill pipeline"""
        self.workflow_stats["cycles"] += 1
        new_pairings = 0

        for reply in self.db.replies:
            if reply["id"] in self.processed_reply_ids:
                continue

            self.processed_reply_ids.add(reply["id"])
            self.workflow_stats["replies_analyzed"] += 1

            # SKILL 1: Extract intent
            # Live mode: reply dicts already carry criteria extracted by the
            # production engine's triage (locations/beds_min/price_max). Mock
            # mode: fall back to the regex skill.
            if "beds_min" in reply or "locations" in reply:
                intent = {
                    "is_rejection": reply.get("is_rejection", False),
                    "criteria": {
                        "beds_min": reply.get("beds_min"),
                        "location": reply.get("city"),
                        "locations": reply.get("locations") or [],
                        "price_max": reply.get("price_max"),
                    },
                    "confidence": 0.9 if reply.get("is_rejection") else 0.5,
                }
            else:
                intent = self.skills.extract_real_estate_intent(reply["text"])

            if not intent["is_rejection"] or not intent["criteria"]:
                continue
            if not (intent["criteria"].get("beds_min") or intent["criteria"].get("location")
                    or intent["criteria"].get("locations") or intent["criteria"].get("price_max")):
                continue

            if not reply.get("campaign_id"):
                continue

            original_campaign = None
            for c in self.db.campaigns:
                if c["id"] == reply["campaign_id"] or c.get("listing_key") == reply["campaign_id"]:
                    original_campaign = c
                    break
            if original_campaign is None:
                continue  # the listing they rejected is not in our visible catalog

            # SKILL 2: Normalize criteria
            normalized = self.skills.normalize_buyer_criteria(intent["criteria"], intent["confidence"])

            # SKILL 3: Find alternatives
            alternatives = self.skills.find_cross_agent_properties(
                normalized,
                original_campaign["agent"],
                list(self.db.campaigns)
            )

            if not alternatives:
                continue

            pairing = {
                "reply_id": reply["id"],
                "consumer_phone": reply["phone"],
                "rejected_agent": original_campaign["agent"],
                "rejected_property": original_campaign["address"],
                "buyer_wants": intent["criteria"],
                "best_alternative": {
                    "agent": alternatives[0]["agent"],
                    "email": alternatives[0]["email"],
                    "phone": alternatives[0]["phone"],
                    "address": alternatives[0]["address"],
                    "score": alternatives[0]["relevance"],
                },
                "alternatives_count": len(alternatives),
                "generated_at": datetime.now().isoformat(),
                "ready_to_contact": True,
            }

            self.pairings_queue.append(pairing)
            new_pairings += 1
            self.workflow_stats["pairings_generated"] += 1

            # Record outcome in memory for learning
            self.memory.record_outcome(
                pairing["reply_id"],
                pairing["consumer_phone"],
                pairing["rejected_agent"],
                pairing["best_alternative"]["agent"],
                success=1,
                feedback="pairing_generated"
            )

        return new_pairings


# ============================================================================
# HERMES MAIN EXECUTION
# ============================================================================

def main():
    # ---------------- data source selection ----------------
    source, note = pick_source()
    LIVE = source != "mock"

    print("\n" + "="*100)
    print("HERMES AGENT - REAL ESTATE ORCHESTRATOR (FORK)")
    print("="*100)
    print("Running in parallel to production_mode.py")
    print("Using Hermes skill architecture with holographic memory")
    print(f"\nData source: {source.upper()}  ({note})\n")

    memory = HermesMemory()

    if LIVE:
        try:
            db = YtelLiveDatabase(source)
        except FileNotFoundError as e:
            # standalone mode before first sync: run the sync now, then load
            if source == "sqlite":
                print(f"[sync] {e}")
                ok, msg = maybe_sync_standalone(days_back=180)
                print(f"[sync] {msg}")
                db = YtelLiveDatabase(source)
            else:
                raise
        orchestrator = HermesOrchestrator(db, memory)
        print(f"[Live] Loaded from {os.path.basename(db.db_path)}:")
        print(f"  {len(db.campaigns)} real property campaigns (parsed blast texts)")
        print(f"  {len(db.replies)} real buyer replies (triaged, link-traced)")
        if db.stats.get("sync_error"):
            print(f"  [warn] db read issue: {db.stats['sync_error']}")
        if source == "sqlite":
            # enrichment: fill agent name/phone from Galilaio's read-only records
            built = build_listings_table(db.db_path)
            filled, note = galilaio_enrich_listings(db.db_path)
            print(f"  [enrich] {built} listings built from blasts; Galilaio: {note}")
            db.refresh()
    else:
        db = HermesLiveDatabase(max_campaigns=2000, max_replies=3000)
        orchestrator = HermesOrchestrator(db, memory)
        # Bootstrap
        print("[Bootstrap] Seeding database...")
        for _ in range(200):
            db.refresh(new_campaigns=1, new_replies=0)
        for _ in range(300):
            db.refresh(new_campaigns=0, new_replies=1)
        print(f"Ready: {len(db.campaigns)} campaigns, {len(db.replies)} replies\n")

    print(f"{'Cycle':<8} {'Campaigns':<12} {'Replies':<10} {'New Matches':<15} {'Total Matches':<15} {'Memory Facts':<15} {'Time':<20}")
    print("-" * 100)

    try:
        cycle = 0
        while True:
            cycle += 1

            # Live mode: re-read the sqlite file (a background sync adds rows
            # to it independently). Mock mode: generate new mock data.
            if LIVE:
                db.refresh()
            else:
                db.refresh(new_campaigns=random.randint(5, 10), new_replies=random.randint(8, 16))
            new_matches = orchestrator.run_workflow()
            persist_pairings(list(orchestrator.pairings_queue),
                             {"cycle": cycle, "new_matches": new_matches,
                              "total_matches": orchestrator.workflow_stats["pairings_generated"],
                              "campaigns": len(db.campaigns), "replies": len(db.replies)})

            mem_stats = memory.get_stats()

            print(
                f"{cycle:<8} "
                f"{len(db.campaigns):<12} "
                f"{len(db.replies):<10} "
                f"{new_matches:<15} "
                f"{orchestrator.workflow_stats['pairings_generated']:<15} "
                f"{mem_stats['facts_stored']:<15} "
                f"{datetime.now().strftime('%H:%M:%S'):<20}"
            )

            if cycle % 10 == 0:
                with open(CHECKPOINT, "w") as f:
                    json.dump({
                        "cycle": cycle,
                        "stats": orchestrator.workflow_stats,
                        "memory": mem_stats,
                        "recent_pairings": list(orchestrator.pairings_queue)[-10:],
                    }, f, indent=2)

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n\n[Stopped by user]\n")

    print("="*100)
    print("HERMES ORCHESTRATOR SHUTDOWN")
    print("="*100)

    mem_stats = memory.get_stats()
    print(f"\nWorkflow:")
    print(f"  Cycles: {orchestrator.workflow_stats['cycles']}")
    print(f"  Replies analyzed: {orchestrator.workflow_stats['replies_analyzed']}")
    print(f"  Pairings generated: {orchestrator.workflow_stats['pairings_generated']}")

    print(f"\nMemory (Holographic Learning):")
    print(f"  Facts stored: {mem_stats['facts_stored']}")
    print(f"  Patterns learned: {mem_stats['patterns_learned']}")
    print(f"  Pairings tracked: {mem_stats['pairings_tracked']}")
    print(f"  Success rate: {mem_stats['success_rate']:.1%}")

    print(f"\nDatabase:")
    if LIVE:
        print(f"  Source: {db.db_path}")
        print(f"  Campaigns loaded: {db.stats['campaigns_loaded']}")
        print(f"  Replies loaded: {db.stats['replies_loaded']}")
    else:
        print(f"  Campaigns created: {db.stats['campaigns_created']}")
        print(f"  Replies created: {db.stats['replies_created']}")
    print(f"  Pairings ready: {len(orchestrator.pairings_queue)}")

    with open(RESULTS_JSON, "w") as f:
        json.dump({
            "summary": {
                "cycles": orchestrator.workflow_stats["cycles"],
                "pairings_generated": orchestrator.workflow_stats["pairings_generated"],
                "conversion_rate": orchestrator.workflow_stats["pairings_generated"] / max(1, orchestrator.workflow_stats["replies_analyzed"]) * 100,
                "memory_stats": mem_stats,
            },
            "recent_pairings": list(orchestrator.pairings_queue)[-20:],
        }, f, indent=2)

    print(f"\nResults saved to orchestrator_hermes/")
    print("="*100 + "\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Ytel Live Database adapter for orchestrator_hermes.

Replaces HermesLiveDatabase (mock) with real Ytel SMS data. Reads from a
SQLite database synchronised by ytel_sync (galilaio.db), exposing the same
duck-typed interface the orchestrator already consumes:

    db.refresh()             # bring in new data
    db.campaigns  (list of dicts)
    db.replies    (list of dicts)

Select source with the YTEL_LIVE environment variable:
    "gal" or "galilaio" -> production engine's data/galilaio.db (recommended:
                           full history + listings + triage + reply context,
                           all maintained by the production pairing engine)
    "sqlite"            -> standalone: orchestrator_hermes/memory/hermes_live.db,
                           synced locally via ytel_sync (fresh/empty until first run)
    "mock" / unset      -> old simulated behaviour

Every outbound blast = campaign; every inbound buyer text = reply. Reply-to-
listing linkage and real property specs come from the engine's listings /
reply_context tables when present (gal mode), giving the skill pipeline what
the mock could never have: real beds / baths / price / agent per campaign.
"""

import os
import sqlite3
from datetime import datetime

# ---------------------------------------------------------------------------
# SQL sources
# ---------------------------------------------------------------------------

def _find_engine_dir():
    """Locate the production pairing-engine repo across machines/OSes:
    1. PAIRING_ENGINE_DIR env var (explicit, wins)
    2. Sibling of this repo's parent:    ../alt-pairings-re/pairing-engine
    3. macOS home:                       ~/alt-pairings-re/pairing-engine
    4. Windows home (this PC):           C:\\Users\\<user>\\alt-pairings-re\\...
    Returns the dir containing ytel_sync.py, or None."""
    env = os.environ.get("PAIRING_ENGINE_DIR", "").strip()
    if env and os.path.isfile(os.path.join(env, "ytel_sync.py")):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.abspath(os.path.join(here, "..", "alt-pairings-re", "pairing-engine")),
        os.path.expanduser("~/alt-pairings-re/pairing-engine"),
        os.path.join(os.path.expanduser("~"), "alt-pairings-re", "pairing-engine"),
    ]
    for c in candidates:
        if os.path.isfile(os.path.join(c, "ytel_sync.py")):
            return c
    return None


_ENGINE_DIR = _find_engine_dir()

# Production engine's database: listings already parsed from blast texts,
# replies stitched per conversation, triaged via Ollama, and linked to the
# listing each buyer was answering (reply_context).
GAL_SQLITE = os.path.join(_ENGINE_DIR, "data", "galilaio.db") if _ENGINE_DIR else ""

# Sync + enrichment helpers import from the production engine repo.
YTEL_SYNC_DIR = _ENGINE_DIR or ""

# Standalone database (kept local to this fork, filled by ytel_sync).
HERMES_SQLITE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "orchestrator_hermes", "memory", "hermes_live.db"
)

# ---------------------------------------------------------------------------
# Regex helpers for the fallback path (no triage tables -> parse raw text)
# ---------------------------------------------------------------------------

import re

_BEDS = re.compile(r"(\d+(?:\.\d)?)\s*-?\s*(?:bd|br|bed|beds|bedroom|bedrooms|bdrm|bdrms)\b", re.I)
_BATHS = re.compile(r"(\d+(?:\.\d+)?)\s*-?\s*(?:ba|bth|bths|bath|baths|bathroom|bathrooms)\b", re.I)
_BD_BA = re.compile(r"\b(\d)\s*/\s*(\d(?:\.\d)?)\b(?!\s*(?:pm|am|\d))")
_SQFT = re.compile(r"(\d[\d,]{2,6})\s*(?:sq\.?\s?ft|sqft|sf|sq\.?\s?feet|square\s*f(?:ee|oo)t)\b", re.I)
_PRICE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k|m|mm|mil|million)?\b", re.I)
_ADDRESS = re.compile(
    r"\b(\d{1,6}(?:-\d{1,6})?\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9'.-]+\s+){0,4}?"
    r"(?:St|Street|Ave|Avenue|Dr|Drive|Ct|Court|Way|Cir|Circle|Pl|Place|Rd|Road|Ln|Lane|"
    r"Blvd|Boulevard|Ter|Terrace|Pkwy|Parkway|Hwy|Highway|Loop|Trl|Trail|Sq|Square|Row|Walk|"
    r"Pt|Point|Cv|Cove|Xing|Crossing|Run|Path|Aly|Alley|Plz|Plaza)\b\.?"
    r"(?:\s*(?:#|Unit|Apt|Ste)\.?[\w-]+)?)", re.I)

# Marketing junk that is NOT a property blast (radius-text ads, stop confirms...)
_BLAST_NOISE = re.compile(r"\$150|radius text|stop list|digitally door knock|"
                          r"\$150 per 1000", re.I)

# Junk replies that carry no buyer intent.
_JUNK_REPLY = re.compile(r"^\s*(stop|help|unsubscribe|start|yes|no|ok|okay|thanks?|"
                         r"okay thank|\W*)\s*\.?\s*$", re.I)

# Words that follow an address but are never a city (day names, open-house boilerplate...)
CITY_STOP = {"link", "open", "sat", "sun", "mon", "tue", "wed", "thu", "fri", "sunday", "saturday", "friday",
             "today", "tomorrow", "beautiful", "stunning", "nicely", "updated", "new", "just", "price", "will",
             "is", "has", "with", "for", "ct", "dr", "st", "ave", "thanks", "what", "the", "a", "an", "in",
             "don't", "check", "come", "join", "click", "call", "reply", "gorgeous", "charming", "spacious",
             "have", "hope", "had", "we", "i", "it", "this", "our", "my", "you", "your", "let", "please", "see",
             "hi", "hello", "looking", "listed", "priced", "coming", "soon", "sold", "pending", "offers",
             "link", "pics", "info", "stop", "thx"}


def _to_price(num, suffix):
    n = float(num.replace(",", ""))
    return int(n * {"k": 1_000, "m": 1_000_000, "mm": 1_000_000,
                    "mil": 1_000_000, "million": 1_000_000}.get((suffix or "").lower(), 1))


def _street_key(address):
    """Join key for the same property across messages (number + first street word)."""
    m = re.match(r"\s*(\d+)\s+(?:[NSEW]\.?\s+)?([A-Za-z0-9']+)", address or "")
    return f"{m.group(1)} {m.group(2).lower()}" if m else None


def _parse_blast(body):
    """Extract a property from a real-estate blast text. Mirrors the
    pairing engine's parse_blast() closely enough for fallback use."""
    text = re.sub(r"https?://\S+", "", body or "")
    m = _ADDRESS.search(text)
    if not m or _BLAST_NOISE.search(text):
        return None
    beds = _BEDS.search(text)
    baths = _BATHS.search(text)
    bdba = _BD_BA.search(text)
    if not beds and not bdba:
        return None
    price = None
    for pm in _PRICE.finditer(text):
        before = text[max(0, pm.start() - 25):pm.start()].lower()
        if re.search(r"credit|reduc|off|drop|improv|grant|down|over|under", before):
            continue
        price = _to_price(*pm.groups())
        if price:
            break
    return {
        "address": re.sub(r"\s+", " ", m.group(1)).strip(" ,."),
        "beds": float(beds.group(1)) if beds else float(bdba.group(1)),
        "baths": float(baths.group(1)) if baths else (float(bdba.group(2)) if bdba else None),
        "sqft": int(_SQFT.group(1).replace(",", "")) if (_SQFT.search(text)) else None,
        "price": price,
    }


def _extract_city(text):
    """City after the street address; junk-filtered with CITY_STOP so day
    names and open-house words don't masquerade as a city."""
    mm = _ADDRESS.search(text or "")
    if not mm:
        return None
    after = text[mm.end():]
    # try up to 3 candidate words, skipping stop words (day names etc.)
    cm = re.match(
        r"\s*(?:,|\bin\b|-)?\s*((?:[A-Z][A-Za-z.'-]+(?:\s+)?){1,3})", after)
    if not cm:
        return None
    for word in cm.group(1).split():
        if word.lower().strip(".,") in CITY_STOP or not word[0].isupper():
            continue
        # strip trailing state codes
        city = re.sub(r"\s+(?:CA|NV|OR|WA|AZ|TX|FL)$", "", word).strip(" ,.")
        if city and city.lower() not in CITY_STOP and len(city) > 2:
            return city
        break
    return None


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class YtelLiveDatabase:
    """Duck-type compatible with HermesLiveDatabase. Read-only on the
    synchronised SQLite file; optionally triggers ytel_sync."""

    def __init__(self, source="mock", max_campaigns=200000, max_replies=300000):
        self.source = source
        if source == "gal":
            self.db_path = GAL_SQLITE
        elif source == "sqlite":
            self.db_path = HERMES_SQLITE
        else:
            raise ValueError(f"YtelLiveDatabase source must be 'gal' or 'sqlite', got {source!r}")

        self.max_campaigns = max_campaigns
        self.max_replies = max_replies

        self._campaigns = []      # outbound blasts
        self._replies = []        # inbound buyer texts
        self._load()

        self.stats = {
            "source": self.db_path,
            "campaigns_loaded": len(self._campaigns),
            "replies_loaded": len(self._replies),
            "sync_error": None,
        }

    # -- internal loading ---------------------------------------------------

    def _connect(self):
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(
                f"database not synced yet: {self.db_path} "
                f"(standalone mode fills it via maybe_sync_standalone / ytel_sync)")
        from pathlib import Path
        uri_path = Path(self.db_path).resolve().as_posix()
        db = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _load(self):
        db = self._connect()
        has_listings = bool(db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='listings'").fetchone())
        has_replies = bool(db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='replies'").fetchone())

        self._load_campaigns(db, has_listings)
        self._load_replies(db, has_replies)
        db.close()

    def _load_campaigns(self, db, has_listings):
        """Outbound blasts -> campaign dicts. When the listings table exists
        (gal mode) use parsed specs; all agents are fair game - the match
        skill excludes the rejected agent per pairing, not globally."""
        self._campaigns = []
        if has_listings:
            rows = db.execute("""SELECT listing_key, address, city, beds, baths, sqft,
                                        price, agent_name, agent_phone, last_ms
                                 FROM listings WHERE kind='for_sale' AND beds IS NOT NULL
                                 AND city IS NOT NULL""").fetchall()
            for r in rows:
                self._campaigns.append({
                    "id": r["listing_key"],
                    "listing_key": r["listing_key"],
                    "agent": r["agent_name"] or "Name not on file",
                    "email": None,
                    "phone": r["agent_phone"],
                    "address": r["address"],
                    "city": r["city"],
                    "beds": int(r["beds"]) if r["beds"] else None,
                    "baths": float(r["baths"]) if r["baths"] is not None else None,
                    "sqft": r["sqft"],
                    "price": r["price"],
                    "last_blast_ms": r["last_ms"],
                })
        else:
            self._load_campaigns_raw(db)

    def _load_campaigns_raw(self, db):
        """No listings table (standalone mode): parse real-estate blasts
        directly from the messages table, deduped on street_key. Agent
        name/phone are not in Ytel blast texts, so they stay on file as
        'Name not on file' - enrich later or switch to gal mode."""
        rows = db.execute("""SELECT body, date_ms FROM messages
                             WHERE direction='outbound'
                             AND body NOT LIKE 'Galilaio Lead Alert%'
                             ORDER BY date_ms""").fetchall()
        dedup = {}
        for r in rows:
            p = _parse_blast(r["body"])
            if not p:
                continue
            key = _street_key(p["address"])
            if not key:
                continue
            if key in dedup:
                dedup[key]["sends"] += 1
                continue
            city = _extract_city(r["body"])
            dedup[key] = {
                "id": key,
                "listing_key": key,
                "agent": "Name not on file",
                "email": None,
                "phone": None,
                "address": p["address"],
                "city": city,
                "beds": int(p["beds"]) if p["beds"] else None,
                "baths": p["baths"],
                "sqft": p["sqft"],
                "price": p["price"],
                "first_blast_ms": r["date_ms"],
                "sends": 1,
            }
        # listings without a city can still match region-less criteria, but
        # when a buyer names a city the matcher needs cities - keep all, the
        # match skill skips cityless listings only when a city was named.
        self._campaigns = list(dedup.values())

    def _load_replies(self, db, has_replies):
        """Buyer replies. gal mode: stitched replies + triage category + the
        listing they were answering (reply_context). sqlite fallback: raw
        inbound messages parsed with the same regex approach."""
        self._replies = []
        triage_by_id = {}
        ctx_by_id = {}
        if has_replies:
            triage_tbl = db.execute("SELECT 1 FROM sqlite_master WHERE name='reply_triage'").fetchone()
            ctx_tbl = db.execute("SELECT 1 FROM sqlite_master WHERE name='reply_context'").fetchone()
            if triage_tbl:
                for r in db.execute("""SELECT t.reply_id, t.category, t.criteria
                                       FROM reply_triage t"""):
                    triage_by_id[r["reply_id"]] = r
            if ctx_tbl:
                for r in db.execute("SELECT reply_id, listing_key FROM reply_context"):
                    ctx_by_id[r["reply_id"]] = r["listing_key"]

            rows = db.execute("""SELECT r.reply_id, r.customer, r.date_ms, r.text
                                 FROM replies r WHERE r.prefilter='keep'""").fetchall()
            for r in rows:
                t = triage_by_id.get(r["reply_id"])
                if t and t["category"] not in ("buyer_criteria", "interested"):
                    continue  # "stop", junk, agent chatter
                crit = {}
                if t and t["criteria"]:
                    try:
                        crit = __import__("json").loads(t["criteria"])
                    except Exception:
                        crit = {}
                is_rejection = bool(crit) or (t and t["category"] == "interested")
                listing_key = ctx_by_id.get(r["reply_id"])
                if is_rejection and not listing_key:
                    continue  # can't responsibly pair without knowing what they saw
                self._replies.append({
                    "id": r["reply_id"],
                    "campaign_id": listing_key,
                    "customer": r["customer"],
                    "phone": r["customer"],
                    "text": r["text"],
                    "city": (crit.get("locations") or [None])[0],
                    "beds_min": crit.get("beds_min"),
                    "price_max": crit.get("price_max"),
                    "locations": crit.get("locations") or [],
                    "is_rejection": is_rejection,
                    "timestamp": datetime.fromtimestamp(r["date_ms"] / 1000).isoformat()
                                 if r["date_ms"] else None,
                })
        else:
            self._load_replies_raw(db)

    def _load_replies_raw(self, db):
        """No stitched-replies table (standalone sqlite mode): parse raw
        inbound messages, try to trace each buyer's preceding blast."""
        rows = db.execute("""SELECT m.sms_sid, m.from_num, m.date_ms, m.body
                             FROM messages m WHERE m.direction='inbound'
                             ORDER BY m.from_num, m.date_ms""").fetchall()
        # index each buyer's inbound history for simple stitching
        grouped = {}
        for r in rows:
            grouped.setdefault(r["from_num"], []).append(r)
        for customer, msgs in grouped.items():
            # one reply dict per group of consecutive messages (gap < 60s)
            cur, parts = [], []
            def flush():
                if cur:
                    text = "".join(parts).strip()
                    if text and not _JUNK_REPLY.match(text):
                        last = cur[-1]
                        listing = self._trace_blast(db, customer, last["date_ms"])
                        beds = _BEDS.search(text)
                        self._replies.append({
                            "id": last["sms_sid"],
                            "campaign_id": listing,
                            "customer": customer,
                            "phone": customer,
                            "text": text,
                            "city": _extract_city(text),
                            "beds_min": int(float(beds.group(1))) if beds else None,
                            "price_max": None,
                            "locations": ([_extract_city(text)] if _extract_city(text) else []),
                            "is_rejection": bool(beds or _extract_city(text)),
                            "timestamp": datetime.fromtimestamp(last["date_ms"] / 1000).isoformat()
                                         if last["date_ms"] else None,
                        })
            for r in msgs:
                if cur and r["date_ms"] - cur[-1]["date_ms"] > 60_000:
                    flush(); cur, parts = [], []
                seg = (r["body"] or "").strip()
                if seg and seg not in parts:
                    parts.append(seg)
                cur.append(r)
            flush()

    def _trace_blast(self, db, customer, date_ms):
        """Which listing was this buyer answering? Look back through the
        blasts sent to them (mirrors the engine's _thread_listing)."""
        msgs = db.execute("""SELECT body FROM messages WHERE direction='outbound'
                             AND to_num=? AND date_ms<=? AND date_ms>=?
                             AND body NOT LIKE 'Galilaio Lead Alert%'
                             ORDER BY date_ms DESC LIMIT 12""",
                          (customer, date_ms + 60_000, date_ms - 400 * 86_400_000)).fetchall()
        for m in msgs:
            p = _parse_blast(m["body"])
            if p:
                return _street_key(p["address"])
        return None

    # -- public interface (same shape HermesLiveDatabase had) ----------------

    def refresh(self, **kw):
        """Reload from disk (cheap reads). If standalone mode and ytel_sync
        exists, offer a live pull via sync() - but never crash the loop."""
        # If the file changed (new sync happened outside), reload everything.
        try:
            db = self._connect()
            db.execute("SELECT MAX(rowid) FROM messages").fetchone()
            db.close()
        except Exception as e:
            self.stats["sync_error"] = str(e)
        self._load()
        self.stats["campaigns_loaded"] = len(self._campaigns)
        self.stats["replies_loaded"] = len(self._replies)

    @property
    def campaigns(self):
        return self._campaigns

    @property
    def replies(self):
        return self._replies


def maybe_sync_standalone(days_back=2, quiet=False):
    """Standalone mode: pull new Ytel SMS into hermes_live.db via ytel_sync.
    Returns (ok, message). Never raises - the orchestrator keeps running.
    ytel_sync hardcodes its own DB path, so we point it at hermes_live.db
    without editing the production engine's copy."""
    sync_file = os.path.join(YTEL_SYNC_DIR, "ytel_sync.py")
    if not os.path.isfile(sync_file):
        return False, f"ytel_sync.py not found in {YTEL_SYNC_DIR}"
    try:
        import sys, importlib.util, sqlite3
        spec = importlib.util.spec_from_file_location("ytel_sync_standalone", sync_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # runs only defs; sync() is under __main__
        os.makedirs(os.path.dirname(HERMES_SQLITE), exist_ok=True)
        # patch module-level DB_PATH to our standalone file, then create schema
        mod.DB_PATH = HERMES_SQLITE
        os.environ.setdefault("YTEL_TOKEN_PATH", "")  # ytel_sync reads env or its token file
        _create_schema(HERMES_SQLITE)
        mod.sync(days_back)
        return True, f"synced {days_back} day(s) into {os.path.basename(HERMES_SQLITE)}"
    except SystemExit:
        return False, "ytel_sync exited early (argparse) - run it manually for backfill"
    except Exception as e:
        return False, f"ytel_sync failed: {e}"


def _create_schema(path):
    """Same schema ytel_sync.connect() creates (kept in sync manually)."""
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            sms_sid     TEXT PRIMARY KEY,
            date_ms     INTEGER,
            ymd         TEXT,
            direction   TEXT,
            from_num    TEXT,
            to_num      TEXT,
            body        TEXT,
            contact_id  TEXT,
            campaign_id TEXT,
            client_id   TEXT,
            raw         TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_msg_contact ON messages(contact_id);
        CREATE INDEX IF NOT EXISTS ix_msg_campaign ON messages(campaign_id);
        CREATE INDEX IF NOT EXISTS ix_msg_dir_date ON messages(direction, date_ms);
        CREATE TABLE IF NOT EXISTS sliced_days (
            ymd TEXT PRIMARY KEY, complete INTEGER, records INTEGER, synced_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_days (
            direction TEXT,
            ymd       TEXT,
            count     INTEGER,
            truncated INTEGER,
            synced_at TEXT,
            PRIMARY KEY (direction, ymd)
        );
    """)
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Galilaio enrichment + listing build (standalone sqlite mode)
# ---------------------------------------------------------------------------

PE_DIR = YTEL_SYNC_DIR


def _open_engine_modules():
    """Import the production pairing engine's helpers read-only (never
    edits that repo). Returns (pe, ge) or (None, None) if unavailable.
    Chdir is scoped to the import only - the caller's cwd is restored so
    the orchestrator's relative paths keep working."""
    import sys, importlib
    orig_cwd = os.getcwd()
    try:
        if PE_DIR not in sys.path:
            sys.path.insert(0, PE_DIR)
        os.chdir(PE_DIR)  # pairing_engine resolves data paths relative to its own file
        pe = importlib.import_module("pairing_engine")
        ge = importlib.import_module("galilaio_enrich")
        return pe, ge
    except Exception:
        return None, None
    finally:
        os.chdir(orig_cwd)


def build_listings_table(db_path):
    """Standalone mode: build the engine-compatible `listings` table from
    raw blast texts (regex parse), so Galilaio enrichment and the match
    skill both have structured listings to work with. Incremental."""
    pe, _ = _open_engine_modules()
    if pe is None:
        return 0
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS listings (
                listing_key TEXT PRIMARY KEY, address TEXT, city TEXT, state TEXT,
                beds REAL, baths REAL, sqft INTEGER, price INTEGER, kind TEXT,
                single_story INTEGER, agent_name TEXT, agent_phone TEXT, headline TEXT,
                body TEXT, first_ms INTEGER, last_ms INTEGER, sends INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS engine_state
                  (key TEXT PRIMARY KEY, value TEXT)""")
    db.commit()

    last = db.execute("SELECT value FROM engine_state WHERE key='listings_rowid'").fetchone()
    last = int(last[0]) if last else 0
    top_row = db.execute("SELECT MAX(rowid) FROM messages").fetchone()[0] or 0
    if top_row <= last:
        db.close()
        return 0

    newest = {}
    for r in db.execute("""SELECT date_ms, body FROM messages
                           WHERE rowid > ? AND rowid <= ? AND direction='outbound'
                           AND body NOT LIKE 'Galilaio Lead Alert%'""", (last, top_row)):
        p = pe.parse_blast(r["body"])
        if not p:
            continue
        key = pe.street_key(p["address"])
        if not key:
            continue
        cur = newest.get(key)
        if cur is None or r["date_ms"] < cur["first_ms"]:
            base = dict(cur) if cur else {}
            base.update({
                "listing_key": key, "address": p["address"], "city": p["city"], "state": p["state"],
                "beds": p["beds"], "baths": p["baths"], "sqft": p["sqft"], "price": p["price"],
                "kind": p["kind"], "single_story": p.get("single_story") or 0,
                "agent_name": None, "agent_phone": None, "headline": p["headline"], "body": p["body"],
                "first_ms": r["date_ms"], "last_ms": r["date_ms"], "sends": 1,
            })
            newest[key] = base
        elif cur:
            cur["sends"] += 1
            cur["last_ms"] = max(cur["last_ms"], r["date_ms"])

    db.executemany("""INSERT INTO listings VALUES (:listing_key,:address,:city,:state,:beds,:baths,:sqft,:price,
                      :kind,:single_story,:agent_name,:agent_phone,:headline,:body,:first_ms,:last_ms,:sends)
                      ON CONFLICT(listing_key) DO UPDATE SET sends=sends+excluded.sends,
                      last_ms=MAX(last_ms, excluded.last_ms)""", list(newest.values()))
    db.execute("INSERT OR REPLACE INTO engine_state VALUES ('listings_rowid', ?)", (top_row,))
    db.commit()
    n = len(newest)
    db.close()
    return n


def galilaio_enrich_listings(db_path):
    """Standalone mode: fill agent names/phones/emails from Galilaio's
    read-only records (campaign catalog + orders). Read-only API calls -
    never registers clicks or updates contacts. Returns (listings_filled,
    note)."""
    pe, ge = _open_engine_modules()
    if ge is None:
        return 0, "galilaio_enrich/pairing_engine not importable"
    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        before = db.execute("SELECT COUNT(*) FROM listings WHERE agent_phone IS NOT NULL").fetchone()[0]
        ge.enrich_listings(db)
        after = db.execute("SELECT COUNT(*) FROM listings WHERE agent_phone IS NOT NULL").fetchone()[0]
        total = db.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        db.close()
        return after - before, f"agent phone on {after}/{total} listings"
    except Exception as e:
        return 0, f"enrichment failed (non-fatal): {e}"


def pick_source(env_var="YTEL_LIVE"):
    """Decide the data source. Returns (source, note)."""
    raw = (os.environ.get(env_var) or "").strip().lower()
    if raw in ("gal", "galilaio"):
        return "gal", "production engine's data/galilaio.db (full synced Ytel history)"
    if raw == "sqlite":
        return "sqlite", "standalone hermes_live.db (run ytel_sync to fill)"
    if raw == "mock":
        return "mock", "simulated data (old behaviour)"
    return "mock", "YTEL_LIVE not set - defaulting to mock; set YTEL_LIVE=gal for real data"
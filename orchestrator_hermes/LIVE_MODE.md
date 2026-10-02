Live-DB switch for orchestrator_hermes
======================================

The Hermes fork runs on real Ytel SMS data. One environment variable
controls everything (set before starting the run):

    YTEL_LIVE=gal     ->  live production data  (RECOMMENDED)
    YTEL_LIVE=sqlite  ->  standalone sync (pulls fresh from api.ytel.com)
    YTEL_LIVE=mock    ->  old simulated behaviour
    (unset)           ->  same as mock, prints a hint

Run:
  git-bash:     YTEL_LIVE=gal python orchestrator_hermes_main.py
  PowerShell:   $env:YTEL_LIVE="gal"; python orchestrator_hermes_main.py

WHY "gal" IS RECOMMENDED
------------------------
The production pairing engine (C:\Users\mattix\alt-pairings-re\pairing-engine)
already syncs ALL of Ytel's SMS history (13 months, ~1.6M outbound + 46.6K
inbound) into its SQLite db (data/galilaio.db) and has solved the hard
parts on top of it:

  - listings table: 3209 real property blasts parsed to
    beds/baths/sqft/price/agent_name/agent_phone (1196 active for-sale
    listings with beds + city)
  - replies table: 40082 buyer conversations stitched (8121 pass junk filter)
  - reply_triage: LLM-classified buyer criteria (locations, beds, budget...)
  - reply_context: which listing each buyer was answering (211 traced)

The adapter reads these tables READ-ONLY (sqlite mode=ro; cannot touch
the production engine's data). The Windows copy dated 2026-09-29
produced 37 verified pairings in cycle 1 of a test run with real agent
names and phones.

The db on this PC is a snapshot - the Mac's live engine has fresher
data. To refresh this machine: copy the Mac's data/galilaio.db over it,
or use standalone mode below.

The production-engine location is discovered automatically:
  1. PAIRING_ENGINE_DIR env var (explicit, wins)
  2. ../alt-pairings-re/pairing-engine (sibling of this repo's parent)
  3. ~/alt-pairings-re/pairing-engine (macOS home)
Set PAIRING_ENGINE_DIR if the repo lives somewhere else.

MAC STUDIO SETUP
----------------
Copy this whole folder to the Mac, then from inside it:
    bash setup_mac.sh
That installs a .venv, finds the pairing-engine repo (expected at
~/alt-pairings-re/pairing-engine on the Mac - it's already there),
runs a read-only smoke test, and installs a launchd agent
(com.hermes.orchestrator, KeepAlive) that runs run_mac.py: orchestrator
thread + dashboard at http://127.0.0.1:8787, logging to
orchestrator_hermes/dashboard/orchestrator_run.log.

Mac control commands:
  foreground:   YTEL_LIVE=gal ./.venv/bin/python orchestrator_hermes_main.py
  stop agent:   launchctl bootout gui/$(id -u)/com.hermes.orchestrator
  restart:      launchctl kickstart -k gui/$(id -u)/com.hermes.orchestrator
  (kickstart historically failed on the engine's Mac; pkill -f run_mac.py
   works - KeepAlive relaunches it)

What transfers vs what stays:
  TRANSFER: orchestrator_hermes_main.py, ytel_live_db.py, run_mac.py,
            dashboard_server.py, setup_mac.sh, orchestrator_hermes/
            (LIVE_MODE.md, skills/, memory/, dashboard/results.db - the
            last only if you want the 37 Windows-verified pairings)
  DO NOT TRANSFER: nothing from the production pairing-engine - the Mac
            already has it, live and current. Do not copy Windows paths,
            and do not let this fork write there (it opens that db
            read-only only).
  Secrets: the fork needs no Ytel token for gal mode (reads the synced
            db). Standalone sqlite mode reads pairing-engine/ytel_token.txt
            on whatever machine it runs - already on the Mac.

Data freshness on the Mac: gal mode reads the Mac engine's live
galilaio.db, which its own launchd sync keeps current (runs every 15 min) -

STANDALONE MODE ("sqlite") - fully independent, works today
-----------------------------------------------------------
Uses the production repo's ytel_sync.py as a library (DB path patched
in memory; the production copy is NOT modified) to write its own db:
    orchestrator_hermes/memory/hermes_live.db

On first run (or whenever the db is missing) it syncs the last 180 days
from api.ytel.com, authenticated with the YTEL_TOKEN env var or
pairing-engine/ytel_token.txt. Verified live 2026-10-02: pulled 3117
real messages (3032 outbound / 85 inbound) in a 1-day test sync.

Known limits of standalone mode (all measured, none speculative):
  - Listings are parsed from raw blast texts (same regex rules as the
    engine's parse_blast), deduped on street_key. One day of data
    yielded 1 real listing - property blasts are batched over weeks,
    so a full 180-day backfill is needed for volume (first run does
    this automatically).
  - Agent name/phone are NOT recoverable from Ytel blast texts. The
    pairing names the listing; agent fields say "Name not on file"
    (no made-up data). Use gal mode for agent-enriched listings, or
    wire galilaio_enrich in later.
  - Replies: parsed from raw inbound texts; only replies naming a city
    or bed count become criteria (no LLM triage in this mode).

MOCK MODE
---------
Unchanged HermesLiveDatabase. Same orchestrator code path - useful for
regression-testing the skills without touching real data.

HOW TO SWITCH TO A DIFFERENT DB LATER
-------------------------------------
The adapter is duck-typed against the old mock: any source only has to
provide .refresh() + .campaigns + .replies with the same dict keys
(campaign: id/agent/email/phone/address/city/beds/baths/price;
 reply: id/campaign_id/phone/text/is_rejection, plus optional
 beds_min/locations/price_max for pre-triaged replies). Point
 GAL_SQLITE / HERMES_SQLITE at a new file or add a third source in
 ytel_live_db.pick_source() - main() needs no changes.

WHAT THE DATA GOES THROUGH (Ytel API quirks, all documented)
------------------------------------------------------------
Ytel ignores page/offset and caps every response at 5000 rows (all
forms tested; see HANDOFF.md in the pairing-engine repo). ytel_sync
works around it with date windows (14-day inbound, 1-day outbound) +
filtered re-queries of capped days, dedup on smsSid, resume-safe.
Replies are linked to the listing the buyer answered by walking the
buyer's SMS history for the preceding blast (the engine's
_thread_listing logic). Ytel keeps ~13 months of logs; older history
exists only in Galilaio.

FILES
-----
orchestrator_hermes_main.py            the orchestrator, source selected by YTEL_LIVE
ytel_live_db.py                        the live-db adapter + Galilaio enrichment
run_mac.py                             Mac launcher (orchestrator thread + dashboard)
dashboard_server.py                    results dashboard (http://127.0.0.1:8787)
setup_mac.sh                           Mac Studio setup (venv + launchd agent)
orchestrator_hermes/LIVE_MODE.md       this file
orchestrator_hermes/dashboard/results.db   pairings the dashboard reads
orchestrator_hermes/memory/hermes_live.db  standalone sync target
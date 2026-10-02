#!/bin/bash
# Hermes orchestrator - Mac Studio setup
# Run from the repo root:  bash setup_mac.sh
# Safe to re-run; nothing is destroyed. Does NOT touch the production
# pairing-engine repo or its database / running launchd agent.

set -e
cd "$(dirname "$0")"

echo "==> 1. Python 3 + venv"
if ! command -v python3 >/dev/null; then
  echo "python3 not found - install Xcode command line tools: xcode-select --install"
  exit 1
fi
python3 -m venv .venv 2>/dev/null || true
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet requests
echo "    $(./.venv/bin/python --version) with requests installed"

echo "==> 2. Locate production pairing-engine (for sync + enrichment + gal data)"
PE=""
for c in "$HOME/alt-pairings-re/pairing-engine" "$PAIRED/../pairing-engine" ./pairing-engine; do
  [ -f "$c/ytel_sync.py" ] && PE="$c" && break
done
if [ -z "$PE" ]; then
  echo "  !! pairing-engine not found next to this repo (~/alt-pairings-re/pairing-engine)."
  echo "     The engine dir can be set explicitly later with:  export PAIRING_ENGINE_DIR=/path/to/pairing-engine"
  echo "     YTEL_LIVE=gal will fail until it exists; YTEL_LIVE=sqlite still works (syncs fresh from Ytel)."
else
  echo "    found: $PE"
  echo "    its data/galilaio.db is what YTEL_LIVE=gal reads (read-only)."
fi

echo "==> 3. Smoke test (gal snapshot read)"
if [ -n "$PE" ]; then
  YTEL_LIVE=gal ./.venv/bin/python -c "
import ytel_live_db as y
db = y.YtelLiveDatabase('gal')
print(f'    OK: {len(db.campaigns)} listings, {len(db.replies)} buyer replies from galilaio.db')"
else
  echo "    skipped (no engine dir yet)"
fi

echo "==> 4. launchd agent (runs orchestrator + dashboard continuously)"
PLIST=~/Library/LaunchAgents/com.hermes.orchestrator.plist
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.hermes.orchestrator</string>
  <key>ProgramArguments</key><array>
    <string>$(pwd)/.venv/bin/python</string>
    <string>-u</string>
    <string>$(pwd)/run_mac.py</string>
  </array>
  <key>WorkingDirectory</key><string>$(pwd)</string>
  <key>EnvironmentVariables</key><dict>
    <key>YTEL_LIVE</key><string>gal</string>
  </dict>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$(pwd)/orchestrator_hermes/dashboard/orchestrator_run.log</string>
  <key>StandardErrorPath</key><string>$(pwd)/orchestrator_hermes/dashboard/orchestrator_run.log</string>
</dict></plist>
EOF
# stop a previous instance first (by label - the HANDOFF-approved way), then load
launchctl bootout gui/$(id -u)/com.hermes.orchestrator 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$PLIST"
echo "    installed $PLIST (KeepAlive, starts at login)"

echo "==> 5. Dashboard"
echo "    served by the same agent at http://127.0.0.1:8787"

echo
echo "Done. Manual commands:"
echo "  foreground run:  YTEL_LIVE=gal ./.venv/bin/python orchestrator_hermes_main.py"
echo "  dashboard only:  ./.venv/bin/python dashboard_server.py"
echo "  stop agent:      launchctl bootout gui/\$(id -u)/com.hermes.orchestrator"
echo "  restart agent:   launchctl kickstart -k gui/\$(id -u)/com.hermes.orchestrator  (or pkill -f run_mac.py)"
echo "  logs:            orchestrator_hermes/dashboard/orchestrator_run.log"
#!/bin/bash
# Run JobPrep in the background at login (macOS LaunchAgent). Undo with scripts/uninstall_service.sh.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.jobprep.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Something is already listening on port 8000 (probably JobPrep started in a terminal). Stop it first (Ctrl-C)." >&2
  exit 1
fi
mkdir -p "$DIR/data" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/uvicorn</string><string>backend.api:app</string>
    <string>--host</string><string>127.0.0.1</string><string>--port</string><string>8000</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/data/server.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/server.log</string>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "JobPrep is running at http://127.0.0.1:8000 and will start at every login. Logs: data/server.log"

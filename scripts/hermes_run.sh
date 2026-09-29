#!/usr/bin/env bash
# Usage: scripts/hermes_run.sh "prompt" [session_id]  -> prints tool events + final output
set -euo pipefail
K=$(grep '^API_SERVER_KEY=' ~/.hermes/.env | cut -d= -f2)
BODY=$(/opt/homebrew/bin/python3 -c 'import json,sys; print(json.dumps({"input": sys.argv[1], "session_id": sys.argv[2]}))' "$1" "${2:-jarvis-cli-$RANDOM}")
RID=$(curl -s -X POST localhost:8642/v1/runs -H "Authorization: Bearer $K" -H "Content-Type: application/json" -d "$BODY" \
  | /opt/homebrew/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
curl -s -N -m "${TIMEOUT:-240}" "localhost:8642/v1/runs/$RID/events" -H "Authorization: Bearer $K" \
  | /opt/homebrew/bin/python3 -u -c '
import json, sys
for line in sys.stdin:
    if not line.startswith("data: "): continue
    e = json.loads(line[6:]); ev = e.get("event")
    if ev == "tool.started": print("TOOL>", e.get("tool"), (e.get("preview") or "")[:120])
    elif ev == "tool.completed": print("TOOL<", e.get("tool"), e.get("duration"), "err" if e.get("error") else "ok", (e.get("preview") or "")[:160].replace("\n"," "))
    elif ev == "approval.request": print("APPROVAL?", json.dumps(e)[:300])
    elif ev in ("run.completed", "run.failed", "run.cancelled"): print("FINAL", ev, "::", e.get("output") or e.get("error")); break
'

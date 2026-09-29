# J.A.R.V.I.S.

An Iron Man style voice + holographic desktop assistant for macOS, built as a front end on the local
[Hermes agent](https://hermes-agent.nousresearch.com/docs), so it inherits every Hermes tool, skill and memory.

- **app/**: Electron + React + Three.js HUD (arc reactor, holographic cards, briefing panel, maps).
  Menu-bar icon, global hotkey (⌥⇧Space), launch at login.
- **core/jarvis_core/**: Python "JARVIS Core" (FastAPI + WebSocket). Wake word ("Hey Jarvis", openWakeWord),
  speech via VoiceStudio (STT + TTS), streaming turns through the Hermes `/v1/runs` API, cards, confirmations,
  and the AI-Inbox-style briefing (Gemini structured triage across both inboxes).
- **core/jarvis_google/**: MCP server giving Hermes Gmail / Calendar / Drive / Docs / Sheets / Contacts on
  two Google accounts. Reads run immediately; send / delete / share / invites are only *proposed* and need an
  explicit AUTHORIZE (voice or click). Deletes go to Trash. Every action is written to an audit log.
  Also: weather (Open-Meteo), Google Places (restaurant/place cards with photos, hours, reviews), turn-by-turn
  directions with live traffic (Google Routes), and Pylon support tickets with Linear linking.
- **Cards**: email, calendar, weather, place / places, route, Pylon ticket list + ticket. Pylon changes (status,
  team, assignee, snooze, note, reply, Linear create/link) are click-only on the card, never exposed to the model.
- **scripts/**: `google_auth.py` (OAuth link / verify), `hermes_run.sh` (CLI probe of the Hermes runs API).

## Run

```bash
# Core
cd core && uv venv && uv pip install -e ".[dev]" && .venv/bin/python -m pytest -q
.venv/bin/python -m jarvis_core.main            # :8765

# Google accounts (OAuth client JSON at ~/.hermes/jarvis/google/client_secret.json)
core/.venv/bin/python scripts/google_auth.py link personal you@gmail.com
core/.venv/bin/python scripts/google_auth.py verify

# Register the MCP server with Hermes
hermes mcp add jarvis_google --command "$PWD/core/.venv/bin/python" --args -m jarvis_google.server

# App
cd app && npm install && npm run build && npx electron .
```

Requires a running Hermes gateway with the API server enabled (`API_SERVER_ENABLED=true`) and VoiceStudio on
`localhost:3900`. No credentials are stored in this repo: OAuth tokens live in `~/.hermes/jarvis/google/`, API
keys are read from `~/.hermes/.env`:

| Variable | Used for |
| --- | --- |
| `GEMINI_API_KEY` | briefing triage, Pylon reply / Linear drafts |
| `GOOGLE_MAPS_API_KEY` | Places API (New) + Routes API (restrict the key to those two) |
| `PYLON_API_KEY`, `JARVIS_PYLON_USER_ID` | Pylon tickets; the Pylon user JARVIS acts as |
| `LINEAR_API_KEY` | creating / linking Linear issues from a ticket |

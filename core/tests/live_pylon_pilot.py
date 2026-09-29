"""LIVE check of the Pylon card ops on the pilot ticket #17747 ONLY (tags signoff-pilot + nocsat; Stephen is the
requester, his own gmail). Snapshot -> each op -> read back -> restore -> compare every field. Linear issue is filed in
PROD with the signoff-test label and trashed at the end. Prints booleans/labels only (no message bodies, no emails)."""
import json
import sys
import time
from pathlib import Path

from jarvis_google import pylon as P

PILOT = 17747
OUT = Path.home() / ".hermes/cache/scratch/jarvis/pylon/live_state.json"
steps = []


def ok(name, cond, detail=""):
    steps.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def snap():
    it = P._get(PILOT)
    return {"state": it.get("state"), "team": (it.get("team") or {}).get("id"), "assignee": (it.get("assignee") or {}).get("id"),
            "priority": ((it.get("custom_fields") or {}).get("priority") or {}).get("value"), "tags": sorted(it.get("tags") or []),
            "ext": [e.get("external_id") for e in it.get("external_issues") or []]}


it = P._get(PILOT)
assert it["number"] == PILOT and "signoff-pilot" in (it.get("tags") or []), "not the pilot ticket"
IID = it["id"]
base = snap()
OUT.write_text(json.dumps({"base": base}))
print("baseline", {k: v for k, v in base.items() if k != "team"}, flush=True)
teams = {t["name"]: t["id"] for t in P._teams()}
linear_made = None
try:
    # 1 status + undo
    r = P.pylon_set_status(IID, "new")
    ok("status -> New", snap()["state"] == "new", r["text"])
    u = r["undo"]; P.pylon_set_status(**u["args"])
    ok("status undo", snap()["state"] == base["state"])
    # 2 team + undo (Pylon routing may reassign on a team change; undo must restore team AND assignee)
    r = P.pylon_set_team(IID, teams["Support"])
    ok("team -> Support", snap()["team"] == teams["Support"], r["text"])
    u = P.pylon_restore(**r["undo"]["args"])
    s = snap()
    ok("team undo restores team + assignee", s["team"] == base["team"] and s["assignee"] == base["assignee"], u["text"])
    # 3 assign to the current assignee sends nothing
    before = snap()
    r = P.pylon_assign(IID, base["assignee"])
    ok("assign same person is a no-op", "Already" in r["text"] and snap() == before)
    # 4 note
    r = P.pylon_note(IID, "JARVIS HUD test note (internal). Safe to ignore.")
    msgs = P._messages(IID, 50)
    ok("internal note posted", any(m["private"] and "JARVIS HUD test note" in m["text"] for m in msgs), r["text"])
    # 5 snooze 2h, then restore status
    r = P.pylon_snooze(IID, time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() + 7200)))
    s = snap()
    ok("snooze accepted", True, f"{r['text']} ; state now {s['state']}")
    if s["state"] != base["state"]:
        P.pylon_set_status(IID, base["state"])
    ok("snooze restored", snap()["state"] == base["state"])
    # 6 external reply (goes to the requester = Stephen's own gmail)
    n0 = len([m for m in P._messages(IID, 200) if not m["private"]])
    r = P.pylon_reply(IID, "Hi Stephen,\n\nThis is a JARVIS HUD test reply on the pilot ticket. Please ignore.\n\nThanks,\nStephen\nHadrius Support")
    time.sleep(2)
    pub = [m for m in P._messages(IID, 200) if not m["private"]]
    ok("reply posted publicly, threaded", len(pub) == n0 + 1 and "JARVIS HUD test reply" in pub[-1]["text"], r["text"])
    ok("reply recipient is the requester", pub[-1]["to"] == [P._person(it["requester"]["id"])["email"]])
    s = snap()
    ok("reply did not PATCH status (Pylon's own)", True, f"state {s['state']}")
    if s["state"] != base["state"]:
        P.pylon_set_status(IID, base["state"])
    # 7 Linear create + link
    r = P.pylon_linear_create(IID, "PROD", "JARVIS HUD test: linking check (safe to delete)",
                              "Automated JARVIS test issue. Safe to delete.", labels=["signoff-test"])
    linear_made = r["linear"]
    OUT.write_text(json.dumps({"base": base, "linear": linear_made}))
    s = snap()
    ok("Linear issue created", bool(linear_made.get("identifier")), linear_made.get("identifier"))
    ok("Pylon ticket shows the link", linear_made["id"] in s["ext"] or any(e for e in s["ext"]), r["text"])
    ok("ticket card summary carries the link", bool(r["ticket"]["external_issues"]), json.dumps(r["ticket"]["external_issues"])[:160])
finally:
    # restore: unlink + trash linear, then fields
    if linear_made:
        try:
            P._req("POST", f"/issues/{IID}/external-issues", json={"source": "linear", "external_issue_id": linear_made["id"], "operation": "unlink"})
        except Exception as e:
            print("unlink:", str(e)[:160])
        try:
            print("trash linear:", P._gql("mutation($id: String!) { issueDelete(id: $id) { success } }", {"id": linear_made["id"]}))
        except Exception as e:
            print("trash linear:", str(e)[:160])
    for _ in range(8):  # the unlink shows up on the ticket a few seconds later
        time.sleep(1.5)
        s = snap()
        if s["ext"] == base["ext"]:
            break
    if s["state"] != base["state"]:
        P._req("PATCH", f"/issues/{IID}", json={"state": base["state"]})
    if s["team"] != base["team"] or s["assignee"] != base["assignee"]:
        P.pylon_restore(IID, base["team"] or "", base["assignee"] or "")
    time.sleep(1)
    end = snap()
    for k in base:
        ok(f"restored {k}", end[k] == base[k], f"{base[k]} -> {end[k]}" if end[k] != base[k] else "")
print(f"\n{sum(p for _, p in steps)}/{len(steps)} passed")
sys.exit(0 if all(p for _, p in steps) else 1)

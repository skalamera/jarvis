"""Click every briefing action on the synthetic [JARVIS TEST] item through the real UI, verifying Gmail state after each.
Only ever touches the message whose id is in ~/.hermes/cache/scratch/jarvis/test_msg_id."""
import json, pathlib, subprocess, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from jarvis_google.accounts import service

MID = pathlib.Path.home().joinpath(".hermes/cache/scratch/jarvis/test_msg_id").read_text().strip()
SHOTS = pathlib.Path.home() / ".hermes/cache/scratch/jarvis"
gm = service("gmail", "personal")


def cdp(*a):
    return subprocess.run([sys.executable, "tests/cdp.py", *a], capture_output=True, text=True).stdout.strip()


def ev(js):
    out = cdp("eval", js)
    try:
        return json.loads(out)
    except ValueError:
        return out


def labels():
    return sorted(gm.users().messages().get(userId="me", id=MID, format="minimal").execute().get("labelIds", []))


ROW = "[...document.querySelectorAll('.brief-row')].find(r => r.innerText.includes('Dana'))"


def click(label, confirm_twice=False):
    js = f"(() => {{ const r = {ROW}; if (!r) return 'NO ROW'; const b = [...r.querySelectorAll('button')].find(b => b.innerText.toLowerCase().includes({json.dumps(label.lower())})); if (!b) return 'NO BTN'; b.click(); return 'ok'; }})()"
    res = ev(js)
    if confirm_twice:
        time.sleep(0.4)
        res2 = ev(js.replace(json.dumps(label.lower()), json.dumps("confirm delete")))
        res = f"{res}/{res2}"
    return res


def undo():
    return ev("(() => { const b = [...document.querySelectorAll('.toast button')].find(b => b.innerText === 'UNDO'); if (!b) return 'NO UNDO'; b.click(); return 'ok'; })()")


def toast():
    return ev("[...document.querySelectorAll('.toast span')].map(s => s.innerText)")


def row_present():
    return ev(f"!!({ROW})")


def step(name, fn, wait=2.5):
    r = fn()
    time.sleep(wait)
    print(f"{name:34} click={r!s:10} toast={toast()} row={row_present()} labels={labels()}")


print("start".ljust(34), "row=", row_present(), "labels=", labels())
step("DONE (archive)", lambda: click("Done"))
step("  undo", undo)
step("READ (mark read)", lambda: click("Read"))
step("  undo", undo)
step("DELETE first click (arm only)", lambda: click("Delete"), 1.0)
step("DELETE confirmed (trash)", lambda: click("Confirm delete"))
step("  undo (untrash)", undo)
step("DISMISS (Gmail untouched)", lambda: click("✕"))
step("  undo", undo)

# reply: open composer, check prefill, save as draft
print("REPLY open:", click("Reply"))
time.sleep(0.6)
print("  prefill:", json.dumps(ev(f"({ROW}).querySelector('textarea')?.value"))[:300])
cdp("shot", str(SHOTS / "ui_brief_reply.png"))
before = {d["id"] for d in gm.users().drafts().list(userId="me", maxResults=50).execute().get("drafts", [])}
print("  save draft:", click("Save draft"))
time.sleep(3)
after = gm.users().drafts().list(userId="me", maxResults=50).execute().get("drafts", [])
new = [d for d in after if d["id"] not in before]
for d in new:
    full = gm.users().drafts().get(userId="me", id=d["id"], format="metadata",
                                   metadataHeaders=["To", "Subject"]).execute()
    h = {x["name"]: x["value"] for x in full["message"]["payload"]["headers"]}
    print("  new draft:", d["id"], "thread match:", full["message"]["threadId"] == MID, h)
    gm.users().drafts().delete(userId="me", id=d["id"]).execute()
    print("  (test draft deleted)")
print("  toast:", toast(), "row still there:", row_present())

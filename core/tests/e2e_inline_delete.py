"""E2E: inline two-click delete on a DISPLAYS email card -> trashed in Gmail,
card removed, UNDO restores to inbox + card. Only touches a synthetic message this script inserts itself."""
import base64, json, subprocess, sys, time
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_google.accounts import account_email, service  # noqa: E402

CDP = [sys.executable, str(Path(__file__).with_name("cdp.py"))]
SHOTS = Path.home() / ".hermes/cache/scratch/jarvis"


def ev(js: str):
    out = subprocess.run(CDP + ["eval", js], capture_output=True, text=True, timeout=60).stdout.strip().splitlines()
    try:
        return json.loads(out[-1])
    except Exception:
        return out[-1] if out else None


def shot(name: str):
    subprocess.run(CDP + ["shot", str(SHOTS / name)], capture_output=True, timeout=60)
    return SHOTS / name


def labels(gm, mid):
    return sorted(gm.users().messages().get(userId="me", id=mid, format="minimal").execute().get("labelIds", []))


def wait(js: str, timeout=20, every=0.5):
    t = time.time()
    while time.time() - t < timeout:
        v = ev(js)
        if v:
            return v
        time.sleep(every)
    return None


gm = service("gmail", "personal")
m = EmailMessage()
m["From"] = "JARVIS Test <jarvis-test@example.com>"
m["To"] = account_email("personal")
m["Subject"] = "[JARVIS TEST] inline delete check"
m.set_content("Synthetic message for the inline-delete test. Safe to delete.")
raw = base64.urlsafe_b64encode(m.as_bytes()).decode()
mid = gm.users().messages().insert(userId="me", body={"raw": raw, "labelIds": ["INBOX", "UNREAD"]}).execute()["id"]
(SHOTS / "test_msg_id").write_text(mid)
print("inserted", mid, labels(gm, mid))
ok = True
try:
    # 1) open the email card on DISPLAYS, then inline-delete it
    ev("(()=>{useStore.getState().set({rightTab:'displays', cards: []}); return true})()")
    ev(f"(()=>{{window.__core.direct('gmail_read', {{account:'personal', message_id:'{mid}'}}); return true}})()")
    got = wait(f"useStore.getState().cards.some(c=>c.kind==='email' && c.data?.id==='{mid}')", 20)
    print("2. email card shown:", bool(got))
    ok &= bool(got)
    btn = "[...document.querySelectorAll('.kind-email .card-actions button')]"
    print("   click 1:", ev(f"(()=>{{const b={btn}.find(b=>b.innerText.trim().toLowerCase()==='trash'); b?.click(); return !!b}})()"))
    time.sleep(0.5)
    armed = ev(f"{btn}.map(b=>b.innerText.trim())")
    print("   armed buttons:", armed)
    shot("ui_del_armed.png")
    print("   gmail after 1 click (should be unchanged):", labels(gm, mid))
    ok &= "TRASH" not in labels(gm, mid)
    ok &= ev("useStore.getState().cards.filter(c=>c.kind==='confirm').length") == 0
    print("   click 2:", ev(f"(()=>{{const b={btn}.find(b=>b.innerText.trim().toLowerCase()==='confirm delete'); b?.click(); return !!b}})()"))
    gone = wait(f"!useStore.getState().cards.some(c=>c.kind==='email' && c.data?.id==='{mid}')", 15)
    time.sleep(1)
    lab = labels(gm, mid)
    print("3. card removed:", bool(gone), "| gmail:", lab, "| AUTHORIZE cards:", ev("useStore.getState().cards.filter(c=>c.kind==='confirm').length"))
    ok &= bool(gone) and "TRASH" in lab
    print("   toast:", ev("useStore.getState().toasts.map(t=>t.text + (t.onUndo?' [UNDO]':''))"))
    shot("ui_del_done.png")

    # 3) UNDO -> back in inbox, card back
    print("4. undo:", ev("(()=>{const b=[...document.querySelectorAll('.toast button')].find(b=>b.innerText==='UNDO'); b?.click(); return !!b})()"))
    back = wait(f"useStore.getState().cards.some(c=>c.kind==='email' && c.data?.id==='{mid}')", 10)
    time.sleep(2.5)
    lab = labels(gm, mid)
    print("   card back:", bool(back), "| gmail:", lab)
    ok &= bool(back) and "TRASH" not in lab and "INBOX" in lab

    # 4) cancel path: arm then 'Keep' -> nothing happens
    ev(f"(()=>{{const b={btn}.find(b=>b.innerText.trim().toLowerCase()==='trash'); b?.click(); return true}})()")
    time.sleep(0.4)
    print("5. keep:", ev(f"(()=>{{const b={btn}.find(b=>b.innerText.trim().toLowerCase()==='keep'); b?.click(); return !!b}})()"),
          ev(f"{btn}.map(b=>b.innerText.trim())"), labels(gm, mid))
finally:
    gm.users().messages().trash(userId="me", id=mid).execute()
    print("cleanup: test message trashed")
print("PASS" if ok else "FAIL")

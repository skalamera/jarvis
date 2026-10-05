"""HoloTube handoff: video over the orb -> minimize / expand; the sidebar player must keep PLAYING, unmuted,
and be the only player. Usage: e2e_holotube.py  (expects one video card already on screen, or asks for one)"""
import json
import subprocess
import sys
import time

PY = sys.executable
S = "/Users/stephenskalamera/.hermes/cache/scratch"


def ev(js: str):
    out = subprocess.run([PY, "tests/cdp.py", "eval", js], capture_output=True, text=True).stdout.strip()
    try:
        v = json.loads(out)
        return json.loads(v) if isinstance(v, str) and v[:1] in "[{" else v
    except Exception:
        return out


STATE = """JSON.stringify([...jarvisMedia.players.values()].map(p=>({id:p.id,playing:p.playing(),
  ...(p.info?{t:Math.round(p.info().time),muted:p.info().muted,vol:p.info().volume,st:p.info().state}:{})})).concat([{active:jarvisMedia.activeId}]))"""


def to_center():
    ev("""(()=>{const s=useStore.getState();const c=s.cards.find(x=>x.kind==='video');
      s.set({cards:s.cards.map(x=>x===c?{...x,openMax:false,data:{...x.data,stage:'center',current:0}}:x)});
      setTimeout(()=>window.dispatchEvent(new CustomEvent('jarvis:tube',{detail:{cardId:c.id,results:c.data.results,idx:0}})),50);return 1})()""")
    time.sleep(12)


def check(label: str):
    a = ev(STATE)
    time.sleep(4)
    b = ev(STATE)
    print(label, "\n  t0:", a, "\n  t+4s:", b)


if not ev("useStore.getState().cards.some(c=>c.kind==='video')"):
    subprocess.Popen([PY, "tests/cdp.py", "ask", "play Iron Man 2 welcome home sir on youtube", f"{S}/htx", "40"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        time.sleep(1)
        if ev("document.querySelectorAll('.ht-frame').length") == 1:
            break
    time.sleep(10)
else:
    to_center()
check("CENTER")
ev("document.querySelector('.ht-bar button[title^=Minimize]').click(),true")
time.sleep(8)
check("MINIMIZED")
to_center()
ev("document.querySelector('.ht-bar button[title=Expand]').click(),true")
time.sleep(8)
check("EXPANDED")
print("xl:", ev("document.querySelectorAll('.holo-xl').length"), "center frames:", ev("document.querySelectorAll('.ht-frame').length"))

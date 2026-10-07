"""Live: launcher -> Trips -> type a request -> wait for 3 itineraries -> screenshots (XL tabs + sidebar)."""
import json, subprocess, sys, time
import os
HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "..", ".venv", "bin", "python")
CDP = os.path.join(HERE, "cdp.py")
OUT = os.path.join(os.environ.get("TMPDIR", "/tmp"), "")
ARGS = sys.argv[1:]
SHOTS_ONLY = "--shots" in ARGS  # plan already on screen: just walk the tabs (planning alone can take ~3 min)
REQ = next((a for a in ARGS if not a.startswith("--")), "Long weekend in Charleston SC Oct 22-25 for 2, foodie, things by the water, no hiking")


def ev(js):
    r = subprocess.run([PY, CDP, "eval", js], capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def shot(name):
    subprocess.run([PY, CDP, "shot", OUT + name], capture_output=True, timeout=60)
    print("SHOT", OUT + name)


s = ''
if not SHOTS_ONLY:
    ev("useStore.getState().set({cards:[]}), true")
    ev("document.querySelector('.launch-btn').click(), true"); time.sleep(1.2)
    print("trips icon:", ev("(()=>{const b=[...document.querySelectorAll('.launch-app')].find(x=>x.textContent.includes('Trips'));if(!b)return 'missing';b.click();return 'clicked '+document.querySelectorAll('.launch-app').length})()"))
    time.sleep(1.5)
    print("empty:", ev("!!document.querySelector('.tp-empty') + ' xl=' + !!document.querySelector('.holo-xl.kind-trip')"))
    shot("trip-empty.png")
    js = ("(()=>{const t=document.querySelector('.tp-comp.big textarea');const s=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;"
          f"s.call(t,{json.dumps(REQ)});t.dispatchEvent(new Event('input',{{bubbles:true}}));return 'typed'}})()")
    print(ev(js)); time.sleep(0.4)
    print(ev("document.querySelector('.tp-go').click(), 'go'"))
    time.sleep(12); shot("trip-forge.png")
    t0 = time.time()
    while time.time() - t0 < 420:
        s = ev("(()=>{const c=useStore.getState().cards.find(c=>c.kind==='trip');return c?(c.data.status||'')+'|'+(c.data.itineraries||[]).length+'|'+(c.data.error||''):'none'})()")
        if s.startswith("ready") or "error" in s:
            break
        time.sleep(5)
    print("state:", s, "after", round(time.time() - t0), "s")
ev("(()=>{const h=document.querySelector('.kind-trip');if(h&&!h.classList.contains('holo-xl'))h.querySelector('.tp-open')?.click();return 1})()")
time.sleep(3); shot("trip-xl-A.png")
print(ev("document.querySelector('.tp-main').scrollTop=900, 'scrolled'")); time.sleep(1); shot("trip-xl-A2.png")
for tab in ("Overview", "Hotels", "Events"):
    ev(f"[...document.querySelectorAll('.tp-tabs button')].find(b=>b.textContent.startsWith({json.dumps(tab)})).click(), true"); time.sleep(1.5)
    shot(f"trip-xl-{tab}.png")
ev("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})), true"); time.sleep(1.2)
print("side:", ev("!!document.querySelector('.tp-side') + ' xl=' + !!document.querySelector('.holo-xl')"))
shot("trip-side.png")

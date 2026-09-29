"""Drive the running Electron app over Chrome DevTools Protocol (launch with --remote-debugging-port=9229).

  python tests/cdp.py shot out.png
  python tests/cdp.py eval "js expression"
  python tests/cdp.py ask "question" out_prefix [wait_s]   -> sends text, screenshots while it runs
"""
import asyncio
import base64
import json
import sys
import time
import urllib.request

import websockets

PORT = 9229


def target() -> str:
    pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
    for p in pages:
        if p.get("type") == "page" and "devtools" not in p.get("url", ""):
            return p["webSocketDebuggerUrl"]
    raise SystemExit(f"no page target: {pages}")


class CDP:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            m = json.loads(await self.ws.recv())
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})

    async def eval(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value", r.get("exceptionDetails"))

    async def shot(self, path):
        r = await self.call("Page.captureScreenshot", format="png")
        open(path, "wb").write(base64.b64decode(r["data"]))
        print("screenshot", path)


async def main():
    cmd = sys.argv[1]
    async with websockets.connect(target(), max_size=64 * 2**20) as ws:
        c = CDP(ws)
        if cmd == "shot":
            await c.shot(sys.argv[2])
        elif cmd == "eval":
            print(json.dumps(await c.eval(sys.argv[2]), indent=1, default=str)[:4000])
        elif cmd == "ask":
            q, prefix = sys.argv[2], sys.argv[3]
            wait = float(sys.argv[4]) if len(sys.argv) > 4 else 60
            await c.eval("window.__cards0 = 0")
            await c.eval(f"""(() => {{ const i = document.querySelector('.composer input');
              const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
              set.call(i, {json.dumps(q)}); i.dispatchEvent(new Event('input', {{bubbles: true}}));
              i.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}})); return true; }})()""")
            t0, shots, done_at = time.time(), 0, None
            state_js = "document.querySelector('.state-label')?.textContent"
            while time.time() - t0 < wait:
                await asyncio.sleep(1.0)
                st = await c.eval(state_js)
                el = round(time.time() - t0, 1)
                if shots == 0 and el > 4:
                    await c.shot(f"{prefix}_working.png")
                    shots += 1
                final = await c.eval("[...document.querySelectorAll('.msg-jarvis')].pop()?.querySelector('.md') ? true : false")
                print(f"t={el}s state={st} final_text={final}", flush=True)
                if final and st in ("STANDING BY",):
                    done_at = done_at or time.time()
                    if time.time() - done_at > 1.5:
                        break
            await c.shot(f"{prefix}_final.png")
            print(json.dumps(await c.eval("""({
              reply: [...document.querySelectorAll('.msg-jarvis .md')].pop()?.innerText,
              cards: [...document.querySelectorAll('.holo')].map(h => h.querySelector('.holo-kind')?.textContent + ' | ' + h.querySelector('.holo-title')?.textContent),
              tools: [...document.querySelectorAll('.act')].map(a => a.innerText.replace(/\\n/g,' ')),
            })"""), indent=1))


asyncio.run(main())

"""Hover a finance chart with real CDP mouse events and report the price readout before / during / after.

  python tests/e2e_chart_hover.py [.kind-stock | .kind-crypto]
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
SEL = sys.argv[1] if len(sys.argv) > 1 else ".kind-stock"
sys.argv = ["cdp.py", "noop"]
import cdp  # noqa: E402

import websockets  # noqa: E402


async def main(sel: str) -> None:
    async with websockets.connect(cdp.target(), max_size=None) as ws:
        c = cdp.CDP(ws)
        read = f"""JSON.stringify({{price: document.querySelector('{sel} .fx-price')?.textContent,
            delta: document.querySelector('{sel} .fx-quote .fx-delta')?.textContent,
            when: document.querySelector('{sel} .fx-quote .fx-when')?.textContent}})"""
        box = json.loads(await c.eval(f"""(()=>{{const r=document.querySelector('{sel} .fx-chart').getBoundingClientRect();
            return JSON.stringify({{x:r.left, y:r.top, w:r.width, h:r.height}})}})()"""))
        print("before  ", await c.eval(read))
        for frac in (0.2, 0.5, 0.8):
            x, y = box["x"] + box["w"] * frac, box["y"] + box["h"] * 0.5
            await c.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            await asyncio.sleep(0.35)
            print(f"hover {int(frac * 100):>2}%", await c.eval(read))
        await c.call("Input.dispatchMouseEvent", type="mouseMoved", x=box["x"] + box["w"] * 0.5, y=box["y"] - 60)
        await asyncio.sleep(0.35)
        print("after   ", await c.eval(read))


asyncio.run(main(SEL))

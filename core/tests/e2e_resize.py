"""Drag a side-panel resize handle (argv[1]: right|left, default right) with real CDP mouse events; report widths before/after, persistence, reset."""
import asyncio
import json
import sys

SIDE = sys.argv[1] if len(sys.argv) > 1 else "right"
SGN = 1 if SIDE == "right" else -1  # moving the pointer left widens the right panel, narrows the left one
KEY = f"jarvis.{SIDE}Width"
MIN = 340 if SIDE == "right" else 220
H = f"(()=>{{const r=document.querySelector('.panel-resizer.pr-{SIDE}').getBoundingClientRect(); return JSON.stringify([r.left+r.width/2, r.top+r.height/2])}})()"
sys.argv = ["cdp.py", "noop"]
import cdp  # noqa: E402

import websockets  # noqa: E402


async def main():
    async with websockets.connect(cdp.target(), max_size=None) as ws:
        c = cdp.CDP(ws) if hasattr(cdp, "CDP") else None
        if c is None:
            cls = next(v for v in vars(cdp).values() if isinstance(v, type) and hasattr(v, "eval"))
            c = cls(ws)

        async def ev(e):
            return await c.eval(e)

        width = f"(()=>Math.round(document.querySelector('aside.{SIDE}').getBoundingClientRect().width))()"
        center_w = "(()=>Math.round(document.querySelector('main.center').getBoundingClientRect().width))()"
        print("start", await ev(width), "center", await ev(center_w))
        h = json.loads(await ev(H))
        x, y = h

        async def drag(to_x):
            await c.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            await c.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
            for i in range(1, 11):
                await c.call("Input.dispatchMouseEvent", type="mouseMoved", x=x + (to_x - x) * i / 10, y=y, button="left", buttons=1)
            await c.call("Input.dispatchMouseEvent", type="mouseReleased", x=to_x, y=y, button="left", clickCount=1)
            await asyncio.sleep(0.3)

        await drag(x - 300 * SGN)  # wider
        print("after drag wider", await ev(width), "center", await ev(center_w), "saved", await ev("localStorage.getItem('" + KEY + "')"))
        await ev("location.reload(), true")
        await asyncio.sleep(5)
        print("after reload (persisted)", await ev(width))
        h = json.loads(await ev(H))
        x, y = h
        await drag(x + 2000 * SGN)  # try to go past the minimum
        print(f"drag way narrower (clamped at min {MIN})", await ev(width))
        h = json.loads(await ev(H))
        x, y = h
        await drag(x - 5000 * SGN)  # try to go past the max
        print("drag way wider (clamped so center keeps >= 520)", await ev(width), "center", await ev(center_w))
        h = json.loads(await ev(H))
        await c.call("Input.dispatchMouseEvent", type="mousePressed", x=h[0], y=h[1], button="left", clickCount=1)
        await c.call("Input.dispatchMouseEvent", type="mouseReleased", x=h[0], y=h[1], button="left", clickCount=1)
        await c.call("Input.dispatchMouseEvent", type="mousePressed", x=h[0], y=h[1], button="left", clickCount=2)
        await c.call("Input.dispatchMouseEvent", type="mouseReleased", x=h[0], y=h[1], button="left", clickCount=2)
        await asyncio.sleep(0.3)
        print("double-click reset", await ev(width), "saved", await ev("localStorage.getItem('" + KEY + "')"))
        print("stuck resizing class?", await ev("document.body.classList.contains('resizing')"))


asyncio.run(main())

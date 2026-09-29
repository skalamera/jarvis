"""Drag the right-panel resize handle with real CDP mouse events; report widths before/after, persistence, reset."""
import asyncio
import json
import sys

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

        width = "(()=>Math.round(document.querySelector('aside.right').getBoundingClientRect().width))()"
        center_w = "(()=>Math.round(document.querySelector('main.center').getBoundingClientRect().width))()"
        print("start", await ev(width), "center", await ev(center_w))
        h = json.loads(await ev("(()=>{const r=document.querySelector('.panel-resizer').getBoundingClientRect(); return JSON.stringify([r.left+r.width/2, r.top+r.height/2])})()"))
        x, y = h

        async def drag(to_x):
            await c.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            await c.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
            for i in range(1, 11):
                await c.call("Input.dispatchMouseEvent", type="mouseMoved", x=x + (to_x - x) * i / 10, y=y, button="left", buttons=1)
            await c.call("Input.dispatchMouseEvent", type="mouseReleased", x=to_x, y=y, button="left", clickCount=1)
            await asyncio.sleep(0.3)

        await drag(x - 300)  # wider
        print("after drag wider", await ev(width), "center", await ev(center_w), "saved", await ev("localStorage.getItem('jarvis.rightWidth')"))
        await ev("location.reload(), true")
        await asyncio.sleep(5)
        print("after reload (persisted)", await ev(width))
        h = json.loads(await ev("(()=>{const r=document.querySelector('.panel-resizer').getBoundingClientRect(); return JSON.stringify([r.left+r.width/2, r.top+r.height/2])})()"))
        x, y = h
        await drag(x + 2000)  # try to go past the minimum
        print("drag way narrower (clamped at min 340)", await ev(width))
        h = json.loads(await ev("(()=>{const r=document.querySelector('.panel-resizer').getBoundingClientRect(); return JSON.stringify([r.left+r.width/2, r.top+r.height/2])})()"))
        x, y = h
        await drag(x - 5000)  # try to go past the max
        print("drag way wider (clamped so center keeps >= 520)", await ev(width), "center", await ev(center_w))
        h = json.loads(await ev("(()=>{const r=document.querySelector('.panel-resizer').getBoundingClientRect(); return JSON.stringify([r.left+r.width/2, r.top+r.height/2])})()"))
        await c.call("Input.dispatchMouseEvent", type="mousePressed", x=h[0], y=h[1], button="left", clickCount=1)
        await c.call("Input.dispatchMouseEvent", type="mouseReleased", x=h[0], y=h[1], button="left", clickCount=1)
        await c.call("Input.dispatchMouseEvent", type="mousePressed", x=h[0], y=h[1], button="left", clickCount=2)
        await c.call("Input.dispatchMouseEvent", type="mouseReleased", x=h[0], y=h[1], button="left", clickCount=2)
        await asyncio.sleep(0.3)
        print("double-click reset", await ev(width), "saved", await ev("localStorage.getItem('jarvis.rightWidth')"))


asyncio.run(main())

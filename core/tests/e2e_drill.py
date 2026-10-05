"""Drill-down check: click a game on the scoreboard (normal and maximized) -> opens in place with Back."""
import asyncio, sys

sys.argv = ["cdp.py", "noop"]
import cdp  # noqa: E402
import websockets  # noqa: E402

S = "/Users/stephenskalamera/.hermes/cache/scratch"
CLICK = """(sel => { const el = document.querySelector(sel); const r = el.getBoundingClientRect();
  const o = { bubbles: true, clientX: r.left + 10, clientY: r.top + 5 };
  el.dispatchEvent(new PointerEvent('pointerdown', o)); el.dispatchEvent(new MouseEvent('click', o)); return true })"""


async def main():
    async with websockets.connect(cdp.target(), max_size=None) as ws:
        cls = next(v for v in vars(cdp).values() if isinstance(v, type) and hasattr(v, "eval"))
        c = cls(ws); ev = c.eval
        state = "JSON.stringify(useStore.getState().cards.slice(0,3).map(x=>x.kind+(x.parent?'<'+x.parent.kind:'')))"
        for maxed in (False, True):
            if maxed:
                await ev(f"{CLICK}('.kind-sports_scoreboard .holo-max')"); await asyncio.sleep(0.6)
            print("before", maxed, await ev(state))
            await ev(f"{CLICK}('.sp-sb-row')"); await asyncio.sleep(1)
            for _ in range(40):
                if 'sports_game<' in (await ev(state)): break
                await asyncio.sleep(0.5)
            print(" after click", await ev(state), "xl:", await ev("!!document.querySelector('.holo-xl .holo-back')"),
                  "xl count:", await ev("document.querySelectorAll('.holo-xl').length"))
            await c.shot(f"{S}/drill_{int(maxed)}.png")
            await ev(f"{CLICK}('.holo-back')"); await asyncio.sleep(1)
            print(" after back", await ev(state), "xl:", await ev("document.querySelectorAll('.holo-xl').length"))
        await ev(f"{CLICK}('.holo-xl .holo-max')")


asyncio.run(main())

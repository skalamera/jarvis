"""Click every launcher icon; record which card opened, whether maximized, and screenshot it."""
import asyncio, json, sys

ARGS = sys.argv[1:]
sys.argv = ["cdp.py", "noop"]
import cdp  # noqa: E402
import websockets  # noqa: E402

S = "/Users/stephenskalamera/.hermes/cache/scratch"
ONLY = sys.argv[1:] if False else None


async def main(only):
    async with websockets.connect(cdp.target(), max_size=None) as ws:
        cls = next(v for v in vars(cdp).values() if isinstance(v, type) and hasattr(v, "eval"))
        c = cls(ws); ev = c.eval
        labels = ["Garage", "Weather", "YouTube", "YT Music", "Markets", "Trading", "Resy", "Maps", "Places", "Directions",
                  "Uber Eats", "Flights", "Sports", "Calendar", "Inbox", "Slack", "Pylon", "Files"]
        for lb in labels:
            if only and lb not in only:
                continue
            await ev("useStore.getState().set({cards: []}), true"); await asyncio.sleep(0.3)
            await ev("document.querySelector('.launch-btn').click(), true"); await asyncio.sleep(0.6)
            await ev(f"[...document.querySelectorAll('.launch-app')].find(b=>b.textContent.includes({json.dumps(lb)})).click(), true")
            got = None
            for _ in range(70):
                await asyncio.sleep(0.5)
                got = json.loads(await ev("JSON.stringify(useStore.getState().cards.map(x=>x.kind))"))
                if got:
                    break
            await asyncio.sleep(2.5)
            xl = await ev("document.querySelectorAll('.holo-xl').length")
            bar = await ev("!!document.querySelector('.holo-xl .cs-bar, .holo-xl .md-search, .holo-xl .dir-form, .holo-xl .cx-bar')")
            inp = await ev("document.querySelector('.composer input').value")
            fn = f"{S}/launch_{lb.replace(' ', '_')}.png"
            await c.shot(fn)
            print(f"{lb:11} cards={got} xl={xl} searchbar={bar} prompt={inp!r}")
            await ev("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})), true")
        await ev("useStore.getState().set({cards: []}), true")


asyncio.run(main(set(ARGS) or None))

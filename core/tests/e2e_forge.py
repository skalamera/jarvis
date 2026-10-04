"""Live HoloForge check: ask for an image, screenshot each phase, then accept or dismiss (argv[1])."""
import asyncio, json, subprocess, sys, time

ARGS = sys.argv[1:]
sys.argv, ACTION = ["cdp.py", "noop"], (ARGS[0] if ARGS else "accept")
import cdp  # noqa: E402
import websockets  # noqa: E402

S = "/Users/stephenskalamera/.hermes/cache/scratch"
PROMPT = ARGS[1] if len(ARGS) > 1 else "generate an image of a red vintage sports car on a neon city street at night"


async def main():
    async with websockets.connect(cdp.target(), max_size=None) as ws:
        cls = next(v for v in vars(cdp).values() if isinstance(v, type) and hasattr(v, "eval"))
        c = cls(ws)
        ev = c.eval
        n0 = await ev("useStore.getState().cards.length")
        await ev(f"""(() => {{ const i = document.querySelector('.composer input');
          const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          set.call(i, {json.dumps(PROMPT)}); i.dispatchEvent(new Event('input', {{bubbles: true}}));
          i.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}})); return true; }})()""")
        seen, t0 = set(), time.time()
        while time.time() - t0 < 420:
            st = await ev("(()=>{const f=document.querySelector('.forge');return f?f.className:''})()")
            ph = (st or "").replace("forge ", "")
            if ph and ph not in seen:
                seen.add(ph)
                await asyncio.sleep(1.2 if ph == "ph-forming" else 0.6)
                await c.shot(f"{S}/forge_{ph}.png")
                print("phase", ph, round(time.time() - t0, 1), "s")
            if ph == "ph-shown":
                break
            await asyncio.sleep(0.25)
        print("cards while in hologram:", await ev("useStore.getState().cards.length"), "(before", n0, ")")
        fg = json.loads(await ev("JSON.stringify(useStore.getState().forge&&{key:useStore.getState().forge.key,art:useStore.getState().forge.result?.data?.artifact?.id})"))
        print("forge", fg)
        if ACTION.startswith("say:"):  # answer the way voice does (same Core path as a transcript)
            await asyncio.sleep(1.5)
            print("forge_pending sent; saying", ACTION[4:])
            await ev(f"""(() => {{ const i = document.querySelector('.composer input');
              const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
              set.call(i, {json.dumps(ACTION[4:])}); i.dispatchEvent(new Event('input', {{bubbles: true}}));
              i.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}})); return true; }})()""")
            await asyncio.sleep(1.2)
        else:
            await ev(f"document.querySelector('.fa.{ACTION}').click(), true")
        await asyncio.sleep(0.35)
        await c.shot(f"{S}/forge_mid.png")
        await asyncio.sleep(1.6)
        print("after", ACTION, "forge:", await ev("!!useStore.getState().forge"),
              "top card:", await ev("JSON.stringify((useStore.getState().cards[0]||{}).kind + ' ' + ((useStore.getState().cards[0]||{}).data?.key||''))"))
        print("ART", (fg or {}).get("art"))


asyncio.run(main())

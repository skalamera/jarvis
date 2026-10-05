"""Live test of the expanded calendar: month/week/day views, toggles, and create -> edit -> delete of a test event."""
import asyncio, json, sys

sys.argv = ["cdp.py", "noop"]
import cdp  # noqa: E402
import websockets  # noqa: E402

S = "/Users/stephenskalamera/.hermes/cache/scratch"


async def main():
    async with websockets.connect(cdp.target(), max_size=None) as ws:
        cls = next(v for v in vars(cdp).values() if isinstance(v, type) and hasattr(v, "eval"))
        c = cls(ws)
        ev = c.eval
        sl = asyncio.sleep
        await ev("""(() => { const s = useStore.getState();
          s.addCard({ id: 'cal-test', kind: 'calendar', title: 'Calendar', account: 'personal', data: { events: [] } }); return true })()""")
        await sl(0.8)
        await ev("document.querySelector('.kind-calendar .holo-max').click(), true")
        await sl(3.5)
        print("month cells", await ev("document.querySelectorAll('.cx-cell').length"),
              "chips", await ev("document.querySelectorAll('.cx-chip').length"),
              "err", await ev("document.querySelector('.cx-err')?.textContent || ''"))
        await c.shot(f"{S}/cal_month.png")
        # toggle work off
        before = await ev("document.querySelectorAll('.cx-chip.cxa-work').length")
        await ev("document.querySelector('.cx-right .cx-tog.cxa-work').click(), true"); await sl(0.4)
        print("work chips", before, "->", await ev("document.querySelectorAll('.cx-chip.cxa-work').length"))
        await ev("document.querySelector('.cx-right .cx-tog.cxa-work').click(), true"); await sl(0.3)
        # week view, previous week
        await ev("[...document.querySelectorAll('.cx-views button')].find(b=>b.textContent==='Week').click(), true"); await sl(2.5)
        print("week title", await ev("document.querySelector('.cx-title').textContent"), "blocks", await ev("document.querySelectorAll('.cx-blk').length"))
        await c.shot(f"{S}/cal_week.png")
        await ev("document.querySelector('.cx-nav .cx-btn').click(), true"); await sl(2.5)
        print("prev week", await ev("document.querySelector('.cx-title').textContent"))
        await ev("[...document.querySelectorAll('.cx-nav .cx-btn')].find(b=>b.textContent==='Today').click(), true"); await sl(2)
        # click a day header -> day view
        await ev("document.querySelectorAll('.cx-tg-day')[1].click(), true"); await sl(2.5)
        print("day view", await ev("document.querySelector('.cx-title').textContent"), await ev("document.querySelector('.cx-views .on').textContent"))
        await c.shot(f"{S}/cal_day.png")
        # create a test event at 10:00 on the shown day by clicking the 10am slot
        await ev("""(() => { const col = document.querySelector('.cx-tg-col'); const r = col.getBoundingClientRect();
          col.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: r.left + 30, clientY: r.top + 10 * 48 + 5 })); return true })()""")
        await sl(0.5)
        await ev("""(() => { const i = document.querySelector('.cx-ed-title');
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i, 'JARVIS calendar test (delete me)');
          i.dispatchEvent(new Event('input', { bubbles: true })); return true })()""")
        await c.shot(f"{S}/cal_editor.png")
        await ev("document.querySelector('.cx-ed .cx-btn.primary').click(), true"); await sl(4)
        blk = "[...document.querySelectorAll('.cx-blk')].find(b=>b.textContent.includes('JARVIS calendar test'))"
        print("created:", await ev(f"!!{blk}"), await ev(f"{blk}?.className"), await ev(f"{blk}?.querySelector('span')?.textContent"))
        # edit: rename + move to work
        await ev(f"{blk}.click(), true"); await sl(0.5)
        await ev("""(() => { const i = document.querySelector('.cx-ed-title');
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i, 'JARVIS calendar test EDITED');
          i.dispatchEvent(new Event('input', { bubbles: true }));
          document.querySelector('.cx-ed-accts .cx-tog.cxa-work').click(); return true })()""")
        await sl(0.3)
        await ev("document.querySelector('.cx-ed .cx-btn.primary').click(), true"); await sl(5)
        blk2 = "[...document.querySelectorAll('.cx-blk')].find(b=>b.textContent.includes('EDITED'))"
        print("edited:", await ev(f"!!{blk2}"), await ev(f"{blk2}?.className"))
        await c.shot(f"{S}/cal_edited.png")
        # delete (two clicks: arm, confirm)
        await ev(f"{blk2}.click(), true"); await sl(0.5)
        await ev("document.querySelector('.cx-ed .cx-btn.danger').click(), true"); await sl(0.2)
        await ev("document.querySelector('.cx-ed .cx-btn.danger').click(), true"); await sl(4)
        print("deleted:", not await ev(f"!!{blk2}"))
        await ev("document.querySelector('.kind-calendar .holo-max').click(), true")


asyncio.run(main())

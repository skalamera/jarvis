"""Local instant answers (time/date) and the per-request reasoning option sent to Hermes."""
import asyncio
import datetime as dt

import pytest

from jarvis_core.hermes_client import HermesClient
from jarvis_core.quick import quick_answer

NOW = dt.datetime(2026, 9, 30, 14, 5)


@pytest.mark.parametrize("q", ["What time is it?", "what's the time", "Jarvis, what time is it", "time please",
                               "What time is it right now, sir?", "Can you tell me what time it is?"])
def test_time_questions(q):
    assert quick_answer(q, "America/New_York", NOW) == "It's 2:05 in the afternoon, sir."


@pytest.mark.parametrize("q", ["What's the date?", "what day is it", "What is today's date, Jarvis?",
                               "what date is it today"])
def test_date_questions(q):
    assert quick_answer(q, "America/New_York", NOW) == "It's Wednesday, September 30, sir."


@pytest.mark.parametrize("q", ["What time is my meeting?", "What time is it in London?", "what's the date of the demo",
                               "What time does the market open?", "time to leave?", "What's on today?"])
def test_other_questions_go_to_the_model(q):
    assert quick_answer(q, "America/New_York", NOW) is None


def test_on_the_hour_and_morning():
    assert quick_answer("what time is it", "UTC", dt.datetime(2026, 1, 1, 9, 0)) == "It's 9 o'clock in the morning, sir."
    assert quick_answer("what time is it", "UTC", dt.datetime(2026, 1, 1, 0, 30)) == "It's 12:30 at night, sir."
    assert quick_answer("what time is it", "UTC", dt.datetime(2026, 1, 1, 2, 15)) == "It's 2:15 at night, sir. Rather late, even by your standards."
    assert quick_answer("what time is it", "UTC", dt.datetime(2026, 1, 1, 5, 0)) == "It's 5 o'clock in the morning, sir."


def test_reasoning_option_in_run_body():
    sent = {}

    class FakeHTTP:
        async def post(self, path, json):
            sent.update(json)

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {"run_id": "r1"}
            return R()

    h = HermesClient("http://x", "k", reasoning="low")
    h._http = FakeHTTP()  # type: ignore[assignment]
    assert asyncio.run(h.start_run("hi", "routing-check-do-not-process", "i")) == "r1"
    assert sent["model_options"] == {"reasoning_effort": "low"}

    h2 = HermesClient("http://x", "k")
    h2._http = FakeHTTP()  # type: ignore[assignment]
    sent.clear()
    asyncio.run(h2.start_run("hi", "routing-check-do-not-process", "i"))
    assert "model_options" not in sent

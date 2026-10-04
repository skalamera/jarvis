import pytest

from jarvis_core.session import classify_confirmation, classify_forge


@pytest.mark.parametrize("t", ["keep it", "Keep it, Jarvis.", "accept", "accept it", "confirm", "yes", "yeah keep it",
                               "Yes.", "okay", "save it", "looks good", "perfect", "I like it", "keep the image please",
                               "sure", "love it"])
def test_keep(t):
    assert classify_forge(t) == "keep"


@pytest.mark.parametrize("t", ["lose it", "Lose it.", "no", "nope", "delete", "delete it", "dismiss", "dismiss it",
                               "discard it", "trash it", "get rid of it", "no, delete it", "scrap that", "nah",
                               "throw it away", "cancel", "reject it", "no thanks", "toss the video"])
def test_lose(t):
    assert classify_forge(t) == "lose"


@pytest.mark.parametrize("t", ["make another one with a blue car", "what's the weather", "keep going with the story",
                               "generate a video of a cat", "no I meant a different style with more neon lights please"])
def test_not_an_answer(t):
    assert classify_forge(t) is None


def test_confirm_classifier_unchanged():
    assert classify_confirmation("yes") == "confirm"
    assert classify_confirmation("cancel") == "cancel"
